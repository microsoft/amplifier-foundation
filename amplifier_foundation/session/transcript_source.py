"""Pinned committed transcript ranges for host windows and forward/export reads.

Open is deliberately cold O(history): hash + incremental semantic verification
with disk-backed row offsets, bounded even for one huge string. Each subsequent
read checks the exact descriptor and namespace/file identities, not history.
Changed commits (including appends) require an explicit reopen, never an
unverified cache advance. This is not a live snapshot over external writers:
unexpected edits/replacements refuse before returning bytes or decoded rows.
"""

from __future__ import annotations

import hashlib
import os
import struct
import tempfile
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from ._media_conversion_parser import CHUNK, Scanner
from .transcript_encoding import transduce
from .transcript_v2 import (
    CommittedPrefix,
    CommittedTranscriptReader,
    UnsupportedTranscript,
    _decode,
    _file,
    _stamp,
)

_OFFSET = struct.Struct(">QQ")
MAX_RANGE_BYTES = 1024 * 1024
MAX_WINDOW_ROWS = 1024
DEFAULT_WINDOW_BYTES = 4 * 1024 * 1024
MAX_WINDOW_BYTES = 16 * 1024 * 1024


@dataclass(frozen=True)
class TranscriptRange:
    """Exact canonical JSON object byte range [start,end), excluding row LF."""

    index: int
    start: int
    end: int


@dataclass(frozen=True)
class TranscriptWindow:
    commit: CommittedPrefix
    start: int
    stop: int
    messages: list[dict]


class CommittedSource:
    """Created only by open_committed_source; usable inside its context.

    Index bounds are zero-based message positions (header excluded). Byte bounds
    are absolute within the selected body, including its header. Memory-bounded
    byte export uses read_range/iter_bytes; decoded window memory is explicitly
    bounded by max_bytes, with oversize individual records refused, never cut.
    """

    def __init__(self, reader, prefix, descriptor, stamp, offsets):
        self._reader = reader
        self.commit = prefix
        self._descriptor = descriptor
        self._stamp = stamp
        self._offsets = offsets
        self._active = True
        self._lock = threading.RLock()

    def _check(self):
        if not self._active:
            raise UnsupportedTranscript("Committed source is closed or invalidated")
        prefix, descriptor = self._reader._descriptor()
        if prefix != self.commit or descriptor != self._descriptor:
            raise UnsupportedTranscript("Committed source selection changed; reopen explicitly")

    @contextmanager
    def _body(self):
        # Host pages can run on worker threads; the anonymous offset stream's
        # seek/read pairs must not interleave between windows.
        with self._lock:
            try:
                self._check()
                with _file(self._reader.root / self.commit.body) as stream:
                    if _stamp(os.fstat(stream.fileno())) != self._stamp:
                        raise UnsupportedTranscript("Committed source identity changed; reopen explicitly")
                    yield stream
                    if _stamp(os.fstat(stream.fileno())) != self._stamp:
                        raise UnsupportedTranscript("Committed source changed during bounded read")
                self._check()
            except BaseException:
                self._active = False
                raise

    def read_range(self, start: int, end: int) -> bytes:
        """Read <=1MiB within verified prefix, with no per-read prefix hash."""
        if (type(start) is not int or type(end) is not int
                or not 0 <= start <= end <= self.commit.committed_bytes
                or end - start > MAX_RANGE_BYTES):
            raise ValueError("Invalid or oversized committed byte range")
        with self._body() as stream:
            stream.seek(start)
            raw = stream.read(end - start)
            if len(raw) != end - start:
                raise UnsupportedTranscript("Committed range shortened")
        return raw

    def iter_bytes(self, start: int = 0, end: int | None = None,
                   *, chunk_size: int = CHUNK) -> Iterator[bytes]:
        """Forward bounded bytes, never bytes beyond this committed boundary."""
        if end is None:
            end = self.commit.committed_bytes
        if (type(start) is not int or type(end) is not int
                or not 0 <= start <= end <= self.commit.committed_bytes
                or type(chunk_size) is not int or not 1 <= chunk_size <= MAX_RANGE_BYTES):
            raise ValueError("Invalid committed byte iterator bounds")
        # Every yielded chunk is checked before exposure, including its directory
        # namespace. A later source mutation invalidates the next read, not a
        # license to continue reading through an old open FD.
        if start == end:
            with self._body():
                pass
        while start < end:
            stop = min(end, start + chunk_size)
            yield self.read_range(start, stop)
            start = stop

    def message_range(self, index: int) -> TranscriptRange:
        """Disk-backed O(1) lookup; does not load any message body."""
        if type(index) is not int or not 0 <= index < self.commit.message_count:
            raise IndexError("Message index outside committed source")
        with self._body():
            self._offsets.seek(index * _OFFSET.size)
            raw = self._offsets.read(_OFFSET.size)
            if len(raw) != _OFFSET.size:
                raise UnsupportedTranscript("Committed source index is incomplete")
            start, end = _OFFSET.unpack(raw)
        return TranscriptRange(index, start, end)

    def window(self, start: int, limit: int, *,
               max_bytes: int = DEFAULT_WINDOW_BYTES) -> TranscriptWindow:
        """Forward decoded rows [start,start+limit), capped by caller byte budget.

        Refuses an oversized requested window rather than silently returning a
        prefix/truncated message. Hosts may use message_range+iter_bytes for a
        huge individual message. Max1024 rows/16MiB encoded bytes per call.
        """
        if (type(start) is not int or not 0 <= start <= self.commit.message_count
                or type(limit) is not int or not 0 <= limit <= MAX_WINDOW_ROWS
                or type(max_bytes) is not int or not 1 <= max_bytes <= MAX_WINDOW_BYTES):
            raise ValueError("Invalid committed window bounds")
        stop = min(self.commit.message_count, start + limit)
        oversized = False
        with self._body() as stream:
            ranges = []
            self._offsets.seek(start * _OFFSET.size)
            budget = 0
            for _ in range(start, stop):
                raw = self._offsets.read(_OFFSET.size)
                if len(raw) != _OFFSET.size:
                    raise UnsupportedTranscript("Committed source index is incomplete")
                a, b = _OFFSET.unpack(raw)
                budget += b - a
                if budget > max_bytes:
                    oversized = True
                    break
                ranges.append((a, b))
            messages = []
            if not oversized:
                for a, b in ranges:
                    stream.seek(a)
                    raw = stream.read(b - a)
                    if len(raw) != b - a:
                        raise UnsupportedTranscript("Committed message shortened")
                    messages.append(_decode(raw))
        if oversized:
            # Caller budget refusal is not source corruption. Keep the checked
            # source available for an explicit byte-range streaming fallback.
            raise ValueError("Requested window exceeds encoded byte budget; stream ranges")
        return TranscriptWindow(self.commit, start, stop, messages)

    def recent(self, limit: int, *, before: int | None = None,
               max_bytes: int = DEFAULT_WINDOW_BYTES) -> TranscriptWindow:
        """Backward recent window, chronological within the returned window."""
        if before is None:
            before = self.commit.message_count
        if (type(before) is not int or not 0 <= before <= self.commit.message_count
                or type(limit) is not int or not 0 <= limit <= MAX_WINDOW_ROWS):
            raise ValueError("Invalid recent-window bounds")
        start = max(0, before - limit)
        return self.window(start, before - start, max_bytes=max_bytes)


@contextmanager
def open_committed_source(session_dir: str | Path, *,
                          expected: CommittedPrefix | None = None) -> Iterator[CommittedSource]:
    """Cold verify once, then pin an exact checked source with disk-backed index.

    Does not materialize a largest JSON string or all records. Semantic schema
    refusal bounds match the writer transducer. The temporary index is anonymous
    rebuildable data, never transcript authority and never persisted hash state.
    """
    reader = CommittedTranscriptReader(session_dir)
    prefix, descriptor = reader._descriptor()
    if expected is not None and prefix != expected:
        raise UnsupportedTranscript("Expected committed source differs")
    source = None
    with tempfile.TemporaryFile("w+b") as offsets:
        with _file(reader.root / prefix.body) as stream:
            stamp = _stamp(os.fstat(stream.fileno()))
            digest, remaining = hashlib.sha256(), prefix.committed_bytes
            while remaining:
                raw = stream.read(min(CHUNK, remaining))
                if not raw:
                    raise UnsupportedTranscript("Committed prefix exceeds body")
                digest.update(raw)
                remaining -= len(raw)
            if digest.hexdigest() != prefix.sha256:
                raise UnsupportedTranscript("Committed source digest differs")
            # Scanner's seek/read view is explicitly capped at committed prefix.
            from .transcript_writer import _header, _PrefixView
            stream.seek(0)
            view = _PrefixView(stream, prefix.committed_bytes)
            scanner = Scanner(view)
            count, saw_header = 0, False
            for index, row in enumerate(scanner.records()):
                fields = scanner.fields(row)
                if index == 0:
                    if _decode(view_read(view, row.start, row.end, CHUNK)) != _decode(_header(prefix.generation)):
                        raise UnsupportedTranscript("Invalid committed source header")
                    saw_header = True
                else:
                    role = fields.get("role")
                    value = scanner.small(role, 32768) if role is not None and role.kind == 34 else None
                    if type(value) is not str or not value or len(value.encode("utf-8")) > 4096:
                        raise UnsupportedTranscript("Committed source requires canonical role")
                    for _ in transduce(scanner, row):
                        pass
                    offsets.write(_OFFSET.pack(row.start, row.end))
                    count += 1
            if not saw_header or count != prefix.message_count:
                raise UnsupportedTranscript("Committed source header/count differs")
            view.seek(prefix.committed_bytes - 1)
            if view.read(1) != b"\n":
                raise UnsupportedTranscript("Committed source ends inside a record")
        reader._unchanged(descriptor)
        offsets.flush()
        source = CommittedSource(reader, prefix, descriptor, stamp, offsets)
        try:
            # Recheck identity even if it changed after the initial _file exit.
            with source._body():
                pass
            yield source
        finally:
            source._active = False


def view_read(view, start, end, limit):
    if end - start > limit:
        raise UnsupportedTranscript("Oversized committed header")
    view.seek(start)
    return view.read(end - start)