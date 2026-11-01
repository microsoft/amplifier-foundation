"""Private native history I/O, not checkpoint/fence or ownership publication.

Reuse the ownership backend's ABI, ACL and pinned-traversal validation, but keep
history's replaceable readers and temporaries separate from its stable locks.
Only ordinary history names can be mutated here. No namespace durability claim.
"""

from __future__ import annotations

import ctypes as C
import io
import re
import time
import uuid
from contextlib import contextmanager
from pathlib import Path

from . import _windows_ownership as win

_FILES = frozenset({
    "transcript.jsonl", "transcript.jsonl.backup",
    "metadata.json", "metadata.json.backup", "transcript.jsonl.append-pending",
})
_TEMPORARY = re.compile(r"\.history-[0-9a-f]{32}\.tmp")


class _BASIC_INFO(C.Structure):
    _fields_ = [
        ("creation", C.c_int64), ("access", C.c_int64),
        ("write", C.c_int64), ("change", C.c_int64), ("attributes", win.DWORD),
    ]


def _history_name(path: Path) -> None:
    if path.name not in _FILES:
        raise ValueError("Windows history mutation requires an ordinary history filename")


class _API(win._API):
    def __init__(self):
        super().__init__()
        for name, result, args in (
            ("SetFilePointerEx", win.BOOL,
             [win.HANDLE, C.c_int64, C.POINTER(C.c_int64), win.DWORD]),
            ("GetFileInformationByHandleEx", win.BOOL,
             [win.HANDLE, C.c_int, win.PVOID, win.DWORD]),
        ):
            function = getattr(self.kernel, name)
            function.restype, function.argtypes = result, args
            setattr(self, name, function)

    def open_history(self, path, *, write=False, temporary=False, remove=False):
        if path.name in {"session.lock", "owner.json", "checkpoint.json", "transfer-fence.json"} or path.name.endswith(".lock"):
            raise ValueError("ownership and stable locks are not history files")
        if write or remove:
            if temporary:
                if not write or remove or not _TEMPORARY.fullmatch(path.name):
                    raise ValueError("invalid Windows history temporary")
            else:
                _history_name(path)
                if remove and path.name != "transcript.jsonl.append-pending":
                    raise ValueError("only an append intent may be removed")
        elif temporary:
            raise ValueError("a history temporary must be a new writable file")
        access = win._READ_CONTROL | win._ATTRIBUTES | win._READ
        if write:
            access |= win._WRITE
        if temporary or remove:
            access |= 0x10000  # DELETE on the temporary/intent, never a lock.
        with self.private_security() as security:
            handle = self.CreateFileW(
                str(path), access, 3 if temporary else 7, C.byref(security),
                1 if temporary else 3, win._OPEN_REPARSE, None,
            )
            error = C.get_last_error() if handle == win._INVALID else 0
        if handle == win._INVALID:
            raise C.WinError(error)
        try:
            self.ok(self.SetHandleInformation(handle, 1, 0))
            self.info(handle, path, directory=False)
            self.validate_acl(handle, private=True)
            return handle
        except BaseException:
            self.close(handle)
            raise

    def seek(self, handle, offset, whence):
        position = C.c_int64()
        self.ok(self.SetFilePointerEx(handle, offset, C.byref(position), whence))
        return position.value

    def write_chunks(self, handle, chunks):
        for chunk in chunks:
            offset = 0
            while offset < len(chunk):
                piece = chunk[offset:offset + 65536]
                buffer, count = C.create_string_buffer(piece), win.DWORD()
                self.ok(self.WriteFile(handle, buffer, len(piece), C.byref(count), None))
                if not 0 < count.value <= len(piece):
                    raise OSError("short Windows history write")
                offset += count.value
        self.ok(self.FlushFileBuffers(handle))

    def replace_history(self, handle, path):
        _history_name(path)
        encoded = str(win._absolute(path)).encode("utf-16-le")
        buffer = C.create_string_buffer(C.sizeof(win.FILE_RENAME_INFO) + len(encoded))
        info = win.FILE_RENAME_INFO.from_buffer(buffer)
        info.Flags = 3  # REPLACE_IF_EXISTS | POSIX_SEMANTICS: old readers survive.
        info.FileNameLength = len(encoded)
        C.memmove(C.addressof(buffer) + win.FILE_RENAME_INFO.FileName.offset,
                  encoded, len(encoded))
        # No MoveFileEx/copy/delete fallback; same pinned directory and volume.
        self.ok(self.SetFileInformationByHandle(handle, 22, buffer, len(buffer)))


class _Reader(io.RawIOBase):
    """BufferedReader supplies bounded readline/peek and lazy indexed seeks."""

    def __init__(self, api, handle):
        self.api, self.handle = api, handle

    def readable(self):
        return True

    def seekable(self):
        return True

    def readinto(self, output):
        self._checkClosed()
        size = min(len(output), 1024 * 1024)
        buffer, count = C.create_string_buffer(size), win.DWORD()
        self.api.ok(self.api.ReadFile(self.handle, buffer, size, C.byref(count), None))
        output[:count.value] = buffer.raw[:count.value]
        return count.value

    def seek(self, offset, whence=0):
        self._checkClosed()
        return self.api.seek(self.handle, offset, whence)

    def tell(self):
        return self.seek(0, 1)

    def close(self):
        if not self.closed:
            try:
                self.api.close(self.handle)
            finally:
                super().close()


@contextmanager
def _file(path, *, write=False, remove=False):
    path = win._absolute(path)
    api = _API()
    with win._pinned(api, path.parent, root=path.parent) as pins:
        if pins.missing:
            raise FileNotFoundError(path)
        handle = api.open_history(path, write=write, remove=remove)
        try:
            yield api, handle
        finally:
            api.close(handle)


@contextmanager
def open_binary(path):
    path = win._absolute(path)
    api = _API()
    with win._pinned(api, path.parent, root=path.parent) as pins:
        if pins.missing:
            raise FileNotFoundError(path)
        # The stream owns the handle; the context retains every ancestor pin.
        with io.BufferedReader(_Reader(api, api.open_history(path))) as stream:
            yield stream


def stamp(path):
    from .shared_state import FileStamp

    try:
        with _file(path) as (api, handle):
            info = api.info(handle, win._absolute(path), directory=False)
            basic = _BASIC_INFO()
            api.ok(api.GetFileInformationByHandleEx(handle, 0, C.byref(basic), C.sizeof(basic)))
            epoch = 116444736000000000
            return FileStamp(
                info.volume, (info.index_high << 32) | info.index_low,
                (info.size_high << 32) | info.size_low,
                (basic.write - epoch) * 100, (basic.change - epoch) * 100,
            )
    except FileNotFoundError:
        return None


def _validate_target(api, path):
    try:
        handle = api.open_history(path)
    except FileNotFoundError:
        return
    api.close(handle)


def _replace(path, chunks):
    _history_name(path)
    path = win._absolute(path)
    api = _API()
    with win._pinned(api, path.parent, root=path.parent, create=True):
        _validate_target(api, path)  # Reject unsafe existing objects, never repair.
        temporary = path.parent / f".history-{uuid.uuid4().hex}.tmp"
        handle = api.open_history(temporary, write=True, temporary=True)
        try:
            api.write_chunks(handle, chunks)
            api.replace_history(handle, path)
        except BaseException as exc:
            try:
                api.close(handle)
            except OSError as cleanup:
                exc.add_note(f"History temporary close failed: {cleanup}")
            try:
                api.ok(api.DeleteFileW(str(temporary)))
            except OSError as cleanup:
                exc.add_note(f"History temporary cleanup failed: {cleanup}")
            raise
        api.close(handle)


def copy_backup(source, target):
    _history_name(source)
    if target != source.with_suffix(source.suffix + ".backup"):
        raise ValueError("history backup must be a sibling of its primary")
    with open_binary(source) as stream:
        _replace(target, iter(lambda: stream.read(1024 * 1024), b""))


def write_prepared(path, content, preserve_backup):
    _history_name(path)
    if not preserve_backup and stamp(path) is not None:
        copy_backup(path, path.with_suffix(path.suffix + ".backup"))
    _replace(path, [content.encode("utf-8")])
    if path.name == "transcript.jsonl":
        remove_pending(path.with_name(path.name + ".append-pending"))


def write_pending(path):
    if path.name != "transcript.jsonl.append-pending":
        raise ValueError("expected transcript append intent")
    _replace(path, [b'{"version":1}\n'])


def remove_pending(path):
    if path.name != "transcript.jsonl.append-pending":
        raise ValueError("expected transcript append intent")
    try:
        with _file(path, remove=True) as (api, handle):
            # FileDispositionInfoEx: DELETE | POSIX_SEMANTICS. Delete the exact
            # validated intent, not a path reopened after validation.
            flags = win.DWORD(3)
            api.ok(api.SetFileInformationByHandle(handle, 21, C.byref(flags), C.sizeof(flags)))
    except FileNotFoundError:
        pass


def append(path, chunks):
    if path.name not in {"transcript.jsonl", "transcript.jsonl.backup"}:
        raise ValueError("only a transcript or its backup may be appended")
    with _file(path, write=True) as (api, handle):
        api.seek(handle, 0, 2)
        api.write_chunks(handle, chunks)


@contextmanager
def metadata_lock(session_dir):
    """Stable, non-inheritable sibling lock; never truncate, unlink or replace."""
    from filelock import Timeout

    session_dir = win._absolute(session_dir)
    api = _API()
    parent = session_dir.parent
    with win._pinned(api, parent, root=parent, private=False, create=True):
        path = parent / f".{session_dir.name}.metadata.lock"
        handle = api.open(path, create=True, write=True)
        try:
            api.validate_acl(handle, private=True)
            overlapped = win.OVERLAPPED()
            deadline = time.monotonic() + 10
            while not api.LockFileEx(handle, 3, 0, 1, 0, C.byref(overlapped)):
                error = C.get_last_error()
                if error != 33:
                    raise C.WinError(error)
                if time.monotonic() >= deadline:
                    raise Timeout(str(path))
                time.sleep(0.05)
            yield
        finally:
            # Close releases LockFileEx on every path, including process death.
            api.close(handle)