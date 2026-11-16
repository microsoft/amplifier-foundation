"""Exact, legacy-checkpoint-backed media reachability acknowledgements.

This module does not save history, repair it, or introduce a transcript layout.
The host calls it *after* its settled canonical save, while holding session write
ownership. A receipt attests to the actual primary, never a recovered backup or
an event/report claiming that a save happened. It is deliberately not portable
authority: a resumed process must acknowledge the actual saved primary again.

The compatibility API scans the saved prefix (O(history)), with at most one
decoded row resident. It is not the future bounded-append writer or a guarantee
against a non-cooperating writer. POSIX file/directory fsync is supported here;
Windows is explicitly refused until its native acknowledgement path qualifies.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import stat
from collections.abc import Callable, Iterable, Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass, field
from itertools import zip_longest
from pathlib import Path
from typing import Any

from .jsonl import _validate_windows_policy

_SEAL = object()
_KEY = os.urandom(32)
_MISSING = object()
_MEDIA = "experimental.media.v1"
_BLOCK = 64 * 1024


class CheckpointRefused(ValueError):
    """The actual primary could not attest to the expected canonical rows."""


def _stamp(info: os.stat_result) -> tuple[int, ...]:
    return (
        info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns
    )


def _absolute(path: str | Path) -> Path:
    # Do not normalize link/.. away before the no-follow ancestor walk.
    if ".." in Path(path).parts:
        raise CheckpointRefused("Parent traversal is not a canonical checkpoint path")
    return Path(os.path.abspath(path))


@contextmanager
def _primary(session_dir: Path, *, writable: bool = False,
             _filename: str = "transcript.jsonl", _pending: bool = True):
    """Pin lexical ancestors and open the regular primary without following links."""
    if os.name != "posix":
        raise CheckpointRefused("Legacy checkpoint acknowledgement is POSIX-only")
    absolute = _absolute(session_dir)
    descriptors = []
    stream = None
    try:
        flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
        directory = os.open(absolute.anchor, flags)
        descriptors.append(directory)
        for component in absolute.parts[1:]:
            directory = os.open(component, flags, dir_fd=directory)
            descriptors.append(directory)
        # An unresolved append can have a perfectly parseable complete-line tail.
        # Never fall through to primary or backup recovery for an acknowledgement.
        if _pending:
            try:
                os.stat("transcript.jsonl.append-pending", dir_fd=directory,
                        follow_symlinks=False)
            except FileNotFoundError:
                pass
            else:
                raise CheckpointRefused("Unresolved legacy transcript append")
        descriptor = os.open(
            _filename,
            (os.O_RDWR if writable else os.O_RDONLY) | os.O_NOFOLLOW | os.O_NONBLOCK,
            dir_fd=directory,
        )
        stream = os.fdopen(descriptor, "rb", buffering=0)
        if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
            raise CheckpointRefused("Canonical transcript is not a regular file")
        yield stream, descriptors
        if _pending:
            try:
                os.stat("transcript.jsonl.append-pending", dir_fd=directory,
                        follow_symlinks=False)
            except FileNotFoundError:
                pass
            else:
                raise CheckpointRefused("Unresolved legacy transcript append")
        # Opening by a pinned directory is not enough: reject renamed/replaced
        # ancestors and leaf entries, not only changes to an old open inode.
        if _stamp(os.stat(_filename, dir_fd=directory,
                          follow_symlinks=False)) != _stamp(os.fstat(stream.fileno())):
            raise CheckpointRefused("Canonical primary changed during acknowledgement")
        for index in range(1, len(descriptors)):
            name = absolute.parts[index]
            linked = os.stat(name, dir_fd=descriptors[index - 1],
                             follow_symlinks=False)
            opened = os.fstat(descriptors[index])
            if (linked.st_dev, linked.st_ino) != (opened.st_dev, opened.st_ino):
                raise CheckpointRefused("Canonical directory changed during acknowledgement")
    except (OSError, UnicodeError) as error:
        raise CheckpointRefused("Canonical checkpoint could not be verified") from error
    finally:
        if stream is not None:
            stream.close()
        for descriptor in reversed(descriptors):
            os.close(descriptor)


def _decode(raw: bytes) -> dict[str, Any]:
    def invalid(_: str):
        raise ValueError("Non-finite JSON")

    # Duplicate keys are not an exact, unambiguous owner record.
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError("Duplicate JSON key")
            result[key] = value
        return result

    try:
        value = json.loads(raw, parse_constant=invalid, object_pairs_hook=pairs)
        if (not isinstance(value, dict)
                or not isinstance(value.get("role"), str) or not value["role"]):
            raise ValueError()
        return value
    except (ValueError, UnicodeError):
        raise CheckpointRefused("Invalid canonical checkpoint row") from None


def _rows(stream) -> Iterator[tuple[bytes, dict[str, Any] | None]]:
    for raw in stream:
        yield raw, _decode(raw) if raw.strip() else None


def _canonical(value: Any) -> bytes:
    # Python equality alone equates true with 1 and loses the exact ref version.
    try:
        return json.dumps(
            value, sort_keys=True, ensure_ascii=False, allow_nan=False,
            separators=(",", ":"),
        ).encode("utf-8")
    except (ValueError, TypeError, UnicodeError):
        raise CheckpointRefused("Expected checkpoint rows must be canonical JSON") from None


def _messages(stream):
    for _, message in _rows(stream):
        if message is not None:
            yield message


@dataclass(frozen=True)
class LegacyCheckpointReceipt:
    """In-memory source-bound proof; serialized fields cannot recreate authority."""

    session_id: str
    prefix_bytes: int
    message_count: int
    sha256: str
    _session_dir: Path = field(repr=False)
    _source_stamp: tuple[int, ...] = field(repr=False)
    _seal: object = field(repr=False)
    _attestation: str = field(repr=False)
    kind: str = field(default="legacy-checkpoint-backed-v1", init=False)
    _operation_dir: Path | None = field(default=None, repr=False)
    _asset_reader: Callable | None = field(default=None, repr=False, compare=False)

    def verify(self) -> dict[str, Any]:
        """Recheck the exact primary before using its reachability acknowledgement."""
        if (self._seal is not _SEAL
                or not hmac.compare_digest(self._attestation, _attest(self))):
            raise CheckpointRefused("Not a local canonical checkpoint acknowledgement")
        with _primary(self._session_dir) as (stream, _):
            before = _stamp(os.fstat(stream.fileno()))
            if before != self._source_stamp:
                raise CheckpointRefused("Canonical checkpoint source changed")
            digest = hashlib.sha256()
            while chunk := stream.read(_BLOCK):
                digest.update(chunk)
            if (digest.hexdigest() != self.sha256
                    or _stamp(os.fstat(stream.fileno())) != before):
                raise CheckpointRefused("Canonical checkpoint content changed")
        return {
            "kind": self.kind, "session_id": self.session_id,
            "prefix_bytes": self.prefix_bytes, "message_count": self.message_count,
            "sha256": self.sha256,
        }

    def contains(self, ref: Mapping[str, Any]) -> bool:
        """Whether this exact ref occurs in this primary's canonical media namespace."""
        wanted = _canonical(ref)
        found = False
        for item in self.iter_refs():
            found = found or _canonical(item) == wanted
        return found

    def iter_refs(self) -> Iterator[dict[str, Any]]:
        """Stream canonical ref data after verification, without a second ref index.

        Exhaust the iterator to verify that no source changed during the scan.
        Ref shape, origin, policy and asset validation remain the media owner's
        responsibility. This method never promotes nested/stringified carriers.
        """
        self.verify()
        if self._operation_dir is not None:
            from .media_conversion import iter_verified_candidate_refs

            yield from iter_verified_candidate_refs(
                self._operation_dir,
                installed=self._session_dir / "transcript.jsonl",
                session_id=self.session_id,
                asset_reader=self._asset_reader,
            )
            self.verify()
            return
        with _primary(self._session_dir) as (stream, _):
            if _stamp(os.fstat(stream.fileno())) != self._source_stamp:
                raise CheckpointRefused("Canonical checkpoint source changed")
            for message in _messages(stream):
                metadata = message.get("metadata")
                envelope = metadata.get(_MEDIA) if isinstance(metadata, dict) else None
                refs = envelope.get("refs") if isinstance(envelope, dict) else None
                if isinstance(refs, list):
                    for item in refs:
                        if not isinstance(item, dict):
                            raise CheckpointRefused("Invalid canonical media reference")
                        yield item
            if _stamp(os.fstat(stream.fileno())) != self._source_stamp:
                raise CheckpointRefused("Canonical checkpoint source changed")


def verified_checkpoint_refs(receipt: LegacyCheckpointReceipt) -> Iterator[dict[str, Any]]:
    """Host verifier callback for SessionMediaStore; never accepts a report/dict."""
    if type(receipt) is not LegacyCheckpointReceipt or receipt._seal is not _SEAL:
        raise CheckpointRefused("Not a local canonical checkpoint acknowledgement")
    yield from receipt.iter_refs()


def _attest(receipt: LegacyCheckpointReceipt) -> str:
    fields = [
        receipt.kind, receipt.session_id, receipt.prefix_bytes,
        receipt.message_count, receipt.sha256, str(receipt._session_dir),
        receipt._source_stamp,
        str(receipt._operation_dir) if receipt._operation_dir is not None else None,
        id(receipt._asset_reader) if receipt._asset_reader is not None else None,
    ]
    return hmac.new(_KEY, _canonical(fields), hashlib.sha256).hexdigest()


def acknowledge_legacy_checkpoint(
    session_dir: str | Path, *, session_id: str,
    expected_messages: Iterable[dict[str, Any]], windows_policy: str = "legacy",
) -> LegacyCheckpointReceipt:
    """Flush and acknowledge an exact already-saved, settled canonical snapshot.

    The caller supplies the *effective saved* rows after host filtering and
    redaction, not the pre-hook result or an event-log projection. No transcript,
    backup, metadata or recovery file is created, replaced, truncated or deleted.
    Mismatch/failure forbids a durable media claim; it never reruns the tool.
    """
    import re

    _validate_windows_policy(windows_policy)
    if not isinstance(session_id, str) or not re.fullmatch(
        r"[A-Za-z0-9][A-Za-z0-9_.-]{0,220}", session_id
    ):
        raise CheckpointRefused("Invalid canonical session identity")
    path = _absolute(session_dir)
    with _primary(path, writable=True) as (stream, directories):
        before = _stamp(os.fstat(stream.fileno()))
        actual = _messages(stream)
        count = 0
        for saved, expected in zip_longest(actual, expected_messages, fillvalue=_MISSING):
            if saved is _MISSING or expected is _MISSING:
                raise CheckpointRefused("Canonical checkpoint row count differs")
            if _canonical(saved) != _canonical(expected):
                raise CheckpointRefused("Canonical checkpoint rows differ")
            count += 1
        stream.seek(0)
        digest = hashlib.sha256()
        while chunk := stream.read(_BLOCK):
            digest.update(chunk)
        if _stamp(os.fstat(stream.fileno())) != before:
            raise CheckpointRefused("Canonical checkpoint source changed")
        os.fsync(stream.fileno())
        # Flush the leaf and each newly linked ancestor without guessing which
        # directories the host just created. This changes no payload or layout.
        for descriptor in reversed(directories):
            os.fsync(descriptor)
        if _stamp(os.fstat(stream.fileno())) != before:
            raise CheckpointRefused("Canonical checkpoint source changed during flush")
        receipt = LegacyCheckpointReceipt(
            session_id, before[2], count, digest.hexdigest(), path, before, _SEAL, ""
        )
        object.__setattr__(receipt, "_attestation", _attest(receipt))
    return receipt


def acknowledge_candidate_checkpoint(
    operation_dir: str | Path, session_dir: str | Path, *,
    session_id: str, asset_reader: Callable,
) -> LegacyCheckpointReceipt:
    """Bounded acknowledgement of an actually installed canonical v1 candidate.

    This is the historical format owner's path, not an arbitrary hash/report
    acknowledgement. Its exact verified candidate, manifest, plan, originals,
    record/ref/slot closure and authorized assets are checked by the existing
    conversion adapter. The caller holds actual writer/recovery ownership and
    has already installed the candidate; this function neither installs it nor
    performs a second save. Unbounded individual strings are never decoded here.
    """
    from .media_conversion import iter_verified_candidate_refs
    import re

    if (not isinstance(session_id, str)
            or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,220}", session_id)):
        raise CheckpointRefused("Invalid canonical session identity")
    if not callable(asset_reader):
        raise CheckpointRefused("Historical checkpoint requires an authorized asset reader")
    path, operation = _absolute(session_dir), _absolute(operation_dir)
    # Bound and pin the small candidate receipt. It remains data; only the
    # format-owner verifier below can establish its relationship to actual bytes.
    with _primary(operation, _filename="verified.json", _pending=False) as (source, _):
        raw = source.read(16385)
    if len(raw) > 16384:
        raise CheckpointRefused("Historical verification receipt is oversized")
    try:
        facts = json.loads(raw)
        digest, size, count = facts["sha256"], facts["bytes"], facts["records"]
        if (type(size) is not int or size < 0
                or type(count) is not int or count < 0
                or not isinstance(digest, str) or not re.fullmatch("[0-9a-f]{64}", digest)):
            raise ValueError()
    except (ValueError, TypeError, KeyError, UnicodeError):
        raise CheckpointRefused("Invalid historical verification receipt") from None
    with _primary(path, writable=True) as (stream, directories):
        before = _stamp(os.fstat(stream.fileno()))
        for _ in iter_verified_candidate_refs(
            operation, installed=path / "transcript.jsonl",
            session_id=session_id, asset_reader=asset_reader,
        ):
            pass  # Exhaust before any acknowledgement, including final checks.
        with _primary(operation, _filename="verified.json", _pending=False) as (source, _):
            if source.read(16385) != raw:
                raise CheckpointRefused("Historical verification receipt changed")
        observed = hashlib.sha256()
        while chunk := stream.read(_BLOCK):
            observed.update(chunk)
        if before[2] != size or observed.hexdigest() != digest:
            raise CheckpointRefused("Installed historical candidate differs")
        os.fsync(stream.fileno())
        for directory in reversed(directories):
            os.fsync(directory)
        if _stamp(os.fstat(stream.fileno())) != before:
            raise CheckpointRefused("Canonical checkpoint source changed during flush")
        receipt = LegacyCheckpointReceipt(
            session_id, size, count, digest, path, before, _SEAL, "",
            _operation_dir=operation, _asset_reader=asset_reader,
        )
        object.__setattr__(receipt, "_attestation", _attest(receipt))
    return receipt