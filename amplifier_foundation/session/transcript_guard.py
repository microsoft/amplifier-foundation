"""Detect native transcript conflicts without loading or owning the history."""

from __future__ import annotations

from os import PathLike
from pathlib import Path
import stat


_FileStamp = tuple[int, int, int, int, int] | None


class NativeTranscriptConflict(RuntimeError):
    """An older host changed native history outside the shared lock."""


class NativeTranscriptGuard:
    """Fail closed when native history changes after admission.

    Foundation's lock coordinates participating hosts. Older CLI releases do
    not use it, so their native file must also be checked before each write.
    This is conflict detection, not a lock over non-cooperating processes.
    """

    def __init__(self, path: str | PathLike[str]) -> None:
        self.path = Path(path)
        self.stamp = self._stamp()
        self.companion_stamps = self._companions()

    @staticmethod
    def conflict() -> NativeTranscriptConflict:
        return NativeTranscriptConflict(
            "The native CLI transcript changed outside this session's shared lock. "
            "Both histories were preserved. Resolve the conflicting transcript before continuing this session.")

    def _stamp(self) -> _FileStamp:
        try:
            info = self.path.lstat()
        except FileNotFoundError:
            return None
        if not stat.S_ISREG(info.st_mode):
            raise NativeTranscriptConflict("The native CLI transcript is not a regular file; it was not changed.")
        return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns)

    def _companions(self) -> tuple[_FileStamp, ...]:
        values = []
        for name in ('transcript.jsonl.backup',):
            path = self.path.with_name(name)
            try:
                info = path.lstat()
                if not stat.S_ISREG(info.st_mode):
                    raise NativeTranscriptConflict('Native session history must use regular files; nothing was changed.')
                values.append((info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns))
            except FileNotFoundError:
                values.append(None)
        return tuple(values)

    def check(self) -> None:
        if self._stamp() != self.stamp or self._companions() != self.companion_stamps:
            raise self.conflict()

    def saved(self) -> None:
        self.stamp = self._stamp()
        self.companion_stamps = self._companions()
