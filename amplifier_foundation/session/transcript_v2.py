"""Reader-only committed-prefix transcript support.

No writer, migration, fence installation, tail truncation or repair lives here.
This recognizes an explicit distinct versioned body; legacy history is unchanged.
The descriptor is authority only after exact body/header/prefix verification.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import re
import stat
from typing import Any


class UnsupportedTranscript(ValueError):
    """An explicit versioned transcript cannot be read safely by this reader."""


_FORMAT = "amplifier.session.transcript"
_FEATURES = {"committed-prefix-v2", "session-media-v1"}
_DESCRIPTOR_FIELDS = {
    "format", "v", "session_id", "generation", "body", "sequence",
    "committed_bytes", "message_count", "sha256", "operation_id", "min_reader",
}
_CHUNK = 64 * 1024
_IDENTITY = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,220}")


def _decode(raw):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise UnsupportedTranscript("Ambiguous transcript object")
            result[key] = value
        return result

    def invalid(_):
        raise UnsupportedTranscript("Non-finite transcript value")

    try:
        return json.loads(raw, object_pairs_hook=pairs, parse_constant=invalid)
    except (UnicodeError, ValueError):
        raise UnsupportedTranscript("Invalid versioned transcript JSON") from None


def _stamp(info):
    return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns)


@contextmanager
def _file(path: Path):
    # Reader support must never resolve arbitrary paths or silently follow links.
    if ".." in path.parts:
        raise UnsupportedTranscript("Invalid versioned transcript path")
    absolute = path.absolute()
    opened = []
    stream = None
    try:
        if os.name != "posix":
            raise UnsupportedTranscript("Versioned transcript reader is not qualified on this platform")
        directory = os.open(absolute.anchor, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        opened.append(directory)
        for part in absolute.parts[1:-1]:
            directory = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                                dir_fd=directory)
            opened.append(directory)
        descriptor = os.open(absolute.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK,
                             dir_fd=directory)
        stream = os.fdopen(descriptor, "rb")
        before = _stamp(os.fstat(stream.fileno()))
        if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
            raise UnsupportedTranscript("Versioned transcript requires regular files")
        yield stream
        if (_stamp(os.fstat(stream.fileno())) != before
                or _stamp(os.stat(absolute.name, dir_fd=directory,
                                  follow_symlinks=False)) != before):
            raise UnsupportedTranscript("Versioned transcript changed during read")
        for index in range(1, len(opened)):
            linked = os.stat(absolute.parts[index], dir_fd=opened[index - 1],
                             follow_symlinks=False)
            pinned = os.fstat(opened[index])
            if (linked.st_dev, linked.st_ino) != (pinned.st_dev, pinned.st_ino):
                raise UnsupportedTranscript("Versioned transcript directory changed")
    except OSError:
        raise UnsupportedTranscript("Versioned transcript cannot be opened safely") from None
    finally:
        if stream is not None:
            stream.close()
        for descriptor in reversed(opened):
            os.close(descriptor)


@dataclass(frozen=True)
class CommittedPrefix:
    session_id: str
    generation: str
    body: str
    sequence: int
    committed_bytes: int
    message_count: int
    sha256: str
    operation_id: str


class CommittedTranscriptReader:
    """Read a verified committed prefix, ignoring all uncommitted tail bytes.

    Verification streams prefix bytes without retaining them. Iteration decodes
    one record at a time; that existing public message API necessarily materializes
    the largest individual message. Historical huge-scalar conversion uses its
    separate owner adapter, not this ordinary resume reader.
    """

    def __init__(self, session_dir: str | Path):
        self.session_dir = Path(session_dir)
        self.root = self.session_dir / "transcript.v2"

    def _descriptor(self) -> tuple[CommittedPrefix, bytes]:
        with _file(self.root / "current.json") as stream:
            raw = stream.read(_CHUNK + 1)
        if len(raw) > _CHUNK:
            raise UnsupportedTranscript("Versioned transcript descriptor is oversized")
        data = _decode(raw)
        if not isinstance(data, dict) or set(data) != _DESCRIPTOR_FIELDS:
            raise UnsupportedTranscript("Unsupported versioned transcript descriptor")
        if data["format"] != _FORMAT or type(data["v"]) is not int or data["v"] != 2:
            raise UnsupportedTranscript("Unsupported transcript version")
        features = data["min_reader"]
        if (not isinstance(features, list) or not features
                or any(type(item) is not str or item not in _FEATURES for item in features)):
            raise UnsupportedTranscript("Unsupported required transcript reader")
        for name in ("session_id", "generation", "operation_id"):
            if not isinstance(data[name], str) or not _IDENTITY.fullmatch(data[name]):
                raise UnsupportedTranscript("Invalid versioned transcript identity")
        if data["body"] != f"g-{data['generation']}.jsonl":
            raise UnsupportedTranscript("Invalid versioned transcript body identity")
        for name in ("sequence", "committed_bytes", "message_count"):
            if type(data[name]) is not int or data[name] < 0:
                raise UnsupportedTranscript("Invalid committed-prefix bound")
        if not isinstance(data["sha256"], str) or not re.fullmatch("[0-9a-f]{64}", data["sha256"]):
            raise UnsupportedTranscript("Invalid committed-prefix digest")
        return CommittedPrefix(**{name: data[name] for name in
                                 CommittedPrefix.__dataclass_fields__}), raw

    def _unchanged(self, raw):
        _, current = self._descriptor()
        if current != raw:
            raise UnsupportedTranscript("Transcript commit changed during read")

    def verify(self) -> CommittedPrefix:
        prefix, descriptor = self._descriptor()
        digest = hashlib.sha256()
        remaining = prefix.committed_bytes
        with _file(self.root / prefix.body) as stream:
            while remaining:
                chunk = stream.read(min(_CHUNK, remaining))
                if not chunk:
                    raise UnsupportedTranscript("Committed transcript prefix exceeds body")
                remaining -= len(chunk)
                digest.update(chunk)
            if digest.hexdigest() != prefix.sha256:
                raise UnsupportedTranscript("Committed transcript digest differs")
        self._unchanged(descriptor)
        # Validate semantic/header/count closure before acknowledging verification.
        count = sum(1 for _ in self._iterate(prefix))
        if count != prefix.message_count:
            raise UnsupportedTranscript("Committed transcript message count differs")
        self._unchanged(descriptor)
        return prefix

    def _iterate(self, prefix) -> Iterator[dict[str, Any]]:
        with _file(self.root / prefix.body) as stream:
            remaining = prefix.committed_bytes
            header = None
            digest = hashlib.sha256()
            count = 0
            while remaining:
                raw = stream.readline(remaining)
                if not raw or not raw.endswith(b"\n"):
                    raise UnsupportedTranscript("Committed transcript ends inside a row")
                remaining -= len(raw)
                digest.update(raw)
                value = _decode(raw)
                if header is None:
                    header = value
                    if value != {
                        "format": _FORMAT, "v": 2, "generation": prefix.generation,
                        "min_reader": ["committed-prefix-v2", "session-media-v1"],
                    }:
                        raise UnsupportedTranscript("Unsupported transcript body header")
                else:
                    if (not isinstance(value, dict) or not isinstance(value.get("role"), str)
                            or not value["role"]):
                        raise UnsupportedTranscript("Invalid committed canonical message")
                    count += 1
                    yield value
            if header is None:
                raise UnsupportedTranscript("Missing transcript body header")
            if digest.hexdigest() != prefix.sha256 or count != prefix.message_count:
                raise UnsupportedTranscript("Committed transcript content changed")

    def iter_messages(self) -> Iterator[dict[str, Any]]:
        prefix = self.verify()
        _, descriptor = self._descriptor()
        # Prefix/descriptor must be the same selection verified above.
        current, _ = self._descriptor()
        if current != prefix:
            raise UnsupportedTranscript("Transcript commit changed during read")
        yield from self._iterate(prefix)
        self._unchanged(descriptor)