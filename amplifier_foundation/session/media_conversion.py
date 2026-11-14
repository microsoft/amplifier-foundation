"""Opt-in canonical-v1 media candidate building, never live transcript activation.

The application owns source selection, original observation policy, exclusive
writer fencing, media admission, checkpoint/apply and recovery retention.
"""

from __future__ import annotations

import base64
import binascii
from copy import deepcopy
import hashlib
import json
import os
import re
import sqlite3
import stat
import tempfile
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Awaitable, BinaryIO, Callable, ContextManager, Iterator, Protocol

from ._media_conversion_parser import CHUNK, Scanner, Span, Unsupported, copy_span

NAMESPACE = "experimental.media.v1"
ADAPTER = "foundation.canonical-media-conversion.v1"
_MARKER = re.compile(r"\[Session image ([0-9a-f]{64})\]\Z")


class MediaCapability(Protocol):
    async def ingest(self, value, *, source_format, origin, policy): ...
    async def publish(self, refs, *, origin, disposition): ...


@dataclass(frozen=True)
class SourceIdentity:
    path: str
    dev: int
    ino: int
    size: int
    mtime_ns: int
    ctime_ns: int
    sha256: str


@dataclass(frozen=True)
class ConversionPlan:
    adapter: str
    owner: str
    session_id: str
    source: SourceIdentity
    backups: tuple[SourceIdentity, ...]
    records: int
    inline_images: int
    existing_refs: int
    selected_records: int


@dataclass(frozen=True)
class Candidate:
    directory: str
    sha256: str
    bytes: int
    records: int
    images: int
    original_bytes: int
    decoded_bytes: int
    inline_removed_bytes: int = 0
    state: str = "verified-candidate-not-applied"


Fence = Callable[[str], ContextManager[None]]
Origin = Callable[[int, int], dict[str, Any]]
AssetReader = Callable[[dict[str, Any]], ContextManager[BinaryIO]]


def _json(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def _digest(stream: BinaryIO) -> tuple[str, int]:
    digest, size = hashlib.sha256(), 0
    while chunk := stream.read(CHUNK):
        digest.update(chunk)
        size += len(chunk)
    return digest.hexdigest(), size


@contextmanager
def _open(path: Path) -> Iterator[BinaryIO]:
    if not path.is_absolute() or path.resolve() != path:
        raise Unsupported("Explicit absolute, non-symlink source path required")
    fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    with os.fdopen(fd, "rb") as stream:
        if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
            raise Unsupported("Source must be a regular file")
        yield stream


def _stamp(stream: BinaryIO) -> tuple[int, ...]:
    info = os.fstat(stream.fileno())
    return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns)


def _identity(path: Path) -> SourceIdentity:
    with _open(path) as stream:
        before = _stamp(stream)
        digest, size = _digest(stream)
        after = _stamp(stream)
        named = path.stat(follow_symlinks=False)
        if before != after or (named.st_dev, named.st_ino) != before[:2] or size != before[2]:
            raise Unsupported("Source changed during verification")
    return SourceIdentity(str(path), *before, digest)


def _check(expected: SourceIdentity) -> None:
    if _identity(Path(expected.path)) != expected:
        raise Unsupported("Source identity/hash changed since preview")


def _message(scanner: Scanner, record: Span) -> dict[str, Span]:
    fields = scanner.fields(record)
    role = fields.get("role")
    if role is None or role.kind != 34 or not scanner.small(role):
        raise Unsupported("Not a canonical v1 message; v2 writer layout is gated")
    if "content" not in fields:
        raise Unsupported("Canonical message content is required")
    return fields


def _images(scanner: Scanner, fields: dict[str, Span]) -> Iterator[tuple[int, Span, Span, str]]:
    content = fields["content"]
    if content.kind != 91:
        return
    for index, block in enumerate(scanner.array(content)):
        if block.kind != 123:
            raise Unsupported("Non-object canonical content block")
        members = scanner.fields(block)
        kind = members.get("type")
        if kind is None:
            raise Unsupported("Content block without a type")
        if scanner.small(kind) != "image":
            if scanner.small(kind) == "tool_result":
                raise Unsupported("Nested tool-result content belongs to its format owner")
            continue
        # Extensions may contain pixels/placement policy. Do not guess.
        if set(members) - {"type", "source", "visibility"} or "source" not in members:
            raise Unsupported("Unsupported image block extension")
        source = scanner.fields(members["source"])
        if set(source) != {"type", "media_type", "data"}:
            raise Unsupported("Unsupported image source schema")
        mime = scanner.small(source["media_type"])
        if scanner.small(source["type"]) != "base64" or mime not in ("image/png", "image/jpeg"):
            raise Unsupported("Only canonical static PNG/JPEG base64 sources are qualified")
        if source["data"].kind != 34:
            raise Unsupported("Image payload must be a JSON string")
        yield index, block, source["data"], mime


def _media(scanner: Scanner, fields: dict[str, Span]) -> tuple[Span | None, Span | None]:
    metadata = fields.get("metadata")
    if metadata is None:
        return None, None
    if metadata.kind != 123:
        if scanner.small(metadata, 4) is None:
            return None, None
        raise Unsupported("Canonical metadata is not an object/null")
    members = scanner.fields(metadata)
    namespace = members.get(NAMESPACE)
    if namespace is None:
        return metadata, None
    media = scanner.fields(namespace)
    if set(media) != {"refs"} or media["refs"].kind != 91:
        raise Unsupported("Unknown canonical media namespace shape")
    return metadata, media["refs"]


def _refs(scanner: Scanner, fields: dict[str, Span]) -> Iterator[tuple[Span, dict]]:
    _, refs = _media(scanner, fields)
    if refs:
        for span in scanner.array(refs):
            members = scanner.fields(span)  # Do not erase duplicate ref members.
            if "provenance" in members:
                scanner.fields(members["provenance"])
            ref = scanner.small(span)
            _validate_ref(ref)
            yield span, ref


def _validate_ref(ref: dict) -> None:
    fields = {"v", "session_id", "sha256", "mime", "bytes", "width", "height", "provenance"}
    if not isinstance(ref, dict) or set(ref) != fields or type(ref["v"]) is not int or ref["v"] != 1:
        raise Unsupported("Unknown/invalid canonical media ref version or fields")
    session = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,220}\Z")
    if not isinstance(ref["session_id"], str) or not session.fullmatch(ref["session_id"]):
        raise Unsupported("Invalid canonical media session")
    if not isinstance(ref["sha256"], str) or not re.fullmatch("[0-9a-f]{64}", ref["sha256"]):
        raise Unsupported("Invalid canonical media digest")
    if ref["mime"] not in ("image/png", "image/jpeg", "image/gif", "image/webp"):
        raise Unsupported("Unknown canonical media type")
    if any(type(ref[key]) is not int or ref[key] <= 0 for key in ("bytes", "width", "height")):
        raise Unsupported("Invalid canonical media dimensions/length")
    provenance = ref["provenance"]
    if not isinstance(provenance, dict) or set(provenance) != {
        "kind", "session_id", "id", "created_at", "expires_at",
    }:
        raise Unsupported("Invalid canonical media provenance")
    if provenance["kind"] not in ("tool", "upload", "host", "migration"):
        raise Unsupported("Unknown canonical media provenance kind")
    if not isinstance(provenance["session_id"], str) or not session.fullmatch(provenance["session_id"]):
        raise Unsupported("Invalid original media session")
    if not isinstance(provenance["id"], str) or not provenance["id"]:
        raise Unsupported("Missing original observation identity")
    try:
        created = datetime.fromisoformat(provenance["created_at"])
        expiry = provenance["expires_at"]
        expired = datetime.fromisoformat(expiry) if expiry is not None else None
        if created.utcoffset() is None or (expired is not None and (
            expired.utcoffset() is None or expired < created
        )):
            raise ValueError("Unqualified original media lifetime")
    except (TypeError, ValueError) as exc:
        raise Unsupported("Invalid original media timestamps") from exc


def _slots(scanner: Scanner, fields: dict[str, Span]) -> Iterator[str]:
    content = fields["content"]
    if content.kind != 91:
        return
    for block in scanner.array(content):
        if block.kind != 123:
            continue
        members = scanner.fields(block)
        kind, text = members.get("type"), members.get("text")
        if kind is None or text is None or text.kind != 34 or text.end - text.start > 1024:
            continue
        if scanner.small(kind) == "text":
            match = _MARKER.fullmatch(scanner.small(text))
            if match:
                yield match[1]


def preview(
    source: Path, *, owner: str, session_id: str, fence: Fence,
    backups: tuple[Path, ...] = (), selected_records: int | None = None,
) -> ConversionPlan:
    """Read-only explicit source inventory under its real owner's writer fence.

    A selected prefix is applied BEFORE asset enumeration. No suffix asset can
    become part of a fork/export merely because it is in the same source file.
    """
    if not owner or not session_id or len(backups) > 64:
        raise ValueError("Explicit owner/session and bounded backup selection required")
    if os.name != "posix":
        raise Unsupported("Canonical conversion durability is currently qualified on POSIX only")
    if selected_records is not None and (type(selected_records) is not int or selected_records < 0):
        raise ValueError("selected_records must be a nonnegative integer")
    with fence(owner):
        identity = _identity(source)
        backup_ids = tuple(_identity(path) for path in backups)
        records = images = refs = 0
        with _open(source) as stream:
            scanner = Scanner(stream)
            for record in scanner.records():
                fields = _message(scanner, record)
                if selected_records is None or records < selected_records:
                    image_count = sum(1 for _ in _images(scanner, fields))
                    if image_count and next(_slots(scanner, fields), None):
                        raise Unsupported("Existing text collides with canonical image slot marker")
                    ref_count = sum(1 for _ in _refs(scanner, fields))
                    if ref_count and fields["content"].kind != 91:
                        raise Unsupported("Historical reference conversion requires explicit canonical image slots")
                    if image_count and ref_count:
                        raise Unsupported("Mixed existing refs and inline images need owner-reviewed slot ordering")
                    images += image_count
                    refs += ref_count
                records += 1
        _check(identity)
        for backup in backup_ids:
            _check(backup)
        if selected_records is not None and selected_records > records:
            raise Unsupported("Selected prefix exceeds canonical history")
    return ConversionPlan(
        ADAPTER, owner, session_id, identity, backup_ids, records, images, refs,
        records if selected_records is None else selected_records,
    )


def _decode(scanner: Scanner, span: Span, output: BinaryIO) -> tuple[str, int]:
    scanner.seek(span.start)
    pending, ended, size = b"", False, 0
    digest = hashlib.sha256()
    try:
        for chunk in scanner.string_chunks():
            if ended and chunk:
                raise Unsupported("Base64 data after padding")
            pending += chunk
            count = len(pending) // 4 * 4
            if not count:
                continue
            raw, pending = pending[:count], pending[count:]
            decoded = base64.b64decode(raw, validate=True)
            # Reject noncanonical pad bits as well as whitespace/invalid alphabet.
            if base64.b64encode(decoded) != raw:
                raise Unsupported("Noncanonical base64 encoding")
            if b"=" in raw:
                ended = True
                if pending:
                    raise Unsupported("Base64 data after padding")
            output.write(decoded)
            digest.update(decoded)
            size += len(decoded)
        if pending or not size:
            raise Unsupported("Incomplete/empty base64 image")
    except (ValueError, binascii.Error) as exc:
        raise Unsupported("Invalid strict base64 image") from exc
    output.seek(0)
    return digest.hexdigest(), size


def _verify_asset(ref: dict, reader: AssetReader) -> None:
    _validate_ref(ref)
    snapshot = deepcopy(ref)
    with reader(deepcopy(snapshot)) as stream:
        digest, size = _digest(stream)
    if digest != snapshot.get("sha256") or size != snapshot["bytes"]:
        raise Unsupported("Asset binary closure verification failed")


def _sync(directory: Path) -> None:
    fd = os.open(directory, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _write_new(path: Path, value: Any) -> None:
    with path.open("xb") as output:
        os.chmod(path, 0o600)
        output.write(_json(value) + b"\n")
        output.flush()
        os.fsync(output.fileno())


def _event(journal: BinaryIO, **value: Any) -> None:
    journal.write(_json(value) + b"\n")
    journal.flush()
    os.fsync(journal.fileno())


def _patch(db: sqlite3.Connection, start: int, end: int, value: bytes) -> None:
    if len(value) > 16384:
        raise Unsupported("Canonical edit exceeds adapter bound")
    db.execute("insert into patches values(?,?,?)", (start, end, value))


def _emit_candidate(source, output, db, selected_end):
    offset = 0
    for start, end, value in db.execute("select start,end,value from patches order by start,rowid"):
        if start < offset:
            raise Unsupported("Overlapping conversion edits")
        copy_span(source, output, offset, start)
        output.write(value)
        offset = end
    copy_span(source, output, offset, selected_end)
    if selected_end:
        output.write(b"\n")


class _HashWriter:
    def __init__(self):
        self.digest = hashlib.sha256()
        self.size = 0

    def write(self, value):
        self.digest.update(value)
        self.size += len(value)

    def result(self):
        return self.digest.hexdigest(), self.size


def _namespace_patch(
    scanner: Scanner, fields: dict[str, Span], row: int, db: sqlite3.Connection,
) -> None:
    """Write metadata edits to disk, never an in-memory per-message ref list."""
    metadata, refs = _media(scanner, fields)
    # Spool replacement insertions too: SQLite per-row blobs stay bounded.
    if refs is not None:
        position = refs.end - 1
        prefix = b"," if next(scanner.array(refs), None) is not None else b""
        suffix = b""
    elif metadata is not None:
        position = metadata.end - 1
        prefix = (b"," if scanner.fields(metadata) else b"") + _json(NAMESPACE) + b':{"refs":['
        suffix = b"]}"
    else:
        position = fields["content"].end  # Insert a new field after content.
        prefix = b',"metadata":{' + _json(NAMESPACE) + b':{"refs":['
        suffix = b"]}}"
        old = fields.get("metadata")
        if old is not None:
            # metadata:null is replaced, not duplicated.
            position = old.start
            prefix = b"{" + _json(NAMESPACE) + b':{"refs":['
    first = True
    for (raw,) in db.execute("select ref from assets where row=? order by block", (row,)):
        _patch(db, position, position, prefix + (b"" if first else b",") + raw)
        prefix, first = b"", False
    if not first:
        old = fields.get("metadata")
        end = old.end if old is not None and metadata is None else position
        _patch(db, position, end, suffix)


async def _stage_image_groups(scanner, fields, row, db, directory, capability, origin_for, journal, cancelled):
    """Complete true-origin groups on disk; memory remains host-max-images bound."""
    ingest_group = getattr(capability, "ingest_group_sync", None)
    if not callable(ingest_group):
        return False
    for index, _, data, mime in _images(scanner, fields):
        if cancelled():
            raise InterruptedError("Conversion cancelled before group decoding")
        origin = deepcopy(origin_for(row, index))
        if len(_json(origin)) > 16384:
            raise Unsupported("Original group provenance exceeds adapter bound")
        file = directory / f"decoded-{row}-{index}"
        with file.open("xb+") as pixels:
            os.chmod(file, 0o600)
            digest, size = _decode(scanner, data, pixels)
        db.execute("insert into image_groups values(?,?,?,?,?,?)",
                   (row, index, _json(origin), mime, digest, size))
    db.commit()
    for (encoded_origin,) in db.execute(
        "select origin from image_groups where row=? group by origin order by min(block)", (row,),
    ):
        origin = json.loads(encoded_origin)
        if cancelled():
            raise InterruptedError("Conversion cancelled before group staging")

        def sources():
            for index, mime in db.execute(
                "select block,mime from image_groups where row=? and origin=? order by block",
                (row, encoded_origin),
            ):
                with _open(directory / f"decoded-{row}-{index}") as pixels:
                    yield {"stream": pixels, "mime": mime}

        _event(journal, state="group-ingest-intent", row=row, origin=origin)
        result = ingest_group(sources(), origin=deepcopy(origin), policy=None)
        refs = deepcopy(result["staged_refs"])
        if not isinstance(refs, list):
            raise Unsupported("Group owner returned invalid reference sequence")
        count = 0
        for index, mime, digest, size in db.execute(
            "select block,mime,digest,bytes from image_groups where row=? and origin=? order by block",
            (row, encoded_origin),
        ):
            if count >= len(refs):
                raise Unsupported("Group owner omitted original image")
            ref = refs[count]
            _validate_ref(ref)
            if (ref["provenance"] != origin or ref["sha256"] != digest or ref["bytes"] != size
                    or ref["mime"] != mime):
                raise Unsupported("Group owner changed ordered original image identity")
            db.execute("insert into assets values(?,?,?,?)", (row, index, _json(ref), encoded_origin))
            count += 1
        if count != len(refs):
            raise Unsupported("Group owner returned extra image references")
        db.commit()
        _event(journal, state="group-staged", row=row, origin=origin, count=count)
        if cancelled():
            raise InterruptedError("Conversion cancelled after group staging")
        _event(journal, state="group-publication-intent", row=row, origin=origin, count=count)
        result = await capability.publish(deepcopy(refs), origin=deepcopy(origin), disposition="retain")
        if result["refs"] != refs:
            raise Unsupported("Group publication changed complete original ref set")
        for ref in refs:
            _event(journal, state="published-assets-only", row=row, ref=ref)
        if cancelled():
            raise InterruptedError("Conversion cancelled after group publication")
    return True


async def _adopt_existing_groups(scanner, selected_records, db, adopt, target_id, limit, journal, cancelled):
    for row, record in enumerate(scanner.records()):
        if row >= selected_records:
            break
        for span, ref in _refs(scanner, _message(scanner, record)):
            db.execute("insert into transfer_refs values(?,?,?,?,?,?)", (
                row, span.start, span.end, _json(ref["provenance"]), _json(ref), None,
            ))
    db.commit()
    for (origin,) in db.execute(
        "select origin from transfer_refs group by origin order by min(start)",
    ):
        if cancelled():
            raise InterruptedError("Conversion cancelled before reference group adoption")
        # This is reference metadata only; the actual host adopt method enforces
        # its selected max_images and owner policy, not a pixel-sized allocation.
        count = db.execute("select count(*) from transfer_refs where origin=?", (origin,)).fetchone()[0]
        if count > limit:
            raise Unsupported("Selected transfer group exceeds host image count")
        refs = [json.loads(raw) for (raw,) in db.execute(
            "select ref from transfer_refs where origin=? order by start", (origin,),
        )]
        expected = [{**deepcopy(ref), "session_id": target_id} for ref in refs]
        _event(journal, state="adoption-group-intent", origin=json.loads(origin), count=len(refs))
        adopted = await adopt(deepcopy(refs))
        if not isinstance(adopted, list) or adopted != expected:
            raise Unsupported("Adoption changed bytes or original provenance/expiry")
        for ref, copied in zip(refs, adopted):
            _validate_ref(copied)
            db.execute("update transfer_refs set adopted=? where ref=?", (_json(copied), _json(ref)))
        db.commit()
        _event(journal, state="adopted-assets-only", origin=json.loads(origin), count=len(refs))
        if cancelled():
            raise InterruptedError("Conversion cancelled after reference adoption")


async def build_candidate(
    plan: ConversionPlan, directory: Path, *, fence: Fence, capability: MediaCapability,
    origin_for: Origin, asset_reader: AssetReader, cancelled: Callable[[], bool] = lambda: False,
    export_assets: bool = False,
    _adopt_ref: Callable[[list[dict]], Awaitable[list[dict]]] | None = None,
    _target_session_id: str | None = None,
    _adopt_limit: int | None = None,
) -> Candidate:
    """Build assets-first candidate + enumerable journal; NEVER replace source.

    ``asset_reader`` is an owner-authorized published/staged inspection seam,
    not ordinary model resolution. It must verify admission/provenance and open
    immutable original bytes. No path/hash-only asset adoption is performed.
    """
    if plan.adapter != ADAPTER:
        raise Unsupported("Unsupported conversion plan")
    if os.name != "posix":
        raise Unsupported("Canonical conversion durability is currently qualified on POSIX only")
    if not directory.is_absolute() or directory.parent.resolve() != directory.parent:
        raise Unsupported("Explicit private operation directory required")
    with fence(plan.owner):
        _check(plan.source)
        for backup in plan.backups:
            _check(backup)
        directory.mkdir(mode=0o700)  # Existing/unknown operation is never replayed.
        _write_new(directory / "plan.json", asdict(plan))
        _sync(directory.parent)
        with (directory / "journal.jsonl").open("xb") as journal:
            os.chmod(directory / "journal.jsonl", 0o600)
            _event(journal, state="started", adapter=ADAPTER, source=asdict(plan.source))
            try:
                return await _build(
                    plan, directory, capability, origin_for, asset_reader, cancelled, journal,
                    export_assets, _adopt_ref, _target_session_id,
                    _adopt_limit,
                )
            except BaseException as exc:
                # No exception payload, image data or opaque provider text in journal.
                _event(journal, state="interrupted", reason=type(exc).__name__)
                raise


async def _build(
    plan, directory, capability, origin_for, reader, cancelled, journal,
    export_assets, adopt_ref, target_session_id,
    adopt_limit,
):
    # Recovery copies are per rewrite, never a standing growing second body.
    for index, identity in enumerate((plan.source, *plan.backups)):
        with _open(Path(identity.path)) as source, (directory / f"original-{index}").open("xb") as output:
            os.chmod(output.name, 0o600)
            copy_span(source, output, 0, identity.size)
            output.flush()
            os.fsync(output.fileno())
        with (directory / f"original-{index}").open("rb") as source:
            if _digest(source) != (identity.sha256, identity.size):
                raise Unsupported("Original recovery copy mismatch")
    db = sqlite3.connect(directory / "manifest.sqlite")
    os.chmod(directory / "manifest.sqlite", 0o600)
    try:
        db.execute("pragma cache_size=-512")
        db.execute("pragma temp_store=FILE")
        db.execute("create table patches(start integer,end integer,value blob)")
        db.execute("create table assets(row integer,block integer,ref blob,origin blob)")
        db.execute("create table closure(ref blob)")
        db.execute("create table image_groups(row integer,block integer,origin blob,mime text,digest text,bytes integer)")
        db.execute("create index image_groups_row_origin on image_groups(row,origin,block)")
        db.execute("create table transfer_refs(row integer,start integer,end integer,origin blob,ref blob,adopted blob)")
        if adopt_ref is not None:
            if type(adopt_limit) is not int or adopt_limit <= 0:
                raise Unsupported("Selected transfer needs the target host image bound")
            with _open(Path(plan.source.path)) as source:
                await _adopt_existing_groups(
                    Scanner(source), plan.selected_records, db, adopt_ref, target_session_id,
                    adopt_limit, journal, cancelled,
                )
        db.execute("create index assets_row_block on assets(row,block)")
        decoded_bytes = image_count = inline_removed_bytes = 0
        selected_end = 0
        with _open(Path(plan.source.path)) as source:
            scanner = Scanner(source)
            for row, record in enumerate(scanner.records()):
                if row >= plan.selected_records:
                    break
                if cancelled():
                    raise InterruptedError("Conversion cancelled at record checkpoint")
                fields = _message(scanner, record)
                if next(_refs(scanner, fields), None) is not None and next(_images(scanner, fields), None) is not None:
                    raise Unsupported("Mixed existing refs and inline images need owner-reviewed slot ordering")
                for span, ref in _refs(scanner, fields):
                    if cancelled():
                        raise InterruptedError("Conversion cancelled before reference adoption/validation")
                    if adopt_ref is not None:
                        raw = db.execute("select adopted from transfer_refs where row=? and start=?",
                                         (row, span.start)).fetchone()
                        if raw is None or raw[0] is None:
                            raise Unsupported("Missing owner-adopted reference group")
                        adopted = json.loads(raw[0])
                        _patch(db, span.start, span.end, _json(adopted))
                        ref = deepcopy(adopted)
                    elif ref.get("session_id") != plan.session_id:
                        raise Unsupported("Foreign ref requires explicit owner-authorized adoption")
                    _verify_asset(ref, reader)
                    db.execute("insert into closure values(?)", (_json(ref),))
                grouped = await _stage_image_groups(
                    scanner, fields, row, db, directory, capability, origin_for, journal, cancelled,
                )
                for index, block, data, mime in _images(scanner, fields):
                    if cancelled():
                        raise InterruptedError("Conversion cancelled before image staging")
                    if grouped:
                        raw, raw_origin = db.execute(
                            "select ref,origin from assets where row=? and block=?", (row, index),
                        ).fetchone()
                        ref, origin = json.loads(raw), json.loads(raw_origin)
                        size = ref["bytes"]
                    else:
                        origin = deepcopy(origin_for(row, index))
                        with tempfile.TemporaryFile(dir=directory) as pixels:
                            digest, size = _decode(scanner, data, pixels)
                            _event(journal, state="ingest-intent", row=row, block=index, origin=origin,
                                   sha256=digest, bytes=size)
                            result = await capability.ingest(
                                {"stream": pixels, "mime": mime}, source_format="image-bytes",
                                origin=deepcopy(origin), policy=None,
                            )
                        refs = result["staged_refs"]
                        if len(refs) != 1:
                            raise Unsupported("Media ingest must stage exactly one image")
                        ref = deepcopy(refs[0])
                        if ref.get("sha256") != digest or ref.get("bytes") != size or ref.get("mime") != mime:
                            raise Unsupported("Ingest changed original image identity")
                        db.execute("insert into assets values(?,?,?,?)", (row, index, _json(ref), _json(origin)))
                        db.commit()
                        _event(journal, state="staged", row=row, block=index, ref=ref, origin=origin)
                        if cancelled():
                            raise InterruptedError("Conversion cancelled after staging")
                        _event(journal, state="publication-intent", row=row, block=index, ref=ref, origin=origin)
                        publication = await capability.publish(
                            [deepcopy(ref)], origin=deepcopy(origin), disposition="retain",
                        )
                        if publication["refs"] != [ref]:
                            raise Unsupported("Publication changed staged ref identity")
                    if ref.get("session_id") != plan.session_id or ref.get("provenance") != origin:
                        raise Unsupported("Ingest changed original observation scope/policy")
                    _verify_asset(ref, reader)
                    db.execute("insert into closure values(?)", (_json(ref),))
                    # Image position is retained as a text slot; F1 owns its exact
                    # marker contract. No user role/tool pairing is reconstructed.
                    members = scanner.fields(block)
                    # Full block rewrite is bounded except original data: copy
                    # optional visibility as its exact raw span.
                    replacement = b'{"type":"text","text":' + _json(
                        f"[Session image {ref['sha256']}]"
                    )
                    visibility = members.get("visibility")
                    if visibility:
                        replacement += b',"visibility":' + _json(scanner.small(visibility))
                    replacement += b"}"
                    _patch(db, block.start, block.end, replacement)
                    image_count += 1
                    decoded_bytes += size
                    inline_removed_bytes += block.end - block.start
                    _event(journal, state="published-assets-only", row=row, block=index, ref=ref)
                    if cancelled():
                        raise InterruptedError("Conversion cancelled after publication")
                _namespace_patch(scanner, fields, row, db)
                db.commit()
                # JSONL termination is normalized only for selected-prefix end.
                selected_end = record.end
            _check(plan.source)
            if cancelled():
                raise InterruptedError("Conversion cancelled before candidate output")
            _event(journal, state="assets-verified", count=image_count)
            with (directory / "candidate.jsonl").open("xb") as output:
                os.chmod(output.name, 0o600)
                _emit_candidate(source, output, db, selected_end)
                output.flush()
                os.fsync(output.fileno())
            expected = _HashWriter()
            _emit_candidate(source, expected, db, selected_end)
            with _open(directory / "candidate.jsonl") as readback:
                if _digest(readback) != expected.result():
                    raise Unsupported("Candidate differs from exact allowed-span transformation")
        # Verify parse, ref equality/order and binary closure from the candidate,
        # rather than treating a successful ingest receipt as a verified rewrite.
        _verify_candidate(directory / "candidate.jsonl", db, reader, plan.selected_records)
        if export_assets:
            _portable_export(directory, db, reader, plan.selected_records)
        if cancelled():
            raise InterruptedError("Conversion cancelled before verification receipt")
        for identity in (plan.source, *plan.backups):
            _check(identity)
        with (directory / "candidate.jsonl").open("rb") as stream:
            digest, size = _digest(stream)
        db.commit()
        result = Candidate(
            str(directory), digest, size, plan.selected_records, image_count,
            plan.source.size, decoded_bytes, inline_removed_bytes,
        )
        with _open(directory / "manifest.sqlite") as stream:
            manifest_hash, manifest_size = _digest(stream)
        with _open(directory / "plan.json") as stream:
            plan_hash, plan_size = _digest(stream)
        _write_new(directory / "verified.json", {
            **asdict(result), "adapter": ADAPTER,
            "manifest_sha256": manifest_hash, "manifest_bytes": manifest_size,
            "plan_sha256": plan_hash, "plan_bytes": plan_size,
        })
        _event(journal, state=result.state, sha256=digest, bytes=size)
        _sync(directory)
        return result
    finally:
        db.close()


def _export_assets(directory, db, reader):
    """Portable bytes are evidence only, never import/admission authority."""
    assets = directory / "assets"
    assets.mkdir(mode=0o700)
    for (raw,) in db.execute("select ref from closure order by rowid"):
        ref = json.loads(raw)
        digest = ref.get("sha256")
        if not isinstance(digest, str) or len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest):
            raise Unsupported("Invalid asset digest")
        path = assets / digest
        if path.exists():
            with _open(path) as existing:
                if _digest(existing) != (digest, ref["bytes"]):
                    raise Unsupported("Export asset changed")
            continue
        with reader(deepcopy(ref)) as source, path.open("xb") as output:
            os.chmod(path, 0o600)
            observed = _HashWriter()
            while chunk := source.read(CHUNK):
                output.write(chunk)
                observed.write(chunk)
            output.flush()
            os.fsync(output.fileno())
        if observed.result() != (digest, ref["bytes"]):
            raise Unsupported("Export binary closure mismatch")
    _sync(assets)


def _portable_export(directory, db, reader, records):
    """Separate delivery surface: NEVER copy private recovery/plan/journal data."""
    portable = directory / "portable"
    portable.mkdir(mode=0o700)
    with _open(directory / "candidate.jsonl") as source, (portable / "candidate.jsonl").open("xb") as output:
        os.chmod(output.name, 0o600)
        while chunk := source.read(CHUNK):
            output.write(chunk)
        output.flush()
        os.fsync(output.fileno())
    exported = sqlite3.connect(portable / "manifest.sqlite")
    os.chmod(portable / "manifest.sqlite", 0o600)
    try:
        exported.execute("pragma cache_size=-512")
        exported.execute("create table closure(ref blob)")
        for (raw,) in db.execute("select ref from closure order by rowid"):
            exported.execute("insert into closure values(?)", (raw,))
        exported.commit()
        _export_assets(portable, exported, reader)
        _verify_candidate(portable / "candidate.jsonl", exported, reader, records)
    finally:
        exported.close()
    receipt = {"adapter": ADAPTER, "records": records, "state": "portable-integrity-only-not-authority"}
    for name, key in (("candidate.jsonl", "candidate"), ("manifest.sqlite", "manifest")):
        with _open(portable / name) as source:
            digest, size = _digest(source)
        receipt[f"{key}_sha256"], receipt[f"{key}_bytes"] = digest, size
    _write_new(portable / "export.json", receipt)
    _sync(portable)


async def transfer_prefix(
    plan: ConversionPlan, directory: Path, *, fence: Fence,
    source_store: Any, target_store: Any, target_session_id: str,
    asset_reader: AssetReader, cancelled: Callable[[], bool] = lambda: False,
) -> Candidate:
    """Fork/import only via the real source owner's accepted-commit adoption.

    The host must qualify source_store (including an authorized exported-store
    adapter) itself; bundle possession or a digest does not create one. Filter
    prefix with preview FIRST. Target asset adoption does not commit a transcript.
    """
    if plan.inline_images:
        raise Unsupported("Externalize inline images before transferring reference ownership")

    async def adopt(refs):
        value = await target_store.adopt(refs, source_store)
        # F1 host store returns the adopted reference sequence.
        if not isinstance(value, list) or len(value) != len(refs):
            raise Unsupported("Invalid owner adoption result")
        return value

    return await build_candidate(
        plan, directory, fence=fence, capability=target_store,
        origin_for=lambda *_: {}, asset_reader=asset_reader, cancelled=cancelled,
        export_assets=True, _adopt_ref=adopt, _target_session_id=target_session_id,
        _adopt_limit=getattr(getattr(target_store, "policy", None), "max_images", None),
    )


def adopt_selected_media(
    messages: list[dict], *, source_store: Any, target_store: Any,
    target_session_id: str, now: datetime | None = None,
) -> list[dict]:
    """Sync closure callback for an ALREADY selected in-memory fork snapshot.

    All messages transform successfully before returning; caller must not write
    until return, then acknowledge its actual target checkpoint. Failure can
    leave published-uncommitted assets in F1's journal, never dangling messages.
    This intentionally retains the existing fork caller's list cost; it is not
    the bounded huge-record conversion API.
    """
    current = now or datetime.now(timezone.utc)
    if current.utcoffset() is None:
        raise ValueError("Fork media clock must be timezone qualified")
    # F1 binds the selected complete set for a real origin, not independent
    # per-image observations. Group across the ALREADY selected snapshot before
    # copying anything. This API intentionally shares the caller's list cost.
    groups: dict[bytes, list[dict]] = {}
    adopted_by_identity: dict[bytes, dict] = {}
    for message in messages:
        metadata = message.get("metadata")
        namespace = metadata.get(NAMESPACE) if isinstance(metadata, dict) else None
        if namespace is None:
            continue
        if not isinstance(namespace, dict) or set(namespace) != {"refs"} or not isinstance(namespace["refs"], list):
            raise Unsupported("Unknown selected media namespace")
        for original_ref in namespace["refs"]:
            _validate_ref(original_ref)
            ref = deepcopy(original_ref)
            if source_store is None or target_store is None or not callable(getattr(target_store, "adopt_sync", None)):
                raise Unsupported("Selected media requires an owner-authorized closure callback")
            if getattr(source_store, "read_only", False):
                source_store.validate_published_ref(deepcopy(ref), check_expiry=False)
            else:
                source_store.validate_ref(deepcopy(ref), committed=True, check_expiry=False)
            expiry = ref["provenance"]["expires_at"]
            if expiry is None or datetime.fromisoformat(expiry) > current:
                groups.setdefault(_json(ref["provenance"]), []).append(ref)
    for refs in groups.values():
        expected = [{**deepcopy(ref), "session_id": target_session_id} for ref in refs]
        adopted = target_store.adopt_sync(deepcopy(refs), source_store)
        if not isinstance(adopted, list) or adopted != expected:
            raise Unsupported("Selected adoption changed original identity or expiry")
        for ref, copied in zip(refs, adopted):
            _validate_ref(copied)
            adopted_by_identity[_json(ref)] = deepcopy(copied)
    result = []
    for message in messages:
        metadata = message.get("metadata")
        if not isinstance(metadata, dict) or NAMESPACE not in metadata:
            result.append(message)
            continue
        namespace = metadata[NAMESPACE]
        if not isinstance(namespace, dict) or set(namespace) != {"refs"} or not isinstance(namespace["refs"], list):
            raise Unsupported("Unknown selected media namespace")
        refs = namespace["refs"]
        if refs and (source_store is None or target_store is None or not callable(
            getattr(target_store, "adopt_sync", None)
        )):
            raise Unsupported("Selected media requires an owner-authorized closure callback")
        content = message.get("content")
        if not isinstance(content, (str, list)):
            raise Unsupported("Selected media message has unsupported content")
        # Validate marker positions once, before any adoption effects.
        slots = []
        if isinstance(content, list):
            for index, block in enumerate(content):
                if isinstance(block, dict) and block.get("type") == "text" and isinstance(block.get("text"), str):
                    match = _MARKER.fullmatch(block["text"])
                    if match:
                        slots.append((index, match[1]))
            if [digest for _, digest in slots] != [ref.get("sha256") for ref in refs if isinstance(ref, dict)]:
                raise Unsupported("Selected media slots do not match exact ordered refs")
        retained, replacements, warnings = [], {}, []
        for index, ref in enumerate(refs):
            _validate_ref(ref)
            snapshot = deepcopy(ref)
            expected = {**snapshot, "session_id": target_session_id}
            # Host validation establishes actual committed origin, even when
            # expiry forbids any pixel read/copy. It grants no new TTL.
            if getattr(source_store, "read_only", False):
                source_store.validate_published_ref(deepcopy(snapshot), check_expiry=False)
            else:
                source_store.validate_ref(deepcopy(snapshot), committed=True, check_expiry=False)
            expiry = snapshot["provenance"]["expires_at"]
            if expiry is not None and datetime.fromisoformat(expiry) <= current:
                warning = "[Image unavailable: original observation expired; no pixels supplied]"
                if isinstance(content, list):
                    replacements[slots[index][0]] = warning
                else:
                    warnings.append(warning)
                continue
            adopted = adopted_by_identity.get(_json(snapshot))
            if adopted != expected:
                raise Unsupported("Selected adoption changed original identity or expiry")
            _validate_ref(adopted)
            retained.append(deepcopy(adopted))
        copied = dict(message)
        copied_metadata = dict(metadata)
        copied_metadata[NAMESPACE] = {"refs": retained}
        copied["metadata"] = copied_metadata
        if replacements:
            copied_content = list(content)
            for index, warning in replacements.items():
                copied_content[index] = {**content[index], "text": warning}
            copied["content"] = copied_content
        elif warnings:
            copied["content"] = content + "\n" + "\n".join(warnings)
        result.append(copied)
    return result


def verify_export(directory: Path) -> None:
    """Verify saved candidate and independent portable binary closure, boundedly.

    Read-only integrity is not authentication, retention consent or a checkpoint.
    Import requires an application-authorized source owner and transfer_prefix.
    """
    expected = {"candidate.jsonl", "manifest.sqlite", "assets", "export.json"}
    with os.scandir(directory) as entries:
        for entry in entries:
            if entry.name not in expected or entry.is_symlink():
                raise Unsupported("Unexpected private/unknown file in portable export")
    receipt = directory / "export.json"
    if receipt.stat().st_size > 16384:
        raise Unsupported("Invalid export receipt")
    value = json.loads(receipt.read_bytes())
    if value.get("adapter") != ADAPTER:
        raise Unsupported("Unknown portable export adapter")
    for name, key in (("candidate.jsonl", "candidate"), ("manifest.sqlite", "manifest")):
        with _open(directory / name) as source:
            if _digest(source) != (value[f"{key}_sha256"], value[f"{key}_bytes"]):
                raise Unsupported("Portable export changed")

    @contextmanager
    def reader(ref):
        digest = ref.get("sha256", "")
        if not isinstance(digest, str) or len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest):
            raise Unsupported("Invalid export digest")
        with _open(directory / "assets" / digest) as stream:
            yield stream

    db = sqlite3.connect(f"file:{directory / 'manifest.sqlite'}?mode=ro", uri=True)
    try:
        db.execute("pragma cache_size=-512")
        count = value["records"]
        _verify_candidate(directory / "candidate.jsonl", db, reader, count)
        db.execute("pragma temp_store=FILE")
        db.execute("create temp table reachable(digest text primary key)")
        for (raw,) in db.execute("select ref from closure"):
            ref = json.loads(raw)
            db.execute("insert or ignore into reachable values(?)", (ref["sha256"],))
        with os.scandir(directory / "assets") as entries:
            for entry in entries:
                if entry.is_symlink() or not entry.is_file() or db.execute(
                    "select 1 from reachable where digest=?", (entry.name,)
                ).fetchone() is None:
                    raise Unsupported("Unexpected asset/private bytes in portable export")
    finally:
        db.close()

def _closure_schema(db):
    allowed = {
        ("table", "closure"), ("table", "patches"), ("table", "assets"),
        ("table", "image_groups"), ("index", "assets_row_block"),
        ("table", "transfer_refs"),
        ("index", "image_groups_row_origin"),
    }
    for kind, name in db.execute("select type,name from sqlite_master"):
        if (kind, name) not in allowed:
            raise Unsupported("Unexpected/dynamic objects in historical manifest")
    schema = db.execute("select type,sql from sqlite_master where name='closure'").fetchone()
    if schema is None or schema[0] != "table" or not re.fullmatch(
        r"CREATE TABLE closure\s*\(\s*ref blob\s*\)", schema[1], re.IGNORECASE,
    ):
        raise Unsupported("Historical manifest closure is not the generated plain table")
    if db.execute(
        "select 1 from closure where typeof(ref)!='blob' or length(ref)>16384 limit 1"
    ).fetchone():
        raise Unsupported("Historical manifest contains an oversized/noncanonical reference row")


def _iter_candidate_refs(path, db, reader, expected_records):
    _closure_schema(db)
    expected = iter(db.execute("select ref from closure order by rowid"))
    count = 0
    with _open(path) as source:
        scanner = Scanner(source)
        for record in scanner.records():
            fields = _message(scanner, record)
            if next(_images(scanner, fields), None) is not None:
                raise Unsupported("Inline image survived candidate conversion")
            for _, ref in _refs(scanner, fields):
                item = next(expected, None)
                if item is None or _json(ref) != item[0]:
                    raise Unsupported("Candidate reference closure mismatch")
                _verify_asset(ref, reader)
                # Authority comes from this exact parsed canonical position,
                # never a second query of a potentially dynamic manifest.
                yield deepcopy(ref)
            if fields["content"].kind == 91 and _media(scanner, fields)[1] is not None:
                slots = iter(_slots(scanner, fields))
                for _, ref in _refs(scanner, fields):
                    if next(slots, None) != ref["sha256"]:
                        raise Unsupported("Canonical image slot/ref position mismatch")
                if next(slots, None) is not None:
                    raise Unsupported("Unbound canonical image marker")
            count += 1
    if count != expected_records or next(expected, None) is not None:
        raise Unsupported("Candidate record/reference count mismatch")


def _verify_candidate(path, db, reader, expected_records):
    for _ in _iter_candidate_refs(path, db, reader, expected_records):
        pass


def iter_verified_candidate_refs(
    operation_dir: Path, *, installed: Path, session_id: str, asset_reader: AssetReader,
) -> Iterator[dict]:
    """Bounded owner verifier for an ACTUALLY installed historical candidate.

    This is a reference enumerator, not a receipt/ACK constructor or authority.
    The actual checkpoint owner binds it to a local receipt, real source/writer
    fence and store. It MUST exhaust this iterator before accepting anything:
    final file/manifest rechecks happen after the last yielded reference.
    No record is passed to json.loads(), including a huge individual message.
    """
    if reconcile(operation_dir, installed=installed) != "installed-bytes-match-checkpoint-unconfirmed":
        raise Unsupported("Installed canonical body differs from verified historical candidate")
    expected = _identity(installed)
    verified = operation_dir / "verified.json"
    if verified.stat().st_size > 16384:
        raise Unsupported("Invalid historical candidate verification receipt")
    receipt = json.loads(verified.read_bytes())
    if receipt.get("adapter") != ADAPTER or (expected.sha256, expected.size) != (
        receipt["sha256"], receipt["bytes"],
    ):
        raise Unsupported("Unknown or changed historical candidate checkpoint")
    db = sqlite3.connect(f"file:{operation_dir / 'manifest.sqlite'}?mode=ro", uri=True)
    try:
        db.execute("pragma cache_size=-512")
        for ref in _iter_candidate_refs(installed, db, asset_reader, receipt["records"]):
            if ref["session_id"] != session_id:
                raise Unsupported("Historical candidate ref belongs to another session")
            yield ref
        _check(expected)
        if reconcile(operation_dir, installed=installed) != "installed-bytes-match-checkpoint-unconfirmed":
            raise Unsupported("Historical candidate changed during checkpoint enumeration")
    finally:
        db.close()


def reconcile(directory: Path, *, installed: Path | None = None) -> str:
    """Read saved exact candidate identity after a lost acknowledgement.

    Does not re-ingest, republish, replace, truncate or infer a commit from an
    assets-only journal. An equal installed hash is still not an owner checkpoint.
    """
    verified = directory / "verified.json"
    if not verified.exists():
        return "interrupted-preserve-and-inspect-journal"
    if verified.stat().st_size > 16384:
        raise Unsupported("Invalid verification receipt")
    value = json.loads(verified.read_bytes())
    with _open(directory / "candidate.jsonl") as stream:
        observed = _digest(stream)
    if observed != (value["sha256"], value["bytes"]):
        raise Unsupported("Saved candidate changed after verification")
    for name, key in (("manifest.sqlite", "manifest"), ("plan.json", "plan")):
        with _open(directory / name) as stream:
            if _digest(stream) != (value[f"{key}_sha256"], value[f"{key}_bytes"]):
                raise Unsupported("Saved manifest/plan changed after verification")
    plan_path = directory / "plan.json"
    if plan_path.stat().st_size > 1024 * 1024:
        raise Unsupported("Invalid saved plan")
    plan = json.loads(plan_path.read_bytes())
    for index, identity in enumerate((plan["source"], *plan["backups"])):
        with _open(directory / f"original-{index}") as stream:
            if _digest(stream) != (identity["sha256"], identity["size"]):
                raise Unsupported("Saved original/backup changed after verification")
    if installed is None:
        return "verified-candidate-not-applied"
    with _open(installed) as stream:
        current = _digest(stream)
    return "installed-bytes-match-checkpoint-unconfirmed" if current == observed else "installed-bytes-differ"