"""Session-owned original media. Hosts own policy, origins and checkpoints.

No application root selection, transcript writer or vendor wire encoding lives
here. Durability currently requires qualified POSIX descriptors/directory fsync.
"""
from __future__ import annotations

import asyncio
import base64
import binascii
import copy
import hashlib
import io
import json
import math
import os
import re
import stat
import tempfile
import threading
import uuid
import warnings
from collections.abc import Mapping
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, BinaryIO, Callable

try:
    import fcntl
except ImportError:  # Text/ref readers remain importable on unqualified platforms.
    fcntl = None

MEDIA_NAMESPACE = "experimental.media.v1"
_SESSION = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,220}$")
_SHA = re.compile(r"^[0-9a-f]{64}$")
_REF_FIELDS = {"v", "session_id", "sha256", "mime", "bytes", "width", "height", "provenance"}
_ORIGIN_FIELDS = {"kind", "session_id", "id", "created_at", "expires_at"}
_MIMES = {"image/png", "image/jpeg"}
_CHUNK = 64 * 1024  # I/O chunk, not an observation quota.


class MediaRefusal(ValueError):
    """Local refusal, never evidence of a transport attempt."""

    def __init__(self, message: str, *, code: str = "refused") -> None:
        super().__init__(message)
        self.code = code


def _json(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
                      allow_nan=False).encode("utf-8")


def _key(value: Any) -> str:
    return hashlib.sha256(_json(value)).hexdigest()


def _positive(value: Any, name: str) -> int:
    if type(value) is not int or value <= 0:
        raise MediaRefusal(f"{name} must be a positive integer")
    return value


def _session(value: Any) -> str:
    if not isinstance(value, str) or not _SESSION.fullmatch(value):
        raise MediaRefusal("invalid session identity")
    return value


def _date(value: Any) -> datetime:
    if not isinstance(value, str):
        raise MediaRefusal("timestamp must be timezone-qualified text")
    try:
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise MediaRefusal("invalid timestamp") from exc
    if result.tzinfo is None or result.utcoffset() is None:
        raise MediaRefusal("timestamp must include timezone")
    return result


def validate_blob_ref(ref: Any) -> dict[str, Any]:
    """Strict minimum reader; structural validity never grants access."""
    if not isinstance(ref, dict) or set(ref) != _REF_FIELDS:
        raise MediaRefusal("invalid media reference fields")
    if type(ref["v"]) is not int or ref["v"] != 1:
        raise MediaRefusal("unsupported media reference version")
    _session(ref["session_id"])
    if not isinstance(ref["sha256"], str) or not _SHA.fullmatch(ref["sha256"]):
        raise MediaRefusal("invalid original media digest")
    if not isinstance(ref["mime"], str) or ref["mime"] not in _MIMES:
        raise MediaRefusal("unsupported image format")
    for name in ("bytes", "width", "height"):
        _positive(ref[name], name)
    origin = ref["provenance"]
    if not isinstance(origin, dict) or set(origin) != _ORIGIN_FIELDS:
        raise MediaRefusal("invalid original media provenance fields")
    if not isinstance(origin["kind"], str) or origin["kind"] not in {"tool", "upload", "host", "migration"}:
        raise MediaRefusal("invalid media origin kind")
    _session(origin["session_id"])
    if not isinstance(origin["id"], str) or not origin["id"]:
        raise MediaRefusal("missing actual origin identity")
    created = _date(origin["created_at"])
    if origin["expires_at"] is not None and _date(origin["expires_at"]) < created:
        raise MediaRefusal("expiry precedes original observation")
    return copy.deepcopy(ref)


@dataclass(frozen=True)
class MediaPolicy:
    """Host-selected limits; no package-level observation quotas."""
    max_bytes: int
    max_pixels: int
    max_images: int
    retention: str = "durable"
    ttl_seconds: float | None = None

    def __post_init__(self) -> None:
        for name in ("max_bytes", "max_pixels", "max_images"):
            _positive(getattr(self, name), name)
        if self.retention not in {"durable", "request-only", "drop"}:
            raise MediaRefusal("unsupported host retention policy")
        if self.ttl_seconds is not None and (
            isinstance(self.ttl_seconds, bool)
            or not isinstance(self.ttl_seconds, (int, float))
            or not math.isfinite(self.ttl_seconds) or self.ttl_seconds <= 0
        ):
            raise MediaRefusal("invalid host media TTL")

    def restrict(self, policy: Any) -> MediaPolicy:
        if policy is None:
            return self
        if not isinstance(policy, Mapping) or set(policy) - {
            "max_bytes", "max_pixels", "max_images", "retention"
        }:
            raise MediaRefusal("unknown producer policy")
        changes = {}
        for name in ("max_bytes", "max_pixels", "max_images"):
            if name in policy:
                value = _positive(policy[name], name)
                if value > getattr(self, name):
                    raise MediaRefusal("producer cannot expand host limits")
                changes[name] = value
        rank = {"drop": 0, "request-only": 1, "durable": 2}
        if "retention" in policy:
            value = policy["retention"]
            if not isinstance(value, str) or value not in rank or rank[value] > rank[self.retention]:
                raise MediaRefusal("producer cannot expand retention")
            changes["retention"] = value
        return replace(self, **changes)


def _private_file(fd: int) -> os.stat_result:
    value = os.fstat(fd)
    if not stat.S_ISREG(value.st_mode) or value.st_nlink != 1:
        raise MediaRefusal("media descriptor is not a private regular file")
    if value.st_mode & 0o077 or value.st_uid != os.getuid():
        raise MediaRefusal("media file is not owner-private")
    return value


def _directory(parent: int, name: str, *, create: bool = True) -> int:
    if create:
        try:
            os.mkdir(name, 0o700, dir_fd=parent)
            os.fsync(parent)
        except FileExistsError:
            pass
    fd = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent)
    value = os.fstat(fd)
    if value.st_mode & 0o077 or value.st_uid != os.getuid():
        os.close(fd)
        raise MediaRefusal("media directory is not owner-private")
    return fd


def _root_descriptor(root: Path, *, create: bool = True) -> int:
    if os.name != "posix" or not hasattr(os, "O_NOFOLLOW"):
        raise MediaRefusal("media durability requires qualified POSIX descriptors")
    if not root.is_absolute() or ".." in root.parts or root == Path("/"):
        raise MediaRefusal("media root must be explicit absolute private path")
    parent = os.open("/", os.O_RDONLY | os.O_DIRECTORY)
    try:
        for part in root.parts[1:]:
            try:
                fd = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent)
            except FileNotFoundError:
                if not create:
                    raise
                os.mkdir(part, 0o700, dir_fd=parent)
                os.fsync(parent)
                fd = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent)
            os.close(parent)
            parent = fd
        value = os.fstat(parent)
        if value.st_mode & 0o077 or value.st_uid != os.getuid():
            raise MediaRefusal("media root must be owner-private")
        return parent
    except BaseException:
        os.close(parent)
        raise


class _Base64Stream:
    def __init__(self, data: str) -> None:
        self.data, self.position, self.pending, self.ended = data, 0, b"", False

    def read(self, n: int) -> bytes:
        if not self.pending and not self.ended:
            end = min(len(self.data), self.position + max(4, (n // 3) * 4))
            text = self.data[self.position:end]
            self.position, self.ended = end, end == len(self.data)
            if not self.ended and "=" in text:
                raise MediaRefusal("base64 padding before end")
            try:
                self.pending = base64.b64decode(text, validate=True)
            except (binascii.Error, ValueError) as exc:
                raise MediaRefusal("invalid strict base64 image") from exc
            if self.ended and base64.b64encode(self.pending).decode("ascii") != text:
                raise MediaRefusal("noncanonical base64 image")
        value, self.pending = self.pending[:n], self.pending[n:]
        return value


def _decode_image(stream: BinaryIO, policy: MediaPolicy) -> tuple[str, int, int]:
    try:
        from PIL import Image
    except ImportError as exc:
        raise MediaRefusal("image decoder unavailable; install the media extra") from exc
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            image = Image.open(stream)
            width, height = image.size
            if width * height > policy.max_pixels:
                raise MediaRefusal("image exceeds host pixel limit")
            if image.format not in {"PNG", "JPEG"} or getattr(image, "n_frames", 1) != 1:
                raise MediaRefusal("only static PNG/JPEG images are supported")
            mime = "image/png" if image.format == "PNG" else "image/jpeg"
            image.verify()
            stream.seek(0)
            with Image.open(stream) as decoded:
                decoded.load()
            return mime, width, height
    except MediaRefusal:
        raise
    except Exception as exc:
        raise MediaRefusal("image decoder rejected original bytes") from exc


class SessionMediaStore:
    """Explicit host-owned store. Never inherit a parent's live instance."""

    def __init__(self, root: Path | str, session_id: str, *, policy: MediaPolicy,
                 checkpoint_verifier: Callable[[Any], Any] | None = None,
                 origin_resolver: Callable[[], Any] | None = None,
                 policy_verifier: Callable[..., Any] | None = None,
                 nested_verifier: Callable[..., Any] | None = None,
                 read_only: bool = False,
                 source_read_verifier: Callable[..., Any] | None = None,
                 request_only_verifier: Callable[..., Any] | None = None) -> None:
        self.session_id = _session(session_id)
        self.policy, self.root = policy, Path(root)
        self.checkpoint_verifier, self.origin_resolver = checkpoint_verifier, origin_resolver
        self.policy_verifier, self.nested_verifier = policy_verifier, nested_verifier
        self.read_only = read_only
        self.source_read_verifier = source_read_verifier
        self.request_only_verifier = request_only_verifier
        self.epoch, self._closed = 0, False
        self._lock = threading.RLock()
        self._prepared: dict[int, Any] = {}
        self._checkpoints: dict[str, Any] = {}
        self._root = _root_descriptor(self.root, create=not read_only)
        self._dirs: dict[str, int] = {}
        self._ownership: int | None = None
        try:
            if not read_only:
                self._ownership = os.open("writer.lock", os.O_RDWR | os.O_CREAT |
                                          os.O_NOFOLLOW | os.O_NONBLOCK, 0o600, dir_fd=self._root)
                _private_file(self._ownership)
                try:
                    fcntl.flock(self._ownership, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError as exc:
                    raise MediaRefusal("media store already has an active writer") from exc
            for name in ("assets", "staging", "records", "origins"):
                self._dirs[name] = _directory(self._root, name, create=not read_only)
            owner = self._read(self._root, "owner.json")
            if owner is None:
                self._write(self._root, "owner.json", {"v": 1, "session_id": self.session_id})
            elif owner != {"v": 1, "session_id": self.session_id}:
                raise MediaRefusal("media root belongs to a different session")
        except BaseException:
            self.close()
            raise

    def _check(self) -> None:
        if self._closed:
            raise MediaRefusal("media store closed")

    def _writable(self) -> None:
        self._check()
        if self.read_only:
            raise MediaRefusal("read-only media store cannot mutate")

    def _read(self, directory: int, name: str) -> Any:
        try:
            fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
        except FileNotFoundError:
            return None
        with os.fdopen(fd, "rb") as stream:
            _private_file(stream.fileno())
            return json.load(stream)

    def _write(self, directory: int, name: str, value: Any) -> None:
        if self.read_only:
            raise MediaRefusal("read-only media store cannot mutate")
        temporary = f".{name}.{uuid.uuid4().hex}"
        fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                     0o600, dir_fd=directory)
        # Failure deliberately leaves evidence; never replay/delete uncertain writes.
        with os.fdopen(fd, "wb") as stream:
            stream.write(_json(value))
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, name, src_dir_fd=directory, dst_dir_fd=directory)
        os.fsync(directory)

    def _origin(self, origin: Any, policy: MediaPolicy) -> tuple[dict, str]:
        if origin is None:
            if self.origin_resolver is None:
                raise MediaRefusal("no trusted current-call media origin binding")
            origin = self.origin_resolver()
        if not isinstance(origin, Mapping) or set(origin) not in (
            {"kind", "session_id", "id"}, _ORIGIN_FIELDS
        ):
            raise MediaRefusal("invalid trusted media origin")
        origin = dict(origin)
        if origin["kind"] not in {"tool", "upload", "host", "migration"}:
            raise MediaRefusal("invalid media origin")
        _session(origin["session_id"])
        if origin["kind"] != "migration" and origin["session_id"] != self.session_id:
            raise MediaRefusal("origin is not bound to this session")
        if not isinstance(origin["id"], str) or not origin["id"]:
            raise MediaRefusal("real origin ID required")
        key = _key({k: origin[k] for k in ("kind", "session_id", "id")})
        prior = self._read(self._dirs["origins"], key + ".json")
        if prior:
            if set(origin) == _ORIGIN_FIELDS and origin != prior["origin"]:
                raise MediaRefusal("conflicting original provenance")
            return prior["origin"], key
        if set(origin) != _ORIGIN_FIELDS:
            origin.update(created_at=datetime.now(timezone.utc).isoformat(), expires_at=None)
        created, expires = _date(origin["created_at"]), origin["expires_at"]
        if expires is not None and _date(expires) < created:
            raise MediaRefusal("original expiry precedes creation")
        if policy.ttl_seconds is not None:
            maximum = created + timedelta(seconds=policy.ttl_seconds)
            if expires is None or _date(expires) > maximum:
                origin["expires_at"] = maximum.isoformat()
        return origin, key

    def _source(self, value: Any) -> tuple[Any, str | None]:
        if isinstance(value, (bytes, bytearray, memoryview)):
            return io.BytesIO(value), None
        if hasattr(value, "read"):
            return value, None
        if isinstance(value, Mapping):
            if set(value) <= {"stream", "mime"} and "stream" in value:
                return value["stream"], value.get("mime")
            if set(value) <= {"encoding", "data", "mime"} and value.get("encoding") == "base64":
                if not isinstance(value.get("data"), str):
                    raise MediaRefusal("base64 source must be explicitly tagged text")
                return _Base64Stream(value["data"]), value.get("mime")
        raise MediaRefusal("unsupported canonical image-bytes source")

    def _stage(self, value: Any, origin: dict, policy: MediaPolicy) -> dict:
        stream, claimed_mime = self._source(value)
        if not callable(getattr(stream, "read", None)):
            raise MediaRefusal("image source must be binary stream")
        name = uuid.uuid4().hex + ".bin"
        self._write(self._dirs["staging"], name + ".json", {
            "v": 1, "state": "staging", "staging": name, "origin": origin,
            "intended_owner": self.session_id, "ref": None,
        })
        fd = os.open(name, os.O_RDWR | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                     0o600, dir_fd=self._dirs["staging"])
        sha, length = hashlib.sha256(), 0
        with os.fdopen(fd, "w+b") as target:
            while True:
                chunk = stream.read(min(_CHUNK, policy.max_bytes - length + 1))
                if not isinstance(chunk, bytes):
                    raise MediaRefusal("image stream returned non-binary data")
                if not chunk:
                    break
                length += len(chunk)
                if length > policy.max_bytes:
                    raise MediaRefusal("image exceeds host byte limit")
                target.write(chunk)
                sha.update(chunk)
            if not length:
                raise MediaRefusal("empty image")
            target.flush()
            target.seek(0)
            mime, width, height = _decode_image(target, policy)
            if claimed_mime is not None and claimed_mime != mime:
                raise MediaRefusal("claimed MIME differs from original image")
            os.fsync(target.fileno())
        os.fsync(self._dirs["staging"])
        ref = {"v": 1, "session_id": self.session_id, "sha256": sha.hexdigest(),
               "mime": mime, "bytes": length, "width": width, "height": height,
               "provenance": copy.deepcopy(origin)}
        record_name = _key(ref) + ".json"
        prior = self._read(self._dirs["records"], record_name)
        if prior is None:
            self._write(self._dirs["records"], record_name, {
                "v": 1, "ref": ref, "state": "staged", "staging": name,
                "retention": policy.retention, "checkpoint": None,
                "intended_owner": self.session_id,
            })
        elif prior["ref"] != ref or prior["retention"] != policy.retention:
            raise MediaRefusal("conflicting staged media observation")
        self._write(self._dirs["staging"], name + ".json", {
            "v": 1, "state": "staged", "staging": name, "origin": origin,
            "intended_owner": self.session_id, "ref": ref,
        })
        return ref

    async def ingest(self, value: Any, *, source_format: str, origin: Any, policy: Any) -> dict:
        return self.ingest_sync(value, source_format=source_format, origin=origin, policy=policy)

    def ingest_sync(self, value: Any, *, source_format: str, origin: Any, policy: Any) -> dict:
        """Host-only synchronous adapter; structural capability remains async."""
        self._writable()
        with self._lock:
            selected = self.policy.restrict(policy)
            provenance, origin_key = self._origin(origin, selected)
            if selected.retention == "drop":
                raise MediaRefusal("host policy forbids retaining image bytes")
            if source_format == "image-bytes":
                refs = [self._stage(value, provenance, selected)]
                normalized = refs[0]
            elif source_format == "core-tool-result":
                normalized, refs = self._adapt_tool_result(value, provenance, selected)
            else:
                raise MediaRefusal("unknown canonical media source format")
            if len(refs) > selected.max_images:
                raise MediaRefusal("image count exceeds host limit")
            old = self._read(self._dirs["origins"], origin_key + ".json")
            if old and old["refs"] != refs and not (
                source_format == "core-tool-result"
                and all(ref in old["refs"] for ref in refs)
            ):
                raise MediaRefusal("conflicting reuse of actual media origin")
            if not old:
                self._write(self._dirs["origins"], origin_key + ".json", {
                    "v": 1, "origin": provenance, "refs": refs, "publication": None,
                })
            return {"value": normalized, "staged_refs": copy.deepcopy(refs)}

    def ingest_group_sync(self, values: Any, *, origin: Any, policy: Any) -> dict:
        """Host-only bounded whole-origin ingestion, preserving one true origin.

        Historical format owners pass an iterable of binary sources for one
        original event/call. It is not a sequence of new observations/IDs, and
        does not publish until the owner supplies the final effective ref set.
        """
        self._writable()
        with self._lock:
            selected = self.policy.restrict(policy)
            provenance, origin_key = self._origin(origin, selected)
            if selected.retention == "drop":
                raise MediaRefusal("host policy forbids retaining image bytes")
            refs = []
            for value in values:
                if len(refs) >= selected.max_images:
                    raise MediaRefusal("image group exceeds host image count limit")
                refs.append(self._stage(value, provenance, selected))
            previous = self._read(self._dirs["origins"], origin_key + ".json")
            if previous is not None and _json(previous["refs"]) != _json(refs):
                raise MediaRefusal("conflicting whole-origin media group")
            if previous is None:
                self._write(self._dirs["origins"], origin_key + ".json", {
                    "v": 1, "origin": provenance, "refs": refs, "publication": None,
                })
            return {"value": copy.deepcopy(refs), "staged_refs": copy.deepcopy(refs)}

    def _adapt_tool_result(self, value: Any, origin: dict, policy: MediaPolicy) -> tuple[Any, list]:
        from amplifier_core import ToolResult
        from amplifier_core.message_models import ImageBlock, TextBlock

        if not isinstance(value, ToolResult):
            raise MediaRefusal("core-tool-result requires actual Core ToolResult")
        result = value.model_copy(deep=True)
        refs: list[dict] = []
        output = result.output
        if isinstance(output, dict) and MEDIA_NAMESPACE in output:
            carrier = output[MEDIA_NAMESPACE]
            if not isinstance(carrier, dict) or set(carrier) != {"refs"} or not isinstance(carrier["refs"], list):
                raise MediaRefusal("invalid structured tool media carrier")
            for ref in carrier["refs"]:
                self.validate_ref(ref, committed=False, check_expiry=False)
                self._validate_origin_relation(origin, ref)
                refs.append(copy.deepcopy(ref))
        content = []
        for block in getattr(result, "content", None) or []:
            if not isinstance(block, ImageBlock):
                content.append(block)
                continue
            if len(refs) >= policy.max_images:
                raise MediaRefusal("image count exceeds host limit")
            source = block.source
            if set(source) - {"type", "media_type", "data"} or source.get("type") != "base64":
                raise MediaRefusal("only canonical inline base64 tool images supported")
            ref = self._stage({"encoding": "base64", "data": source.get("data"),
                               "mime": source.get("media_type")}, origin, policy)
            refs.append(ref)
            content.append(TextBlock(text=f"[Session image {ref['sha256']}]"))
        if refs:
            if output is None:
                output = {}
            elif not isinstance(output, dict):
                output = {"result": output}  # Never parse JSON-looking original text.
            result.output = {**output, MEDIA_NAMESPACE: {"refs": copy.deepcopy(refs)}}
        if hasattr(result, "content"):
            result.content = content or None
        return result, refs

    def _validate_origin_relation(self, origin: dict, ref: dict) -> None:
        if ref["provenance"] == origin:
            return
        if self.nested_verifier is None or self.nested_verifier(
            copy.deepcopy(origin), copy.deepcopy(ref)
        ) is not True:
            raise MediaRefusal("no dispatcher-attested parent/child media relationship")

    def validate_ref(self, ref: Any, *, committed: bool = True,
                     check_expiry: bool = True) -> dict:
        self._check()
        normalized = validate_blob_ref(ref)
        if normalized["session_id"] != self.session_id:
            raise MediaRefusal("foreign media owner")
        record = self._read(self._dirs["records"], _key(normalized) + ".json")
        if record is None or record["ref"] != normalized:
            raise MediaRefusal("unowned or forged media reference")
        if record["state"] == "dropped" or self.policy.retention == "drop":
            raise MediaRefusal("media retention revoked")
        if committed:
            if record["state"] != "committed":
                raise MediaRefusal("media lacks acknowledged canonical checkpoint")
            self.verify_checkpoint(normalized)
        expires = normalized["provenance"]["expires_at"]
        if check_expiry and expires is not None and _date(expires) <= datetime.now(timezone.utc):
            raise MediaRefusal("original image observation expired", code="expired")
        if check_expiry and self.policy_verifier is not None:
            if self.policy_verifier(copy.deepcopy(normalized), purpose="model") is not True:
                raise MediaRefusal("source owner refused media delivery", code="source_policy")
        return normalized

    def validate_published_ref(self, ref: Any, *, check_expiry: bool = False) -> dict:
        """Host-only accepted history/recovery identity, not model reachability.

        The host separately owns the selected current/recovery source and its
        export/transfer permission. Staged observations never qualify.
        """
        normalized = self.validate_ref(ref, committed=False, check_expiry=check_expiry)
        record = self._read(self._dirs["records"], _key(normalized) + ".json")
        if record["state"] not in {"published", "committed"}:
            raise MediaRefusal("media lacks accepted publication")
        if self.read_only and (
            self.source_read_verifier is None or self.source_read_verifier(
                copy.deepcopy(normalized), purpose="transfer"
            ) is not True
        ):
            raise MediaRefusal("read-only transfer lacks verified selected source")
        return normalized

    def validate_published_group(self, refs: Any, *, origin: Any) -> dict:
        """Host-only read-only check of one exact ordered retained publication.

        Individual ownership does not prove group completeness. Compare with
        the origin's actual final publication, including post-hook selection,
        without publishing, resolving pixels, renewing expiry or granting
        canonical/model reachability. Observer stores retain their independent
        selected-source guard through validate_published_ref.
        """
        self._check()
        if not isinstance(refs, list) or not refs or len(refs) > self.policy.max_images:
            raise MediaRefusal("invalid or over-limit published media group")
        if not isinstance(origin, Mapping) or set(origin) not in (
            {"kind", "session_id", "id"}, _ORIGIN_FIELDS
        ):
            raise MediaRefusal("published group requires an explicit real origin")
        supplied_origin = dict(origin)
        if (
            not isinstance(supplied_origin["kind"], str)
            or supplied_origin["kind"] not in {"tool", "upload", "host", "migration"}
            or not isinstance(supplied_origin["id"], str)
            or not supplied_origin["id"]
        ):
            raise MediaRefusal("invalid published media group origin")
        _session(supplied_origin["session_id"])
        if set(supplied_origin) == _ORIGIN_FIELDS:
            created = _date(supplied_origin["created_at"])
            expires = supplied_origin["expires_at"]
            if expires is not None and _date(expires) < created:
                raise MediaRefusal("published group expiry precedes original creation")
        identity = {key: supplied_origin[key] for key in ("kind", "session_id", "id")}
        name = _key(identity) + ".json"
        with self._lock:
            normalized = [validate_blob_ref(ref) for ref in refs]
            saved = self._read(self._dirs["origins"], name)
            if not isinstance(saved, dict) or not isinstance(saved.get("origin"), dict):
                raise MediaRefusal("published media group has no admitted origin")
            saved_origin = saved["origin"]
            if (
                set(saved_origin) != _ORIGIN_FIELDS
                or _json({key: saved_origin[key] for key in identity}) != _json(identity)
                or (set(supplied_origin) == _ORIGIN_FIELDS
                    and _json(saved_origin) != _json(supplied_origin))
            ):
                raise MediaRefusal("published media group origin differs")
            expected = {"refs": normalized, "disposition": "retain"}
            if _json(saved.get("publication")) != _json(expected):
                raise MediaRefusal("published media group differs from final retained publication")
            snapshot = _json(saved)
            # Repeat individual and source checks to catch late callback refusal
            # or publication changes while an observer has no writer lock.
            for _ in range(2):
                for ref in normalized:
                    self.validate_published_ref(ref, check_expiry=False)
            if _json(self._read(self._dirs["origins"], name)) != snapshot:
                raise MediaRefusal("published media group changed during verification")
        return {"valid": True, "ref_count": len(normalized)}

    def resolve_published_ref(self, ref: Any) -> BinaryIO:
        normalized = self.validate_published_ref(ref)
        expires = normalized["provenance"]["expires_at"]
        if expires is not None and _date(expires) <= datetime.now(timezone.utc):
            raise MediaRefusal("expired source has metadata only", code="expired")
        snapshot = self._open_verified(normalized, self._dirs["assets"], normalized["sha256"])
        try:
            self.validate_published_ref(ref)
            return snapshot
        except BaseException:
            snapshot.close()
            raise

    def _open_verified(self, ref: dict, directory: int, name: str) -> BinaryIO:
        fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
        stream = os.fdopen(fd, "rb")
        snapshot = tempfile.TemporaryFile(mode="w+b")
        try:
            before = _private_file(fd)
            if before.st_size != ref["bytes"]:
                raise MediaRefusal("original media size changed")
            sha = hashlib.sha256()
            while chunk := stream.read(_CHUNK):
                sha.update(chunk)
                snapshot.write(chunk)
            after = os.fstat(fd)
            fields = ("st_dev", "st_ino", "st_size", "st_mtime_ns", "st_ctime_ns")
            if any(getattr(before, f) != getattr(after, f) for f in fields) or sha.hexdigest() != ref["sha256"]:
                raise MediaRefusal("original media bytes changed")
            snapshot.seek(0)
            return snapshot
        except BaseException:
            snapshot.close()
            raise
        finally:
            stream.close()

    def resolve_ref(self, ref: Any, *, committed: bool = True) -> BinaryIO:
        if self.read_only:
            raise MediaRefusal("observer reads require guarded resolve_published_ref")
        normalized = self.validate_ref(ref, committed=committed)
        record = self._read(self._dirs["records"], _key(normalized) + ".json")
        directory, name = (
            (self._dirs["staging"], record["staging"])
            if record["state"] == "staged"
            else (self._dirs["assets"], normalized["sha256"])
        )
        return self._open_verified(normalized, directory, name)

    async def publish(self, refs: Any, *, origin: Any, disposition: str) -> dict:
        return self.publish_sync(refs, origin=origin, disposition=disposition)

    def publish_sync(self, refs: Any, *, origin: Any, disposition: str) -> dict:
        """Host-only synchronous publication; no event loop or tool replay."""
        self._writable()
        if disposition not in {"retain", "drop"} or not isinstance(refs, list):
            raise MediaRefusal("invalid media publication request")
        refs = [validate_blob_ref(ref) for ref in refs]
        with self._lock:
            provenance, origin_key = self._origin(origin, self.policy)
            saved = self._read(self._dirs["origins"], origin_key + ".json")
            if saved is None and not refs:
                return {"refs": [], "publication": {"state": "dropped", "durability": "none"}}
            if saved is None or any(ref not in saved["refs"] for ref in refs):
                raise MediaRefusal("publication is not staged for actual origin")
            publication = {"refs": refs, "disposition": disposition}
            if saved["publication"] is not None:
                if saved["publication"] != publication:
                    raise MediaRefusal("conflicting publication for actual media origin")
                return {"refs": copy.deepcopy(refs if disposition == "retain" else []),
                        "publication": {"state": "published" if refs and disposition == "retain" else "dropped",
                                        "durability": "assets-only" if refs and disposition == "retain" else "none"}}
            for ref in saved["refs"]:
                record_name = _key(ref) + ".json"
                record = self._read(self._dirs["records"], record_name)
                retained = disposition == "retain" and ref in refs
                if record is not None and record["state"] == "dropped" and not retained:
                    # Reconcile an identical partially persisted drop operation.
                    continue
                checked = self.validate_ref(ref, committed=False, check_expiry=False)
                self._validate_origin_relation(provenance, checked)
                if retained:
                    with self.resolve_ref(ref, committed=False) as source:
                        digest = ref["sha256"]
                        try:
                            fd = os.open(digest, os.O_WRONLY | os.O_CREAT | os.O_EXCL |
                                         os.O_NOFOLLOW, 0o600, dir_fd=self._dirs["assets"])
                        except FileExistsError:
                            with self._open_verified(ref, self._dirs["assets"], digest):
                                pass
                            self._fsync_asset(digest)
                        else:
                            with os.fdopen(fd, "wb") as target:
                                while chunk := source.read(_CHUNK):
                                    target.write(chunk)
                                target.flush()
                                os.fsync(target.fileno())
                            os.fsync(self._dirs["assets"])
                    if record["state"] != "committed":
                        record["state"] = "published"
                elif record["state"] in {"committed", "published"} and ref["provenance"] != provenance:
                    # Outer suppression cannot revoke an independently settled child.
                    continue
                elif record["state"] == "committed":
                    raise MediaRefusal("publication cannot revoke committed reference")
                else:
                    record["state"] = "dropped"
                self._write(self._dirs["records"], record_name, record)
            saved["publication"] = publication
            self._write(self._dirs["origins"], origin_key + ".json", saved)
            kept = refs if disposition == "retain" else []
            return {"refs": copy.deepcopy(kept), "publication": {
                "state": "published" if kept else "dropped",
                "durability": "assets-only" if kept else "none",
            }}

    def acknowledge_checkpoint(self, receipt: Any) -> dict:
        """Host-only canonical ACK, not a seventh capability operation."""
        self._check()
        if self.read_only:
            raise MediaRefusal("read-only media store cannot bind canonical acceptance")
        if self.checkpoint_verifier is None:
            raise MediaRefusal("host has no durable checkpoint verifier")
        # Local checkpoint authority stays in memory. Its seal/HMAC/private path
        # must never be serialized or reconstructed from a journal.
        serialized = receipt.verify() if callable(getattr(receipt, "verify", None)) else receipt
        serialized = json.loads(_json(serialized))
        if isinstance(serialized, dict) and serialized.get("session_id", self.session_id) != self.session_id:
            raise MediaRefusal("checkpoint belongs to a different canonical session")
        checkpoint_key = _key(serialized)
        count = 0
        # Disk spool bounds memory by one ref and exhausts the source verifier
        # BEFORE any acceptance is bound (its final checks may still refuse).
        with self._lock, tempfile.TemporaryFile(mode="w+b") as pending:
            for ref in self.checkpoint_verifier(receipt):
                self.validate_ref(ref, committed=False, check_expiry=False)
                record = self._read(self._dirs["records"], _key(ref) + ".json")
                if record["state"] not in {"published", "committed"}:
                    raise MediaRefusal("checkpoint references unpublished media")
                if record["retention"] != "durable":
                    raise MediaRefusal("request-only observation cannot become durable")
                expiry = ref["provenance"]["expires_at"]
                expired = expiry is not None and _date(expiry) <= datetime.now(timezone.utc)
                metadata_only = record.get("metadata_only") is True
                if metadata_only and not expired:
                    raise MediaRefusal("metadata-only adoption requires expired original")
                if not ((record["state"] == "committed" or metadata_only) and expired):
                    with self._open_verified(ref, self._dirs["assets"], ref["sha256"]):
                        pass
                pending.write(_json(ref) + b"\n")
                count += 1
            pending.seek(0)
            for line in pending:
                ref = json.loads(line)
                record = self._read(self._dirs["records"], _key(ref) + ".json")
                record.update(state="committed", checkpoint=serialized)
                self._write(self._dirs["records"], _key(ref) + ".json", record)
            self._checkpoints = {checkpoint_key: receipt}
        return {"state": "committed", "durability": "legacy-checkpoint-backed",
                "ref_count": count}

    def _fsync_asset(self, digest: str) -> None:
        """Reconciliation must finish durability barriers, not only hash bytes."""
        fd = os.open(digest, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK,
                     dir_fd=self._dirs["assets"])
        try:
            _private_file(fd)
            os.fsync(fd)
        finally:
            os.close(fd)
        os.fsync(self._dirs["assets"])

    def verify_checkpoint(self, ref: dict) -> None:
        record = self._read(self._dirs["records"], _key(ref) + ".json")
        if not record or record["state"] != "committed" or self.checkpoint_verifier is None:
            raise MediaRefusal("no verified canonical media checkpoint")
        receipt = self._checkpoints.get(_key(record["checkpoint"]))
        if receipt is None:
            raise MediaRefusal("resumed host must acknowledge current canonical source")
        # Exhaust source-verifying iterators, even after finding the wanted ref.
        found = False
        for accepted in self.checkpoint_verifier(receipt):
            found = found or _json(ref) == _json(accepted)
        if not found:
            raise MediaRefusal("media is no longer in verified canonical prefix")

    async def adopt(self, refs: list, source_store: SessionMediaStore) -> list:
        """Host-only selected-prefix copy; target publication still needs ACK."""
        return self.adopt_sync(refs, source_store)

    def adopt_sync(self, refs: list, source_store: SessionMediaStore) -> list:
        """Synchronous owner copy for synchronous fork paths; never runs a loop."""
        self._writable()
        if self.policy.retention != "durable":
            raise MediaRefusal("target policy does not allow durable adoption")
        adopted = []
        grouped: dict[str, list] = {}
        for ref in refs:
            if source_store.read_only:
                source_store.validate_published_ref(ref)
            else:
                source_store.validate_ref(ref, check_expiry=False)
            grouped.setdefault(_key(ref["provenance"]), []).append(ref)
        for group in grouped.values():
            origin = group[0]["provenance"]
            # Provenance can be foreign only through this explicit host adoption path.
            with self._lock:
                target_refs = []
                for ref in group:
                    expiry = origin["expires_at"]
                    if expiry is not None and _date(expiry) <= datetime.now(timezone.utc):
                        replacement = {**copy.deepcopy(ref), "session_id": self.session_id}
                        name = _key(replacement) + ".json"
                        previous = self._read(self._dirs["records"], name)
                        if previous is None:
                            self._write(self._dirs["records"], name, {
                                "v": 1, "ref": replacement, "state": "published",
                                "staging": None, "retention": "durable", "checkpoint": None,
                                "intended_owner": self.session_id, "metadata_only": True,
                                "source_acceptance": {"session_id": source_store.session_id,
                                                      "ref_key": _key(ref)},
                            })
                    else:
                        reader = source_store.resolve_published_ref if source_store.read_only else source_store.resolve_ref
                        with reader(ref) as stream:
                            replacement = self._stage(stream, origin, self.policy)
                    target_refs.append(replacement)
                    classification = source_store.get_origin_classification(ref)
                    if classification is not None:
                        self.classify_origin(replacement, classification=classification)
                key = _key({k: origin[k] for k in ("kind", "session_id", "id")})
                prior = self._read(self._dirs["origins"], key + ".json")
                if prior and prior["refs"] != target_refs:
                    raise MediaRefusal("conflicting selected-prefix adoption")
                if not prior:
                    self._write(self._dirs["origins"], key + ".json", {
                        "v": 1, "origin": origin, "refs": target_refs, "publication": None,
                    })
                # Publish with an internal origin path; ordinary capability origins stay local.
                self._publish_adopted(target_refs)
                adopted.extend(target_refs)
        return adopted

    def import_verified(self, ref: dict, stream: BinaryIO | None, *, receipt: Any,
                        verifier: Callable[..., Any]) -> dict:
        """Host-only package adoption; integrity is not import authority.

        The host verifier must authorize this exact original ref/package/target,
        not merely compare a supplied hash. No journal supplied by a package is
        ever installed. Target canonical reachability still requires its own ACK.
        """
        self._writable()
        original = validate_blob_ref(ref)
        if self.policy.retention != "durable" or verifier(
            copy.deepcopy(original), receipt=receipt, target_session_id=self.session_id
        ) is not True:
            raise MediaRefusal("package owner did not authorize exact media import")
        expires = original["provenance"]["expires_at"]
        def expired() -> bool:
            return expires is not None and _date(expires) <= datetime.now(timezone.utc)
        if expired():
            # A host's earlier precheck/decode can cross the original TTL before
            # this final owner boundary. Authorized metadata remains importable;
            # never read/retry the now-expired stream or renew its provenance.
            return self._import_expired_metadata(original)
        if stream is None:
            raise MediaRefusal("unexpired import requires verified original bytes")
        with self._lock:
            expected_target = {**original, "session_id": self.session_id}
            previous = self._read(self._dirs["records"], _key(expected_target) + ".json")
            if previous is not None and (
                _json(previous.get("ref")) != _json(expected_target)
                or previous.get("state") not in {"staged", "published", "committed"}
                or previous.get("retention") != "durable"
            ):
                raise MediaRefusal("import conflicts with existing target acceptance")
            if expired():
                return self._import_expired_metadata(original)
            target = self._stage(stream, original["provenance"], self.policy)
            if target != {**original, "session_id": self.session_id}:
                raise MediaRefusal("imported bytes differ from authorized original reference")
            if expired():
                return self._import_expired_metadata(original)
            self._publish_adopted([target])
            if expired():
                return self._import_expired_metadata(original)
            return target

    def _import_expired_metadata(self, original: dict) -> dict:
        """Finish an already-authorized import as terminal original metadata."""
        target = {**copy.deepcopy(original), "session_id": self.session_id}
        with self._lock:
            name = _key(target) + ".json"
            previous = self._read(self._dirs["records"], name)
            if previous is not None and (
                _json(previous.get("ref")) != _json(target)
                or previous.get("state") not in {"staged", "published", "committed"}
                or previous.get("retention") != "durable"
            ):
                raise MediaRefusal("expired import conflicts with existing acceptance")
            record = previous or {
                "v": 1, "ref": target, "staging": None, "retention": "durable",
                "checkpoint": None, "intended_owner": self.session_id,
            }
            record = {
                **record, "metadata_only": True,
                "state": "committed" if record.get("state") == "committed" else "published",
                "source_acceptance": {"session_id": original["session_id"],
                                      "ref_key": _key(original), "kind": "host-verified-package"},
            }
            # Staged/already-published original bytes are recovery evidence, not
            # live delivery. Never unlink them in an import/expiry transition.
            self._write(self._dirs["records"], name, record)
        return target

    def _publish_adopted(self, refs: list) -> None:
        for ref in refs:
            record = self._read(self._dirs["records"], _key(ref) + ".json")
            if record is None or record.get("state") not in {"staged", "published", "committed"}:
                raise MediaRefusal("adoption cannot promote dropped or absent acceptance")
            if record.get("metadata_only") is True:
                continue
            with self._open_verified(ref, self._dirs["staging"], record["staging"]) as source:
                try:
                    fd = os.open(ref["sha256"], os.O_WRONLY | os.O_CREAT | os.O_EXCL |
                                 os.O_NOFOLLOW, 0o600, dir_fd=self._dirs["assets"])
                except FileExistsError:
                    with self._open_verified(ref, self._dirs["assets"], ref["sha256"]):
                        pass
                    self._fsync_asset(ref["sha256"])
                else:
                    with os.fdopen(fd, "wb") as target:
                        while chunk := source.read(_CHUNK):
                            target.write(chunk)
                        target.flush()
                        os.fsync(target.fileno())
                    os.fsync(self._dirs["assets"])
            if record["state"] != "committed":
                record["state"] = "published"
                self._write(self._dirs["records"], _key(ref) + ".json", record)

    def journal(self):
        """Enumerable metadata-only recovery entries, no deletion or hydration."""
        self._check()
        for directory in ("records", "staging"):
            # scandir(fd) avoids materializing a corpus-size filename list.
            with os.scandir(self._dirs[directory]) as entries:
                for entry in entries:
                    if entry.name.endswith(".json") and not entry.name.startswith("."):
                        record = self._read(self._dirs[directory], entry.name)
                        if record:
                            yield record

    def classify_origin(self, origin_or_ref: dict, *, classification: str) -> None:
        """Host-only indexed source routing, never consent or model authority.

        Hosts call after actual producer admission using the original real call,
        not a model argument or marker string. Classification is immutable and
        follows original provenance through adoption. No pixel bytes live here.
        """
        with self._lock:
            self._classify_origin(origin_or_ref, classification=classification)

    def _classify_origin(self, origin_or_ref: dict, *, classification: str) -> None:
        self._writable()
        if not isinstance(classification, str) or not re.fullmatch(
            r"[a-z][a-z0-9.-]{0,100}", classification
        ):
            raise MediaRefusal("invalid source classification")
        if set(origin_or_ref) == _REF_FIELDS:
            normalized = self.validate_ref(origin_or_ref, committed=False, check_expiry=False)
            provenance = normalized["provenance"]
        else:
            provenance, key = self._origin(origin_or_ref, self.policy)
            if self._read(self._dirs["origins"], key + ".json") is None:
                raise MediaRefusal("source classification requires admitted origin")
        key = _key({k: provenance[k] for k in ("kind", "session_id", "id")})
        name = key + ".classification.json"
        value = {"v": 1, "origin": {k: provenance[k] for k in ("kind", "session_id", "id")},
                 "classification": classification}
        previous = self._read(self._dirs["origins"], name)
        if previous is not None and previous != value:
            raise MediaRefusal("source classification cannot be rebound")
        if previous is None:
            self._write(self._dirs["origins"], name, value)

    def get_origin_classification(self, ref: dict) -> str | None:
        """Host-only source routing lookup; absence is not permission."""
        normalized = self.validate_ref(ref, committed=False, check_expiry=False)
        provenance = normalized["provenance"]
        identity = {k: provenance[k] for k in ("kind", "session_id", "id")}
        record = self._read(self._dirs["origins"], _key(identity) + ".classification.json")
        if record is None:
            return None
        if record.get("origin") != identity or record.get("v") != 1:
            raise MediaRefusal("invalid source classification record")
        return record["classification"]

    def register_prepared(self, prepared: Any) -> None:
        self._check()
        self._prepared[id(prepared.request)] = prepared

    def unregister_prepared(self, prepared: Any) -> None:
        self._prepared.pop(id(prepared.request), None)

    def revoke(self) -> None:
        """Invalidate policy epoch without deleting pixels or canonical evidence."""
        self.epoch += 1
        for prepared in list(self._prepared.values()):
            prepared.release()

    def close(self) -> None:
        if self._closed:
            return
        self.revoke()
        self._closed = True
        for fd in self._dirs.values():
            os.close(fd)
        self._dirs.clear()
        if self._ownership is not None:
            os.close(self._ownership)
            self._ownership = None
        os.close(self._root)


class SessionMediaCapability:
    """Six-operation structural interface for trusted mounted consumers."""
    version = 1

    def __init__(self, store: SessionMediaStore, *, enabled: bool = False) -> None:
        self._store, self.session_id, self.enabled = store, store.session_id, enabled
        self._consumers: dict[str, dict] = {}

    async def ingest(self, value, *, source_format, origin, policy):
        if not self.enabled:
            raise MediaRefusal("compatible media graph is not enabled by host")
        return await self._store.ingest(value, source_format=source_format, origin=origin, policy=policy)

    async def publish(self, refs, *, origin, disposition):
        return await self._store.publish(refs, origin=origin, disposition=disposition)

    def validate(self, value, *, purpose, request_options=None):
        if purpose in {"count", "transport", "provider-count", "provider-send"}:
            prepared = self._store._prepared.get(id(value))
            if prepared is None or prepared.request is not value:
                raise MediaRefusal("unknown private media projection")
            return prepared.validate(request_options=request_options)
        if purpose not in {"model", "reference", "persist"}:
            raise MediaRefusal("unsupported media validation purpose")
        for ref in value if isinstance(value, list) else [value]:
            self._store.validate_ref(ref, committed=purpose != "persist")
        return {"valid": True}

    def resolve(self, ref, *, purpose):
        if purpose != "model":
            raise MediaRefusal("ordinary media resolution requires model purpose")
        return self._store.resolve_ref(ref)

    async def prepare_request(self, request, *, provider_identity, model, request_options,
                              purpose, execution_scope):
        from .media_projection import prepare_media_request
        return await prepare_media_request(
            self._store, request, provider_identity=provider_identity, model=model,
            request_options=request_options, purpose=purpose, execution_scope=execution_scope,
        )

    def report(self, origin_or_prepared, *, event, outcome, details=None):
        facts = details or {}
        if not isinstance(facts, dict) or any(
            not isinstance(k, str) or (v is not None and type(v) not in {str, bool, int, float})
            for k, v in facts.items()
        ):
            raise MediaRefusal("media reports accept scalar facts only")
        if event == "consumer-ready":
            if not isinstance(origin_or_prepared, str):
                raise MediaRefusal("consumer readiness requires component identity")
            self._consumers[origin_or_prepared] = copy.deepcopy(facts)
        receipt = {"recorded": True, "authoritative": False, "event": event,
                   "outcome": outcome, "enabled": self.enabled}
        if event == "readiness":
            receipt["consumers"] = copy.deepcopy(self._consumers)
        return receipt


class NeutralSessionMediaStore:
    """No-storage neutral projector for unqualified/text-only hosts/platforms.

    It cannot accept a ref, create files, ingest bytes or make durable claims.
    Raw unadmitted inline images become warnings through the same projector.
    """
    policy = {"retention": "drop"}
    origin_resolver = None
    nested_verifier = None
    request_only_verifier = None

    def __init__(self, session_id: str) -> None:
        self.session_id = _session(session_id)
        self.epoch = 0
        self._prepared: dict[int, Any] = {}
        self._closed = False

    def validate_ref(self, ref, *, committed=True, check_expiry=True):
        validate_blob_ref(ref)
        raise MediaRefusal("neutral host has no admitted media owner")

    def resolve_ref(self, ref, *, committed=True):
        return self.validate_ref(ref, committed=committed)

    def verify_checkpoint(self, ref):
        raise MediaRefusal("neutral host has no canonical media checkpoint")

    async def ingest(self, *args, **kwargs):
        raise MediaRefusal("neutral host does not ingest image bytes")

    async def publish(self, *args, **kwargs):
        raise MediaRefusal("neutral host does not publish media")

    def register_prepared(self, prepared):
        if self._closed:
            raise MediaRefusal("neutral media store closed")
        self._prepared[id(prepared.request)] = prepared

    def unregister_prepared(self, prepared):
        self._prepared.pop(id(prepared.request), None)

    def close(self):
        if self._closed:
            return
        self._closed = True
        self.epoch += 1
        for prepared in list(self._prepared.values()):
            prepared.release()


def bind_session_media(coordinator: Any, *, store: SessionMediaStore,
                       enabled: bool = False) -> SessionMediaCapability:
    """Public before_initialize installer; host explicitly chooses every input."""
    if coordinator.get_capability(MEDIA_NAMESPACE) is not None:
        raise MediaRefusal("coordinator already has media; do not inherit live state")
    session_id = getattr(coordinator, "session_id", store.session_id)
    if session_id != store.session_id:
        raise MediaRefusal("media installer session mismatch")
    if store.origin_resolver is None:
        def current_origin():
            task = asyncio.current_task()
            contexts = getattr(coordinator, "_tool_dispatch_contexts", {})
            context = contexts.get(task) if task is not None else None
            call_id = context.get("tool_call_id") if isinstance(context, dict) else None
            if not isinstance(call_id, str) or not call_id:
                raise MediaRefusal("no trusted current-task tool call identity")
            return {"kind": "tool", "session_id": store.session_id, "id": call_id}
        store.origin_resolver = current_origin
    if store.nested_verifier is None:
        def verify_nested(parent_origin, ref):
            verifier = coordinator.get_capability("tools.dispatch.media_refs")
            if verifier is None:
                verifier = coordinator.get_capability("session.media.nested_verifier")
            return callable(verifier) and verifier(parent_origin, ref) is True
        store.nested_verifier = verify_nested
    capability = SessionMediaCapability(store, enabled=enabled)
    coordinator.register_capability(MEDIA_NAMESPACE, capability)
    return capability