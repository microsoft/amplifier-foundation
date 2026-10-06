"""Rebuildable, payload-free transcript indexes and opt-in append persistence.

The JSONL files remain portable. Indexes live only in a bounded process cache;
replacement, truncation, or an external edit invalidates them. Writers still
require the session ownership lock. An append intent makes an interrupted batch
read from the previous complete backup, including a crash on a line boundary.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
from collections import OrderedDict
from collections.abc import Callable, Iterator, Sequence
from dataclasses import dataclass
from pathlib import Path
from threading import RLock
from typing import Any

from ..io.files import _write_atomic
from .shared_state import FileStamp, file_stamp

_CACHE: OrderedDict[Path, TranscriptIndex] = OrderedDict()
_LOCK = RLock()
_MAX_ROWS = 100_000
_MAX_FILES = 64


def intent_path(path: Path) -> Path:
    return path.with_name(path.name + ".append-pending")


def encode(message: dict[str, Any]) -> bytes:
    return json.dumps(
        message, sort_keys=True, ensure_ascii=False, allow_nan=False
    ).encode()


def _digest(message: dict[str, Any]) -> bytes:
    return hashlib.sha256(encode(message)).digest()


@dataclass(frozen=True)
class Row:
    offset: int
    size: int
    digest: bytes


class TranscriptIndex(Sequence):
    """Immutable file revision with lazy bodies and small host projections.

    Projectors must return compact lookup fields, never message bodies. Callers
    use a versioned, policy-specific key; projection results are read-only.
    """

    def __init__(self, path: Path, stamp: FileStamp, rows: list[Row], terminated: bool):
        self.path, self.stamp, self.rows = path, stamp, rows
        self.terminated = terminated
        self._projections: dict[str, list[Any]] = {}
        self._projection_lock = RLock()

    def __len__(self) -> int:
        return len(self.rows)

    def check(self) -> None:
        if file_stamp(self.path) != self.stamp:
            raise ValueError("Session history changed during indexed read")

    def read_positions(self, positions) -> list[dict[str, Any]]:
        from .history import _decode

        self.check()
        result = []
        with self.path.open("rb") as stream:
            for position in positions:
                row = self.rows[position]
                stream.seek(row.offset)
                value = _decode(stream.read(row.size))
                result.append(value)
        self.check()
        return result

    def __getitem__(self, key):
        if isinstance(key, slice):
            return self.read_positions(range(*key.indices(len(self))))
        return self.read_positions([key])[0]

    def __iter__(self) -> Iterator[dict[str, Any]]:
        from .history import _decode

        self.check()
        with self.path.open("rb") as stream:
            for row in self.rows:
                stream.seek(row.offset)
                yield _decode(stream.read(row.size))
        self.check()

    def project(
        self, key: str, projector: Callable[[dict[str, Any], int], Any]
    ) -> list[Any]:
        with self._projection_lock:
            self.check()
            values = self._projections.setdefault(key, [])
            if len(values) < len(self):
                # Stream missing rows; do not materialize the full payload set.
                from .history import _decode

                additions = []
                with self.path.open("rb") as stream:
                    for position, row in enumerate(
                        self.rows[len(values) :], len(values)
                    ):
                        stream.seek(row.offset)
                        additions.append(
                            projector(_decode(stream.read(row.size)), position)
                        )
                self.check()
                values.extend(additions)
            return values


def _remember(index: TranscriptIndex) -> TranscriptIndex:
    with _LOCK:
        _CACHE[index.path] = index
        _CACHE.move_to_end(index.path)
        while (
            len(_CACHE) > _MAX_FILES
            or sum(len(item) for item in _CACHE.values()) > _MAX_ROWS
        ):
            _CACHE.popitem(last=False)
    return index


def indexed(path: Path) -> TranscriptIndex:
    from .history import _decode, _InvalidFile, _valid_message

    path = path.absolute()
    stamp = file_stamp(path)
    if stamp is None:
        raise FileNotFoundError(path)
    with _LOCK:
        cached = _CACHE.get(path)
        if cached is not None and cached.stamp == stamp:
            _CACHE.move_to_end(path)
            return cached
    # External writers can edit a prefix while growing the file. Only our own
    # verified append may extend an index; size growth alone is not evidence.
    rows = []
    terminated = True
    with path.open("rb") as stream:
        offset = 0
        for number, raw in enumerate(stream, 1):
            terminated = raw.endswith(b"\n")
            if raw.strip():
                try:
                    value = _decode(raw)
                    if not _valid_message(value):
                        raise ValueError()
                    rows.append(Row(offset, len(raw), _digest(value)))
                except (ValueError, UnicodeError):
                    raise _InvalidFile(number) from None
            offset += len(raw)
    index = TranscriptIndex(path, stamp, rows, terminated)
    index.check()
    return _remember(index)


def _sync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _append(path: Path, chunks: Iterator[bytes]) -> None:
    # No buffered partial JSON records. A pending intent still covers a crash
    # between complete rows of the same batch.
    with path.open("ab", buffering=0) as stream:
        for chunk in chunks:
            view = memoryview(chunk)
            while view:
                written = stream.write(view)
                if not written:
                    raise OSError("Short transcript append")
                view = view[written:]
        os.fsync(stream.fileno())


def _copy_backup(source: Path, target: Path) -> None:
    """Establish a backup once, using bounded buffers and atomic replacement."""
    descriptor, name = tempfile.mkstemp(prefix=".transcript-backup-", dir=target.parent)
    try:
        with os.fdopen(descriptor, "wb") as output, source.open("rb") as input_file:
            shutil.copyfileobj(input_file, output, length=1024 * 1024)
            output.flush()
            os.fsync(output.fileno())
        os.replace(name, target)
    finally:
        Path(name).unlink(missing_ok=True)


def append_messages(path: Path, messages: list[dict[str, Any]]) -> bool:
    """Append/no-op if the entire saved prefix matches; otherwise return False.

    The caller validates incoming metadata before this operation. A False result
    requests its existing atomic replacement path (edits, compaction, recovery).
    Backup remains the previous complete transcript without copying its prefix
    on every save. This path is opt-in until hosts use intent-aware readers.
    """
    from .history import _InvalidFile

    pending = intent_path(path)
    if pending.exists():
        return False
    try:
        current = indexed(path)
    except (FileNotFoundError, _InvalidFile):
        return False
    if not current.terminated or len(messages) < len(current):
        return False
    tail = []
    for position, value in enumerate(messages):
        raw = encode(value)
        if position < len(current):
            if hashlib.sha256(raw).digest() != current.rows[position].digest:
                return False
        else:
            tail.append(raw + b"\n")
    current.check()
    if not tail:
        return True
    backup = path.with_suffix(path.suffix + ".backup")
    try:
        old = indexed(backup)
    except (FileNotFoundError, _InvalidFile):
        old = None
    if old is not None and (old.stamp.dev, old.stamp.ino) == (
        current.stamp.dev,
        current.stamp.ino,
    ):
        # Replacing a hardlinked backup changes the primary's ctime. Use the
        # ordinary atomic primary replacement path instead of mutating it.
        return False
    if (
        old is not None
        and old.terminated
        and (old.stamp.dev, old.stamp.ino) != (current.stamp.dev, current.stamp.ino)
        and len(old) <= len(current)
        and all(a.digest == b.digest for a, b in zip(old.rows, current.rows))
    ):
        start = (
            current.rows[len(old)].offset
            if len(old) < len(current)
            else current.stamp.size
        )

        def previous_tail():
            with path.open("rb") as stream:
                stream.seek(start)
                while chunk := stream.read(1024 * 1024):
                    yield chunk

        _append(backup, previous_tail())
        backup_rows = old.rows + [
            Row(old.stamp.size + row.offset - start, row.size, row.digest)
            for row in current.rows[len(old) :]
        ]
    else:
        # First append or changed CLI history: establish one complete backup.
        _copy_backup(path, backup)
        backup_rows = list(current.rows)
    backup_stamp = file_stamp(backup)
    assert backup_stamp is not None
    _remember(TranscriptIndex(backup.absolute(), backup_stamp, backup_rows, True))
    current.check()
    _write_atomic(pending, '{"version":1}\n')
    with pending.open("rb") as stream:
        os.fsync(stream.fileno())
    _sync_directory(path.parent)
    # Failures deliberately retain intent + complete backup for read recovery.
    _append(path, iter(tail))
    pending.unlink()
    _sync_directory(path.parent)
    stamp = file_stamp(path)
    assert stamp is not None
    rows = list(current.rows)
    offset = current.stamp.size
    for raw in tail:
        rows.append(Row(offset, len(raw), hashlib.sha256(raw[:-1]).digest()))
        offset += len(raw)
    updated = TranscriptIndex(current.path, stamp, rows, True)
    with current._projection_lock:
        updated._projections = {
            key: list(values) for key, values in current._projections.items()
        }
    _remember(updated)
    return True
