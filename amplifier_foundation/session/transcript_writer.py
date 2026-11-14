"""Opt-in POSIX committed-prefix writer; no host activation or implicit adoption.

The caller MUST hold a real host-owned writer-quiescence fence covering *all*
writers (including old nonparticipants) and supply asset admission/publication
which fsyncs every newly reachable asset before returning. A journal is neither
that fence nor permission to migrate. No default fence/admission is provided.

Normal append accepts only a settled suffix and an exact previous commit
witness. Cold verification is O(history); warm append hashes/serializes O(suffix).
Public resume still materializes messages in CommittedTranscriptReader.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
import tempfile
import weakref
from collections.abc import Callable, Iterable
from contextlib import AbstractContextManager, contextmanager
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, BinaryIO
from uuid import uuid4

from ._media_conversion_parser import CHUNK, Scanner
from .transcript_encoding import canonical_bytes, canonical_records, transduce
from .transcript_v2 import (
    _FORMAT,
    _IDENTITY,
    CommittedPrefix,
    CommittedTranscriptReader,
    UnsupportedTranscript,
    _decode,
    _stamp,
    _validate_body_header,
)

try:
    import fcntl
except ImportError:  # Importable on unqualified platforms; no writes admitted.
    fcntl = None

Fence = Callable[[Path], AbstractContextManager[Callable[[], None]]]
Admission = Callable[[], None]
LEGACY = ("transcript.jsonl", "transcript.jsonl.backup")
FEATURES = ["committed-prefix-v2", "session-media-v1"]


class TranscriptConflict(UnsupportedTranscript):
    """Expected source/commit or operation identity no longer matches."""


@dataclass(frozen=True)
class SourceWitness:
    dev: int
    ino: int
    size: int
    mtime_ns: int
    ctime_ns: int
    sha256: str


@dataclass(frozen=True)
class CommitAck:
    commit: CommittedPrefix
    reconciled: bool = False


def _identity(value: str) -> str:
    if type(value) is not str or not _IDENTITY.fullmatch(value) or len(value) > 180:
        raise TranscriptConflict("Invalid operation/session identity")
    return value


def _small(value: Any) -> bytes:
    raw = b"".join(canonical_bytes(value)) + b"\n"
    if len(raw) > CHUNK:
        raise TranscriptConflict("Oversized control record")
    return raw


def _descriptor(prefix: CommittedPrefix) -> bytes:
    return _small(dict(asdict(prefix), format=_FORMAT, v=2, min_reader=FEATURES))


def _header(generation: str) -> bytes:
    return _small({"format": _FORMAT, "v": 2, "generation": generation, "min_reader": FEATURES})


def _receipt(operation, operation_id, session_id):
    """A historical ack is authority only with matching, validated witnesses."""
    raw = operation.read("published.json")
    if raw != operation.read("result.json"):
        raise TranscriptConflict("Published/result operation witnesses differ")
    data = _decode(raw)
    if not isinstance(data, dict):
        raise TranscriptConflict("Invalid operation receipt")
    try:
        prefix = CommittedPrefix(**{
            key: data[key] for key in CommittedPrefix.__dataclass_fields__
        })
    except (KeyError, TypeError):
        raise TranscriptConflict("Incomplete operation receipt") from None
    if (prefix.operation_id != operation_id or prefix.session_id != session_id
            or any(type(getattr(prefix, key)) is not str
                   or not _IDENTITY.fullmatch(getattr(prefix, key))
                   for key in ("session_id", "generation", "operation_id"))
            or prefix.body != f"g-{prefix.generation}.jsonl"
            or any(type(getattr(prefix, key)) is not int or getattr(prefix, key) < 0
                   for key in ("sequence", "committed_bytes", "message_count"))
            or type(prefix.sha256) is not str
            or not re.fullmatch("[0-9a-f]{64}", prefix.sha256)
            or _descriptor(prefix) != raw):
        raise TranscriptConflict("Invalid operation receipt identity/content")
    return prefix


class _Dir:
    """Pinned no-follow directory-relative I/O. Never opens a supplied link."""

    def __init__(self, fd: int):
        self.fd = fd

    @classmethod
    @contextmanager
    def open(cls, path: Path):
        if os.name != "posix" or ".." in path.parts:
            raise TranscriptConflict("POSIX non-symlink absolute namespace required")
        path = path.absolute()
        links = []
        current = cls(os.open(path.anchor, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW))
        try:
            for name in path.parts[1:]:
                child = current.child(name)
                links.append((current, name, child))
                current = child
            yield current
            for parent, name, child in links:
                parent.check_child(name, child)
        finally:
            for parent, _, _ in links:
                os.close(parent.fd)
            os.close(current.fd)

    def check_child(self, name, child):
        a = os.stat(name, dir_fd=self.fd, follow_symlinks=False)
        b = os.fstat(child.fd)
        if (a.st_dev, a.st_ino) != (b.st_dev, b.st_ino):
            raise TranscriptConflict("Directory namespace replaced")

    def exists(self, name):
        try:
            os.stat(name, dir_fd=self.fd, follow_symlinks=False)
            return True
        except FileNotFoundError:
            return False

    def child(self, name):
        return _Dir(os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                            dir_fd=self.fd))

    @contextmanager
    def directory(self, name, *, create=False):
        if create and not self.exists(name):
            os.mkdir(name, mode=0o700, dir_fd=self.fd)
        if create:
            # Retry may observe mkdir's effect after its parent fsync failed.
            # Re-establish the namespace durability obligation even when the
            # directory already exists; child fsync cannot stand in for this.
            self.sync()
        child = self.child(name)
        try:
            yield child
            self.check_child(name, child)
        finally:
            os.close(child.fd)

    def sync(self):
        os.fsync(self.fd)

    @contextmanager
    def file(self, name, flags=os.O_RDONLY, *, mode="rb"):
        fd = os.open(name, flags | os.O_NOFOLLOW | os.O_NONBLOCK,
                     0o600, dir_fd=self.fd)
        try:
            info = os.fstat(fd)
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                raise TranscriptConflict("Expected an unaliased regular file")
            stream = os.fdopen(fd, mode)
            fd = -1
            with stream:
                yield stream
                linked = os.stat(name, dir_fd=self.fd, follow_symlinks=False)
                pinned = os.fstat(stream.fileno())
                if (linked.st_dev, linked.st_ino) != (pinned.st_dev, pinned.st_ino):
                    raise TranscriptConflict("File namespace replaced")
        finally:
            if fd != -1:
                os.close(fd)

    def read(self, name):
        with self.file(name) as stream:
            before = _stamp(os.fstat(stream.fileno()))
            raw = stream.read(CHUNK + 1)
            if len(raw) > CHUNK or _stamp(os.fstat(stream.fileno())) != before:
                raise TranscriptConflict("Changed/oversized control file")
            return raw

    def new(self, name, raw):
        with self.file(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL, mode="wb") as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        self.sync()

    def once(self, name, raw):
        if self.exists(name):
            if self.read(name) != raw:
                raise TranscriptConflict("Operation identity reused for different input")
            # A preceding fsync may have failed after creating the exact file.
            with self.file(name) as stream:
                os.fsync(stream.fileno())
            self.sync()
        else:
            self.new(name, raw)

    def replace(self, name, raw, *, expected, check):
        temporary = f".pointer-{uuid4().hex}"
        self.new(temporary, raw)
        check()
        actual = self.read(name) if self.exists(name) else None
        if actual != expected:
            raise TranscriptConflict("Descriptor CAS witness changed")
        os.replace(temporary, name, src_dir_fd=self.fd, dst_dir_fd=self.fd)
        self.sync()


class _PrefixView:
    """Seekable bounded view; scanner may never inspect an uncommitted tail."""

    def __init__(self, stream, limit):
        self.stream, self.limit = stream, limit

    def tell(self):
        return self.stream.tell()

    def seek(self, offset):
        if not 0 <= offset <= self.limit:
            raise TranscriptConflict("Prefix seek outside commit")
        return self.stream.seek(offset)

    def read(self, size=CHUNK):
        return self.stream.read(min(size, self.limit - self.tell()))


def _digest(stream, limit=None):
    digest, count = hashlib.sha256(), 0
    while limit is None or count < limit:
        raw = stream.read(CHUNK if limit is None else min(CHUNK, limit - count))
        if not raw:
            break
        count += len(raw)
        digest.update(raw)
    if limit is not None and count != limit:
        raise TranscriptConflict("Committed prefix exceeds body")
    return digest, count


def _source(directory, name) -> SourceWitness | None:
    if not directory.exists(name):
        return None
    with directory.file(name) as stream:
        before = _stamp(os.fstat(stream.fileno()))
        digest, _ = _digest(stream)
        if _stamp(os.fstat(stream.fileno())) != before:
            raise TranscriptConflict("Source changed while hashing")
        return SourceWitness(*before, digest.hexdigest())


def observe_source(session_dir: str | Path, name: str) -> SourceWitness | None:
    """Read-only observation, not writer exclusion or migration permission."""
    if name not in LEGACY:
        raise TranscriptConflict("Unknown legacy entry")
    with _Dir.open(Path(session_dir)) as directory:
        return _source(directory, name)


def _same_source(actual, expected, *, moved=False):
    if actual == expected:
        return
    # An owned rename may change ctime; all other identity+hash fields match.
    if (moved and actual is not None and expected is not None
            and asdict(actual) | {"ctime_ns": expected.ctime_ns} == asdict(expected)):
        return
    raise TranscriptConflict("Legacy source witness changed")


def _validate_prefix(stream, prefix):
    view = _PrefixView(stream, prefix.committed_bytes)
    scanner = Scanner(view)
    count = 0
    for index, row in enumerate(scanner.records()):
        if index == 0:
            scanner.fields(row)  # Also reject duplicate header keys.
            _validate_body_header(scanner.small(row), prefix.generation)
        else:
            fields = scanner.fields(row)
            role = fields.get("role")
            value = scanner.small(role, 32768) if role is not None and role.kind == 34 else None
            if type(value) is not str or not value or len(value.encode("utf-8")) > 4096:
                raise TranscriptConflict("Canonical message requires role")
            # Visit all fields recursively: reject duplicate keys/nonfinite values
            # without ever materializing a large scalar.
            for _ in transduce(scanner, row):
                pass
            count += 1
    if count != prefix.message_count or prefix.committed_bytes == 0:
        raise TranscriptConflict("Missing header/message count mismatch")
    view.seek(prefix.committed_bytes - 1)
    if view.read(1) != b"\n":
        raise TranscriptConflict("Committed prefix ends in a torn row")


def _candidate_prefix(prefix):
    """An explicit selector is checked input, never candidate/ownership authority."""
    if type(prefix) is not CommittedPrefix:
        raise TypeError("Exact serialized candidate CommittedPrefix required")
    for key in ("session_id", "generation", "operation_id"):
        _identity(getattr(prefix, key))
    if (prefix.body != f"g-{prefix.generation}.jsonl"
            or any(type(getattr(prefix, key)) is not int or getattr(prefix, key) < 0
                   for key in ("sequence", "committed_bytes", "message_count"))
            or prefix.committed_bytes == 0 or type(prefix.sha256) is not str
            or not re.fullmatch("[0-9a-f]{64}", prefix.sha256)):
        raise TranscriptConflict("Invalid serialized candidate prefix")


@contextmanager
def _serialized_snapshot(candidate, prefix, candidate_check):
    """Pin a regular FD and copy its exact selected bytes without moving its cursor.

    Namespace/lease ownership is the caller's obligation, checked through the
    required callback. Dup/pread avoids trusting overridden read/seek methods or
    sharing the input cursor. The anonymous snapshot is not transcript authority.
    """
    if not callable(candidate_check):
        raise TypeError("Live candidate ownership check required")
    candidate_check()
    if os.name != "posix":
        raise TranscriptConflict("Serialized candidates require qualified POSIX I/O")
    fd = os.dup(candidate.fileno())
    try:
        info = os.fstat(fd)
        stamp = _stamp(info)
        if (not stat.S_ISREG(info.st_mode) or info.st_nlink > 1
                or info.st_size != prefix.committed_bytes):
            raise TranscriptConflict("Serialized candidate requires an exact regular-file EOF")

        def check():
            candidate_check()
            if (_stamp(os.fstat(fd)) != stamp
                    or _stamp(os.fstat(candidate.fileno())) != stamp):
                raise TranscriptConflict("Serialized candidate changed or was replaced")

        check()
        with tempfile.TemporaryFile("w+b") as snapshot:
            digest, offset = hashlib.sha256(), 0
            while offset < prefix.committed_bytes:
                check()
                raw = os.pread(fd, min(CHUNK, prefix.committed_bytes - offset), offset)
                if not raw:
                    raise TranscriptConflict("Serialized candidate shortened")
                snapshot.write(raw)
                digest.update(raw)
                offset += len(raw)
            if os.pread(fd, 1, offset) or digest.hexdigest() != prefix.sha256:
                raise TranscriptConflict("Serialized candidate EOF or digest differs")
            check()
            snapshot.flush()
            snapshot.seek(0)
            yield snapshot, check
            check()  # Includes the existing writer's ownership-fence exit.
    finally:
        os.close(fd)


def _serialized_spool(snapshot, prefix, spool, check):
    """Header-aware bounded transduction into the same canonical rewrite spool."""
    snapshot.seek(0)
    scanner = Scanner(snapshot)
    digest, size, count, header = hashlib.sha256(), 0, 0, False
    for index, row in enumerate(scanner.records()):
        check()
        fields = scanner.fields(row)
        if index == 0:
            _validate_body_header(scanner.small(row), prefix.generation)
            header = True
            continue
        role = fields.get("role")
        value = scanner.small(role, 32768) if role is not None and role.kind == 34 else None
        if type(value) is not str or not value or len(value.encode("utf-8")) > 4096:
            raise TranscriptConflict("Serialized candidate requires canonical message role")
        for raw in transduce(scanner, row):
            check()
            spool.write(raw)
            digest.update(raw)
            size += len(raw)
        spool.write(b"\n")
        digest.update(b"\n")
        size += 1
        count += 1
    if not header or count != prefix.message_count:
        raise TranscriptConflict("Serialized candidate header or message count differs")
    snapshot.seek(prefix.committed_bytes - 1)
    if snapshot.read(1) != b"\n":
        raise TranscriptConflict("Serialized candidate ends inside a row")
    check()
    return digest, size, count


@dataclass(frozen=True)
class _PreparedState:
    prefix: CommittedPrefix
    digest: Any
    size: int
    count: int
    snapshot: BinaryIO
    rows: BinaryIO
    guard: Callable[[], None]
    pid: int


_PREPARED = weakref.WeakKeyDictionary()


def _prepared_state(candidate):
    if type(candidate) is not PreparedTranscriptCandidate:
        raise TranscriptConflict("Exact local prepared candidate required")
    state = _PREPARED.get(candidate)
    if state is None or state.pid != os.getpid():
        raise TranscriptConflict("Prepared candidate is closed, revoked or inherited")
    try:
        state.guard()
    except BaseException:
        _PREPARED.pop(candidate, None)
        raise
    return state


class _VerifiedCandidateView:
    """Read-only bounded seekable view; deliberately exposes no writable FD."""

    def __init__(self, candidate):
        self._candidate, self._offset, self._active = candidate, 0, True

    def _state(self):
        if not self._active:
            raise TranscriptConflict("Verified candidate stream is closed")
        return _prepared_state(self._candidate)

    def tell(self):
        self._state()
        return self._offset

    def seek(self, offset, whence=os.SEEK_SET):
        state = self._state()
        if type(offset) is not int or whence not in (os.SEEK_SET, os.SEEK_CUR, os.SEEK_END):
            raise ValueError("Invalid candidate seek")
        value = offset + (self._offset if whence == os.SEEK_CUR else
                          state.prefix.committed_bytes if whence == os.SEEK_END else 0)
        if not 0 <= value <= state.prefix.committed_bytes:
            raise ValueError("Candidate seek outside verified bytes")
        self._offset = value
        return value

    def read(self, size=CHUNK):
        if type(size) is not int or not 0 <= size <= CHUNK:
            raise ValueError("Candidate reads must be explicitly bounded to 64KiB")
        state = self._state()
        length = min(size, state.prefix.committed_bytes - self._offset)
        raw = os.pread(state.snapshot.fileno(), length, self._offset)
        if len(raw) != length:
            raise TranscriptConflict("Prepared snapshot shortened")
        self._state()
        self._offset += len(raw)
        return raw


class PreparedTranscriptCandidate:
    """Local context-issued immutable input, NOT media/source adoption authority.

    Canonical witness is available before publication for stable lost-ack
    reconciliation. open_verified() exposes the exact raw header-bearing snapshot
    for the converter's bounded ref validator; no mutable FD or row dict API.
    A caller-provided prefix/digest or a constructed/copied object cannot replace
    this issued object. Context exit, source error or fork invalidates it.
    """

    __slots__ = ("__weakref__",)

    @property
    def candidate_prefix(self):
        return _prepared_state(self).prefix

    @property
    def canonical_sha256(self):
        return _prepared_state(self).digest.hexdigest()

    @property
    def canonical_bytes(self):
        return _prepared_state(self).size

    @property
    def message_count(self):
        return _prepared_state(self).count

    def check(self):
        _prepared_state(self)

    @contextmanager
    def open_verified(self):
        self.check()
        view = _VerifiedCandidateView(self)
        try:
            yield view
            self.check()  # Caller must exhaust its ref iterator, including EOF.
        except BaseException:
            _PREPARED.pop(self, None)
            raise
        finally:
            view._active = False


@contextmanager
def prepare_serialized_candidate(
    candidate: BinaryIO, *, candidate_prefix: CommittedPrefix,
    candidate_check: Callable[[], None],
) -> Iterable[PreparedTranscriptCandidate]:
    """Verify and privately snapshot one owned exact-EOF v2 serialized candidate.

    The host/converter owns real input namespace and ownership and passes its
    live guard. All snapshot bytes and canonical row hashes are checked before
    exposing the prepared object. This does not admit/publish media, write target
    history, manufacture a receipt, or turn selector data into adoption authority.
    """
    _candidate_prefix(candidate_prefix)
    issued = PreparedTranscriptCandidate()
    with _serialized_snapshot(candidate, candidate_prefix, candidate_check) as (snapshot, guard):
        with tempfile.TemporaryFile("w+b") as rows:
            digest, size, count = _serialized_spool(snapshot, candidate_prefix, rows, guard)
            rows.flush()
            _PREPARED[issued] = _PreparedState(
                candidate_prefix, digest.copy(), size, count, snapshot, rows, guard, os.getpid())
            try:
                yield issued
                issued.check()
            finally:
                _PREPARED.pop(issued, None)


def _fences(session, operation_id=None):
    marker = {"format": _FORMAT, "v": 2, "status": "unsupported-by-legacy-entrypoint"}
    if operation_id is not None:
        marker["operation_id"] = operation_id
    for name in LEGACY:
        with session.directory(name) as fence:
            value = json.loads(fence.read("format.json"))
            if (not isinstance(value, dict) or value.get("format") != _FORMAT
                    or value.get("v") != 2
                    or value.get("status") != "unsupported-by-legacy-entrypoint"
                    or (operation_id is not None and value != marker)):
                raise TranscriptConflict("Missing or invalid legacy directory fence")


def _fence_marker(operation_id):
    return _small({
        "format": _FORMAT, "v": 2, "status": "unsupported-by-legacy-entrypoint",
        "operation_id": operation_id,
    })


def _install_fence(session, root, name, operation_id, check):
    """Publish a prebuilt *nonempty* directory, never a bare mkdir sentinel."""
    if session.exists(name):
        with session.directory(name) as fence:
            fence.once("format.json", _fence_marker(operation_id))
        return
    stage = ".fence-" + name
    with root.directory(stage, create=True) as fence:
        fence.once("format.json", _fence_marker(operation_id))
    check()
    if session.exists(name):
        raise TranscriptConflict("Legacy entry reappeared before fence installation")
    os.rename(stage, name, src_dir_fd=root.fd, dst_dir_fd=session.fd)
    session.sync()
    root.sync()


class TranscriptWriter:
    """Explicit owner API. Instance cache is never a persisted hash sidecar."""

    def __init__(self, session_dir: str | Path, *, fence: Fence):
        if not callable(fence):
            raise TypeError("A caller-owned quiescence fence is required")
        self.session_dir = Path(session_dir).absolute()
        self.fence = fence
        self._warm = None

    @contextmanager
    def _owned(self):
        try:
            with self.fence(self.session_dir) as check:
                if not callable(check):
                    raise TypeError("Fence must yield a live ownership check")
                check()
                with _Dir.open(self.session_dir) as session:
                    with session.file(".transcript-v2.lock", os.O_RDWR | os.O_CREAT,
                                      mode="r+b") as lock:
                        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
                        check()
                        yield session, check
                        check()
        except BaseException:
            # Includes fsync/rename/open/close/lease errors and cancellation.
            self._warm = None
            raise

    def _current(self, root, expected=None):
        raw = root.read("current.json")
        prefix, selected = CommittedTranscriptReader(self.session_dir)._descriptor()
        if selected != raw or (expected is not None and prefix != expected):
            raise TranscriptConflict("Expected commit witness changed")
        with root.file(prefix.body) as stream:
            before = _stamp(os.fstat(stream.fileno()))
            if self._warm is not None and self._warm[:2] == (prefix, before):
                digest = self._warm[2].copy()
            else:
                digest, _ = _digest(stream, prefix.committed_bytes)
                if digest.hexdigest() != prefix.sha256:
                    raise TranscriptConflict("Committed prefix hash differs")
                stream.seek(0)
                _validate_prefix(stream, prefix)
            if _stamp(os.fstat(stream.fileno())) != before:
                raise TranscriptConflict("Body changed during verification")
        if root.read("current.json") != raw:
            raise TranscriptConflict("Descriptor changed during verification")
        self._warm = (prefix, before, digest.copy())
        return prefix, raw, digest

    @staticmethod
    def _settle(root, operations, prefix, check):
        """Durably record the selected operation before any successor can run."""
        with operations.directory(prefix.operation_id) as operation:
            intended = operation.read("result.json")
            if intended != _descriptor(prefix):
                raise TranscriptConflict("Selected operation has no exact result witness")
            check()
            with root.file(prefix.body) as body:
                os.fsync(body.fileno())
            with root.file("current.json") as current:
                os.fsync(current.fileno())
            operations.sync()
            root.sync()
            operation.once("published.json", intended)

    @staticmethod
    def _tail(root, session, prefix, operation_id, check, expected_stamp):
        """Preserve+fsync the exact tail before any truncation/replacement."""
        with root.file(prefix.body, os.O_RDWR, mode="r+b") as body:
            before = _stamp(os.fstat(body.fileno()))
            if before != expected_stamp:
                raise TranscriptConflict("Body replaced after prefix verification")
            if before[2] < prefix.committed_bytes:
                raise TranscriptConflict("Committed body shortened")
            if before[2] == prefix.committed_bytes:
                return before
            with session.directory("recovery", create=True) as recovery:
                with recovery.directory(operation_id, create=True) as owned:
                    name = f"tail-{uuid4().hex}.bin"
                    with owned.file(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                                    mode="wb") as saved:
                        body.seek(prefix.committed_bytes)
                        digest, count = hashlib.sha256(), 0
                        while raw := body.read(CHUNK):
                            saved.write(raw)
                            digest.update(raw)
                            count += len(raw)
                        saved.flush()
                        os.fsync(saved.fileno())
                    owned.sync()
                    owned.new(name + ".json", _small({
                        "body": prefix.body, "committed_bytes": prefix.committed_bytes,
                        "sha256": digest.hexdigest(), "bytes": count,
                        "operation_id": operation_id,
                    }))
                    if _stamp(os.fstat(body.fileno())) != before:
                        raise TranscriptConflict("Uncommitted tail changed during preservation")
                    check()
                    body.truncate(prefix.committed_bytes)
                    body.flush()
                    os.fsync(body.fileno())
                    return _stamp(os.fstat(body.fileno()))

    def append(self, suffix: Iterable[dict[str, Any]], *, expected: CommittedPrefix,
               operation_id: str, admit: Admission) -> CommitAck:
        """Append only a settled suffix. Same op+same input reconciles, not replays.

        The temporary suffix spool is anonymous, bounded in Python memory, and
        not a second persistent transcript/backup. Intent/result receipts contain
        only small hashes and witnesses; original/tail recovery is never deleted.
        """
        return self._write(suffix, expected=expected, operation_id=operation_id,
                           admit=admit, rewrite=False)

    def rewrite(self, messages: Iterable[dict[str, Any]], *, expected: CommittedPrefix,
                operation_id: str, admit: Admission) -> CommitAck:
        """Real edit/compaction only: new generation, retain the prior body."""
        return self._write(messages, expected=expected, operation_id=operation_id,
                           admit=admit, rewrite=True)

    def rewrite_serialized(
        self, candidate: BinaryIO, *, candidate_prefix: CommittedPrefix,
        expected: CommittedPrefix, operation_id: str, admit: Admission,
        candidate_check: Callable[[], None],
    ) -> CommitAck:
        """Rewrite an owned header-bearing candidate without materializing rows.

        Candidate is an open regular FD, exact EOF == candidate_prefix bytes;
        header/generation, role-bearing schema, digest/count and session binding
        are checked before admission or canonical mutation. The caller keeps its
        actual candidate namespace/lease pinned via candidate_check throughout.
        No pathname, bare JSONL, ignored tail or arbitrary bytes callback accepted.

        Uses the *same* rewrite engine/intent/kind/opid/CAS as rewrite(). The
        intent's suffix_sha256 is stable canonical message JSONL (header excluded),
        NOT the candidate's raw body hash. Reconcile uses that same canonical
        digest; an ordinary rewrite of the same rows is the same operation.
        CommitAck remains an unsealed selector, not a typed media receipt.
        """
        try:
            _candidate_prefix(candidate_prefix)
            if (type(expected) is not CommittedPrefix
                    or candidate_prefix.session_id != expected.session_id):
                raise TranscriptConflict("Candidate and target session differ")
            _identity(operation_id)
            with prepare_serialized_candidate(
                candidate, candidate_prefix=candidate_prefix, candidate_check=candidate_check,
            ) as prepared:
                return self.rewrite_prepared(
                    prepared, expected=expected, operation_id=operation_id, admit=admit)
        except BaseException:
            # A late input/fence check may fail after pointer publication. It is
            # an unknown acknowledgement, never permission to replay/new-op.
            self._warm = None
            raise

    def rewrite_prepared(
        self, candidate: PreparedTranscriptCandidate, *, expected: CommittedPrefix,
        operation_id: str, admit: Admission,
    ) -> CommitAck:
        """Consume the exact issued spool validated by admission, same rewrite engine.

        Before calling, retain candidate.canonical_sha256 with the settled intent
        for reconcile(kind="rewrite"). Admission must validate/publish refs from
        candidate.open_verified() under the actual store/host ownership, exhausting
        the converter's ref iterator. Only an exact sealed ACK after publication
        can establish media reachability; this prepared object is not that ACK.
        """
        try:
            state = _prepared_state(candidate)
            if type(expected) is not CommittedPrefix or state.prefix.session_id != expected.session_id:
                raise TranscriptConflict("Candidate and target session differ")

            def load(spool, check):
                offset = 0
                while offset < state.size:
                    check()
                    raw = os.pread(state.rows.fileno(), min(CHUNK, state.size - offset), offset)
                    if not raw:
                        raise TranscriptConflict("Prepared canonical spool shortened")
                    spool.write(raw)
                    offset += len(raw)
                check()
                return state.digest.copy(), state.size, state.count

            ack = self._write(
                None, expected=expected, operation_id=operation_id, admit=admit,
                rewrite=True, _spool_loader=load, _input_check=candidate.check)
            candidate.check()  # The target owner fence has also exited now.
            return ack
        except BaseException:
            self._warm = None
            raise

    def _write(self, messages, *, expected, operation_id, admit, rewrite,
               _spool_loader=None, _input_check=None):
        operation_id = _identity(operation_id)
        if not isinstance(expected, CommittedPrefix) or not callable(admit):
            raise TypeError("Explicit expected commit and admission required")
        with self._owned() as (session, check), tempfile.TemporaryFile("w+b") as spool:
            if _input_check is not None:
                owned_check = check

                def check():
                    owned_check()
                    _input_check()

            if _spool_loader is not None:
                digest, size, count = _spool_loader(spool, check)
            else:
                digest, size, count = hashlib.sha256(), 0, 0
                for message in messages:
                    check()
                    if (type(message) is not dict or type(message.get("role")) is not str
                            or not message["role"] or len(message["role"]) > 4096
                            or len(message["role"].encode("utf-8")) > 4096):
                        raise TranscriptConflict("Settled suffix must contain canonical messages")
                    for raw in canonical_bytes(message):
                        check()
                        spool.write(raw)
                        digest.update(raw)
                        size += len(raw)
                    spool.write(b"\n")
                    digest.update(b"\n")
                    size += 1
                    count += 1
            with session.directory("transcript.v2") as root:
                _fences(session)
                intent = _small({
                    "kind": "rewrite" if rewrite else "append", "operation_id": operation_id,
                    "expected": asdict(expected), "suffix_sha256": digest.hexdigest(),
                    "suffix_bytes": size, "suffix_messages": count,
                })
                with root.directory("operations") as operations:
                    # Retry an already acknowledged historical operation even if
                    # a later commit/rewrite is now current. No new mutation.
                    if operations.exists(operation_id):
                        with operations.directory(operation_id) as operation:
                            operation.once("intent.json", intent)
                            if operation.exists("published.json"):
                                self._current(root)
                                result = _receipt(operation, operation_id, expected.session_id)
                                operations.sync()
                                return CommitAck(result, True)
                    prefix, raw_descriptor, state = self._current(root)
                    self._settle(root, operations, prefix, check)
                    if prefix.operation_id == operation_id:
                        with operations.directory(operation_id) as operation:
                            operation.once("intent.json", intent)
                        return CommitAck(prefix, True)
                    if prefix != expected:
                        raise TranscriptConflict("Expected commit witness changed")
                    if not count and not rewrite:
                        return CommitAck(prefix)
                    with operations.directory(operation_id, create=True) as operation:
                        operation.once("intent.json", intent)
                        # Admission owns media validation/publication/fsync and
                        # must happen before canonical reachability changes.
                        admit()
                        check()
                        if rewrite:
                            with root.file(prefix.body) as old_body:
                                if _stamp(os.fstat(old_body.fileno())) != self._warm[1]:
                                    raise TranscriptConflict("Rewrite source changed after verification")
                            generation = "r-" + operation_id
                            body_name = f"g-{generation}.jsonl"
                            # Never truncate a torn new-generation attempt.
                            if root.exists(body_name):
                                with session.directory("recovery", create=True) as recovery:
                                    with recovery.directory(operation_id, create=True) as retained:
                                        os.rename(body_name, f"generation-{uuid4().hex}.jsonl",
                                                  src_dir_fd=root.fd, dst_dir_fd=retained.fd)
                                        retained.sync()
                                        root.sync()
                            flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
                            state, offset, total = hashlib.sha256(), 0, count
                        else:
                            body_stamp = self._tail(root, session, prefix, operation_id,
                                                    check, self._warm[1])
                            generation, body_name = prefix.generation, prefix.body
                            flags = os.O_RDWR
                            offset, total = prefix.committed_bytes, prefix.message_count + count
                        with root.file(body_name, flags, mode="r+b" if not rewrite else "wb") as body:
                            if not rewrite and _stamp(os.fstat(body.fileno())) != body_stamp:
                                raise TranscriptConflict("Body replaced before append")
                            body.seek(offset)
                            if rewrite:
                                header = _header(generation)
                                body.write(header)
                                state.update(header)
                                offset += len(header)
                            spool.seek(0)
                            while chunk := spool.read(CHUNK):
                                check()
                                body.write(chunk)
                                state.update(chunk)
                                offset += len(chunk)
                            body.flush()
                            os.fsync(body.fileno())
                            stamp = _stamp(os.fstat(body.fileno()))
                        root.sync()
                        result = CommittedPrefix(prefix.session_id, generation, body_name,
                                                 prefix.sequence + 1, offset, total,
                                                 state.hexdigest(), operation_id)
                        if rewrite:
                            # Keep the old body at its generation identity (it
                            # may still be selected until the pointer switch).
                            # Explicit recovery witness owns that retained body;
                            # never copy a continuously growing backup.
                            with session.directory("recovery", create=True) as recovery:
                                with recovery.directory(operation_id, create=True) as retained:
                                    retained.once("previous-generation.json", raw_descriptor)
                        operation.once("result.json", _descriptor(result))
                        root.replace("current.json", _descriptor(result),
                                     expected=raw_descriptor, check=check)
                        self._settle(root, operations, result, check)
                        self._warm = (result, stamp, state.copy())
                        return CommitAck(result)

    def reconcile(self, *, operation_id: str, expected: CommittedPrefix,
                  suffix_sha256: str, kind: str = "append") -> CommitAck | None:
        """Resolve an uncertain operation without replaying/encoding its payload.

        Caller supplies the stable SHA256 of canonical suffix JSONL bytes and
        exact predecessor. None means no committed effect for that request;
        staged tails may remain and must be handled only by the owned same-op
        retry. An unexplained successor or mismatching request refuses.
        """
        operation_id = _identity(operation_id)
        if kind not in ("append", "rewrite") or not isinstance(expected, CommittedPrefix):
            raise TypeError("Explicit kind and predecessor required")
        with self._owned() as (session, check):
            _fences(session)
            with session.directory("transcript.v2") as root:
                prefix, _, _ = self._current(root)
                with root.directory("operations") as operations:
                    self._settle(root, operations, prefix, check)
                    if not operations.exists(operation_id):
                        if prefix != expected:
                            raise TranscriptConflict("Unknown operation after changed commit")
                        return None
                    with operations.directory(operation_id) as operation:
                        intent = _decode(operation.read("intent.json"))
                        if (not isinstance(intent, dict) or intent.get("kind") != kind
                                or intent.get("operation_id") != operation_id
                                or intent.get("expected") != asdict(expected)
                                or intent.get("suffix_sha256") != suffix_sha256):
                            raise TranscriptConflict("Reconciliation request witness differs")
                        if operation.exists("published.json"):
                            return CommitAck(_receipt(operation, operation_id, prefix.session_id), True)
                        if prefix != expected:
                            raise TranscriptConflict("Unpublished operation after changed commit")
                        return None

    def initialize(self, *, session_id: str, operation_id: str, admit: Admission) -> CommitAck:
        """Explicitly initialize an empty session; refuse recovery/pending evidence."""
        return self._migrate(session_id=session_id, operation_id=operation_id,
                             expected_sources=dict.fromkeys(LEGACY), source=None, admit=admit)

    def migrate(self, *, session_id: str, operation_id: str,
                expected_sources: dict[str, SourceWitness | None],
                source: str, admit: Admission) -> CommitAck:
        """Migrate the explicitly selected observed canonical source, never guess."""
        if source not in LEGACY:
            raise TypeError("Migration requires an explicitly selected existing source")
        return self._migrate(session_id=session_id, operation_id=operation_id,
                             expected_sources=expected_sources, source=source, admit=admit)

    def _migrate(self, *, session_id: str, operation_id: str,
                expected_sources: dict[str, SourceWitness | None],
                source: str | None, admit: Admission) -> CommitAck:
        """Quiescent canonical-v1 migration or explicit new-session initialization.

        Sources must be explicitly observed; no stale-backup inference. Both
        legacy names are moved (if present), byte-for-byte, into recovery/opid.
        Missing-journal/foreign-root states refuse and require owner diagnosis;
        they are never interpreted as an empty healthy transcript.
        """
        session_id, operation_id = _identity(session_id), _identity(operation_id)
        if (set(expected_sources) != set(LEGACY) or source not in (*LEGACY, None)
                or (source is not None and expected_sources[source] is None)
                or (source is None and any(expected_sources.values()))
                or not callable(admit)):
            raise TypeError("Explicit complete source selection and admission required")
        plan = _small({
            "format": _FORMAT, "v": 2, "session_id": session_id,
            "operation_id": operation_id, "source": source,
            "sources": {name: asdict(value) if value else None
                        for name, value in expected_sources.items()},
        })
        generation = "m-" + operation_id
        body_name = f"g-{generation}.jsonl"
        with self._owned() as (session, check):
            existed = session.exists("transcript.v2")
            if not existed:
                if (session.exists("recovery")
                        or any(session.exists(name) for name in os.listdir(session.fd)
                               if name.startswith(("transcript.jsonl.", ".transcript-"))
                               and name not in (*LEGACY, ".transcript-v2.lock"))):
                    raise TranscriptConflict("Recovery/interrupted legacy evidence requires diagnosis")
                # Validate original names before entering a transition.
                for name, witness in expected_sources.items():
                    _same_source(_source(session, name), witness)
            with session.directory("transcript.v2", create=not existed) as root:
                if existed and not root.exists("migration.json"):
                    raise TranscriptConflict("Incomplete/foreign versioned root without journal")
                root.once("migration.json", plan)
                with root.directory("operations", create=True) as operations:
                    with operations.directory(operation_id, create=True) as operation:
                        operation.once("intent.json", plan)
                        if root.exists("current.json"):
                            _fences(session, operation_id)
                            prefix, _, _ = self._current(root)
                            if operation.exists("published.json"):
                                return CommitAck(_receipt(operation, operation_id, session_id), True)
                            if prefix.operation_id != operation_id:
                                raise TranscriptConflict("Migration already followed by another operation")
                            self._settle(root, operations, prefix, check)
                            return CommitAck(prefix, True)
                        with session.directory("recovery", create=True) as recovery:
                            with recovery.directory(operation_id, create=True) as retained:
                                # Reconcile only the exact journal's owned moves.
                                locations = {}
                                for name, witness in expected_sources.items():
                                    if retained.exists(name):
                                        _same_source(_source(retained, name), witness, moved=True)
                                        locations[name] = retained
                                        if session.exists(name):
                                            with session.directory(name) as fence:
                                                fence.once("format.json", _fence_marker(operation_id))
                                    elif witness is None and session.exists(name):
                                        with session.directory(name) as fence:
                                            fence.once("format.json", _fence_marker(operation_id))
                                        locations[name] = retained
                                    else:
                                        _same_source(_source(session, name), witness)
                                        locations[name] = session
                                if root.exists(body_name):
                                    os.rename(body_name, f"generation-{uuid4().hex}.jsonl",
                                              src_dir_fd=root.fd, dst_dir_fd=retained.fd)
                                    retained.sync()
                                    root.sync()
                                state, size, count = hashlib.sha256(), 0, 0
                                with root.file(body_name, os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                                               mode="wb") as body:
                                    header = _header(generation)
                                    body.write(header)
                                    state.update(header)
                                    size += len(header)
                                    if source is not None:
                                        with locations[source].file(source) as original:
                                            # Count using emitted record delimiters: encoded
                                            # string newlines are escaped, never raw LF.
                                            before = _stamp(os.fstat(original.fileno()))
                                            for chunk in canonical_records(original):
                                                check()
                                                body.write(chunk)
                                                state.update(chunk)
                                                size += len(chunk)
                                                if chunk == b"\n":
                                                    count += 1
                                            if _stamp(os.fstat(original.fileno())) != before:
                                                raise TranscriptConflict("Migration source changed")
                                    body.flush()
                                    os.fsync(body.fileno())
                                root.sync()
                                admit()
                                check()
                                retained.once("sources.json", plan)
                                # Install originally absent names first. At least
                                # one legacy entry remains an original or a fence
                                # throughout all source moves. With both present,
                                # backup-first also blocks fallback before primary
                                # is removed. No empty healthy old-host window.
                                order = sorted(LEGACY, key=lambda name: (
                                    expected_sources[name] is not None,
                                    name != "transcript.jsonl.backup"))
                                for name in order:
                                    witness = expected_sources[name]
                                    if locations[name] is session and witness is not None:
                                        _same_source(_source(session, name), witness)
                                        with session.file(name) as original:
                                            os.fsync(original.fileno())
                                        check()
                                        os.rename(name, name, src_dir_fd=session.fd,
                                                  dst_dir_fd=retained.fd)
                                        retained.sync()
                                        session.sync()
                                    if witness is not None:
                                        _same_source(_source(retained, name), witness, moved=True)
                                        with retained.file(name) as original:
                                            os.fsync(original.fileno())
                                        retained.sync()
                                    _install_fence(session, root, name, operation_id, check)
                                session.sync()
                                _fences(session, operation_id)
                                result = CommittedPrefix(session_id, generation, body_name, 1,
                                                         size, count, state.hexdigest(), operation_id)
                                operation.once("result.json", _descriptor(result))
                                root.once("ready.json", _small({
                                    "format": _FORMAT, "v": 2, "session_id": session_id,
                                    "operation_id": operation_id,
                                }))
                                root.replace("current.json", _descriptor(result),
                                             expected=None, check=check)
                                self._settle(root, operations, result, check)
                                self._warm = None  # First append cold-verifies the new body.
                                return CommitAck(result)