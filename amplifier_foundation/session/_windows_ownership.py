"""Private local-NTFS ownership backend. No authoritative namespace mutations.

Every path component is opened without following reparse points and kept open
without FILE_SHARE_DELETE before opening its child. The stable lock is never
unlinked or replaced. Only owner.json is advisory and may be atomically replaced;
this is NOT an acknowledgment of power-loss directory/ancestor durability.
"""

from __future__ import annotations

import ctypes as C
import os
import re
import uuid
from contextlib import contextmanager
from pathlib import Path, PureWindowsPath

# Fixed-width Windows types (including when ABI layout is tested on POSIX).
DWORD = C.c_uint32
WORD = C.c_uint16
BYTE = C.c_ubyte
BOOL = C.c_int32
HANDLE = C.c_void_p
PVOID = C.c_void_p
PDWORD = C.POINTER(DWORD)


class OVERLAPPED(C.Structure):
    _fields_ = [
        ("Internal", C.c_size_t), ("InternalHigh", C.c_size_t),
        ("Offset", DWORD), ("OffsetHigh", DWORD), ("hEvent", HANDLE),
    ]


class SECURITY_ATTRIBUTES(C.Structure):
    _fields_ = [("nLength", DWORD), ("lpSecurityDescriptor", PVOID), ("bInheritHandle", BOOL)]


class FILE_RENAME_INFO(C.Structure):
    # FileRenameInfoEx uses the DWORD Flags arm of the initial union, not BOOL.
    # WCHAR is always UTF-16, even on a POSIX host testing this ABI layout.
    _fields_ = [("Flags", DWORD), ("RootDirectory", HANDLE),
                ("FileNameLength", DWORD), ("FileName", WORD * 1)]


class FILETIME(C.Structure):
    _fields_ = [("low", DWORD), ("high", DWORD)]


class FILE_INFO(C.Structure):
    _fields_ = [
        ("attributes", DWORD), ("creation", FILETIME), ("access", FILETIME),
        ("write", FILETIME), ("volume", DWORD), ("size_high", DWORD),
        ("size_low", DWORD), ("links", DWORD), ("index_high", DWORD), ("index_low", DWORD),
    ]


class ACL_INFO(C.Structure):
    _fields_ = [("count", DWORD), ("used", DWORD), ("free", DWORD)]


class ACE(C.Structure):
    _fields_ = [("kind", BYTE), ("flags", BYTE), ("size", WORD), ("mask", DWORD), ("sid", DWORD)]


_RESERVED = re.compile(r"^(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])$", re.I)
_MUTATION = 0x500D0156  # GENERIC_ALL/WRITE, DELETE, WRITE_DAC/OWNER, file/dir writes
# OS authority, not ordinary-user groups or localized account names. These
# principals can already administer the machine; do not mistake their ACEs on
# C:\, Users, or a profile for an untrusted cross-user grant.
_OS_AUTHORITIES = frozenset({
    "S-1-5-18",  # LocalSystem
    "S-1-5-32-544",  # BUILTIN\Administrators
    "S-1-5-80-956008885-3418522649-1831038044-1853292631-2271478464",  # TrustedInstaller
})
# Ancestors above the state/native root may allow creating a sibling directory
# (e.g. Authenticated Users on C:\). This does not grant mutation of the parent
# or an existing child. FILE_ADD_FILE == FILE_WRITE_DATA is NOT exempt: MS-FSA
# 2.1.5.10.37 permits setting a reparse point with it OR FILE_WRITE_ATTRIBUTES.
_TRAVERSAL_MUTATION = _MUTATION & ~4
_INVALID = C.c_void_p(-1).value
_READ = 0x80000000
_WRITE = 0x40000000
_READ_CONTROL = 0x20000
_ATTRIBUTES = 0x80
_SHARE_RW = 3  # Deliberately no FILE_SHARE_DELETE.
_REPARSE = 0x400
_DIRECTORY = 0x10
_OPEN_REPARSE = 0x00200000
_BACKUP_SEMANTICS = 0x02000000


def validate_session_id(value: str) -> None:
    """Do not merge distinct exact IDs through Win32 case/device aliases."""
    if value != value.lower() or _RESERVED.fullmatch(value):
        raise ValueError("Windows session_id must be lowercase and not a reserved device name")


def _local_path(value: Path) -> Path:
    path = PureWindowsPath(str(value))
    if (not path.is_absolute() or not re.fullmatch(r"[A-Za-z]:", path.drive)
            or len(path.parts) > 128):
        raise ValueError("Windows ownership requires a bounded absolute local drive path")
    for part in path.parts[1:]:
        if (part in {".", ".."} or part.endswith((".", " "))
                or any(char in part for char in '<>:"|?*\x00')
                or any(ord(char) < 32 for char in part)
                or _RESERVED.fullmatch(part.split(".")[0])):
            raise ValueError("unsafe Windows ownership path component")
    return value


def _absolute(value: Path) -> Path:
    # Do not resolve/normalize away a reparse point or ".." before inspection.
    return _local_path(value if value.is_absolute() else Path.cwd() / value)


class _API:
    """Declare every ABI, in particular pointer-sized HANDLE returns on Win64."""

    def __init__(self) -> None:
        if os.name != "nt":
            raise RuntimeError("native Windows ownership is unavailable on this platform")
        self.kernel = C.WinDLL("kernel32", use_last_error=True)
        self.security = C.WinDLL("advapi32", use_last_error=True)
        signatures = {
            "CreateFileW": (HANDLE, [C.c_wchar_p, DWORD, DWORD, C.POINTER(SECURITY_ATTRIBUTES), DWORD, DWORD, HANDLE]),
            "CreateDirectoryW": (BOOL, [C.c_wchar_p, C.POINTER(SECURITY_ATTRIBUTES)]),
            "CloseHandle": (BOOL, [HANDLE]),
            "SetHandleInformation": (BOOL, [HANDLE, DWORD, DWORD]),
            "GetCurrentProcess": (HANDLE, []),
            "GetFileInformationByHandle": (BOOL, [HANDLE, C.POINTER(FILE_INFO)]),
            "GetFinalPathNameByHandleW": (DWORD, [HANDLE, C.c_wchar_p, DWORD, DWORD]),
            "GetVolumeInformationByHandleW": (BOOL, [HANDLE, C.c_wchar_p, DWORD, PDWORD, PDWORD, PDWORD, C.c_wchar_p, DWORD]),
            "GetDriveTypeW": (DWORD, [C.c_wchar_p]),
            "LockFileEx": (BOOL, [HANDLE, DWORD, DWORD, DWORD, DWORD, C.POINTER(OVERLAPPED)]),
            "UnlockFileEx": (BOOL, [HANDLE, DWORD, DWORD, DWORD, C.POINTER(OVERLAPPED)]),
            "ReadFile": (BOOL, [HANDLE, PVOID, DWORD, PDWORD, PVOID]),
            "WriteFile": (BOOL, [HANDLE, PVOID, DWORD, PDWORD, PVOID]),
            "FlushFileBuffers": (BOOL, [HANDLE]),
            "SetFileInformationByHandle": (BOOL, [HANDLE, C.c_int, PVOID, DWORD]),
            "DeleteFileW": (BOOL, [C.c_wchar_p]),
            "LocalFree": (PVOID, [PVOID]),
            "OpenProcessToken": (BOOL, [HANDLE, DWORD, C.POINTER(HANDLE)]),
            "GetTokenInformation": (BOOL, [HANDLE, DWORD, PVOID, DWORD, PDWORD]),
            "ConvertSidToStringSidW": (BOOL, [PVOID, C.POINTER(C.c_wchar_p)]),
            "ConvertStringSecurityDescriptorToSecurityDescriptorW": (BOOL, [C.c_wchar_p, DWORD, C.POINTER(PVOID), PDWORD]),
            "GetSecurityInfo": (DWORD, [HANDLE, DWORD, DWORD, C.POINTER(PVOID), C.POINTER(PVOID), C.POINTER(PVOID), C.POINTER(PVOID), C.POINTER(PVOID)]),
            "GetSecurityDescriptorControl": (BOOL, [PVOID, C.POINTER(WORD), PDWORD]),
            "GetAclInformation": (BOOL, [PVOID, PVOID, DWORD, DWORD]),
            "GetAce": (BOOL, [PVOID, DWORD, C.POINTER(PVOID)]),
            "IsValidSid": (BOOL, [PVOID]),
        }
        for name, (result, args) in signatures.items():
            dll = self.kernel if hasattr(self.kernel, name) else self.security
            function = getattr(dll, name)
            function.restype, function.argtypes = result, args
            setattr(self, name, function)
        self.sid = self._user_sid()

    @staticmethod
    def ok(value: object) -> None:
        if not value:
            raise C.WinError(C.get_last_error())

    def close(self, handle: int) -> None:
        self.ok(self.CloseHandle(handle))

    def sid_text(self, sid: PVOID) -> str:
        self.ok(self.IsValidSid(sid))
        text = C.c_wchar_p()
        self.ok(self.ConvertSidToStringSidW(sid, C.byref(text)))
        try:
            return text.value
        finally:
            self.LocalFree(C.cast(text, PVOID))

    def _user_sid(self) -> str:
        token = HANDLE()
        self.ok(self.OpenProcessToken(self.GetCurrentProcess(), 8, C.byref(token)))
        try:
            size = DWORD()
            if self.GetTokenInformation(token, 1, None, 0, C.byref(size)):
                raise RuntimeError("unexpected empty token information")
            if C.get_last_error() != 122:  # ERROR_INSUFFICIENT_BUFFER only.
                raise C.WinError(C.get_last_error())
            buffer = C.create_string_buffer(size.value)
            self.ok(self.GetTokenInformation(token, 1, buffer, size, C.byref(size)))
            return self.sid_text(C.cast(buffer, C.POINTER(PVOID))[0])
        finally:
            self.close(token)

    @contextmanager
    def private_security(self, *, directory: bool = False):
        descriptor = PVOID()
        inheritance = "OICI" if directory else ""
        self.ok(self.ConvertStringSecurityDescriptorToSecurityDescriptorW(
            f"O:{self.sid}D:P(A;{inheritance};FA;;;{self.sid})",
            1, C.byref(descriptor), None,
        ))
        try:
            yield SECURITY_ATTRIBUTES(C.sizeof(SECURITY_ATTRIBUTES), descriptor, False)
        finally:
            self.LocalFree(descriptor)

    def validate_acl(self, handle: int, *, private: bool, traversal: bool = False) -> None:
        from .shared_state import SharedStateError

        owner, dacl, descriptor = PVOID(), PVOID(), PVOID()
        error = self.GetSecurityInfo(handle, 1, 5, C.byref(owner), None, C.byref(dacl), None, C.byref(descriptor))
        if error:
            raise C.WinError(error)
        try:
            control, revision = WORD(), DWORD()
            self.ok(self.GetSecurityDescriptorControl(descriptor, C.byref(control), C.byref(revision)))
            valid_owners = {self.sid} if private else {self.sid, *_OS_AUTHORITIES}
            if self.sid_text(owner) not in valid_owners or not dacl:
                raise SharedStateError("Windows state requires trusted ownership and a non-null DACL")
            if private and not control.value & 0x1000:  # SE_DACL_PROTECTED
                raise SharedStateError("Windows private state requires a protected DACL")
            info = ACL_INFO()
            self.ok(self.GetAclInformation(dacl, C.byref(info), C.sizeof(info), 2))
            for index in range(info.count):
                pointer = PVOID()
                self.ok(self.GetAce(dacl, index, C.byref(pointer)))
                ace = C.cast(pointer, C.POINTER(ACE)).contents
                # Standard allow/deny only; unknown/object/callback ACEs fail
                # closed rather than guessing the effective permissions.
                if ace.kind not in {0, 1} or ace.size < C.sizeof(ACE):
                    raise SharedStateError("unsupported Windows state ACE")
                if not private and ace.flags & 8:  # INHERIT_ONLY_ACE
                    # No access to this directory. Each opened child is checked
                    # separately; newly created children get protected DACLs.
                    continue
                sid = PVOID(pointer.value + ACE.sid.offset)
                allowed = {self.sid, *_OS_AUTHORITIES, "S-1-3-4"}  # OWNER RIGHTS, verified above.
                if ace.kind == 0 and self.sid_text(sid) not in allowed:
                    # Private state also rejects inherit-only ordinary-user
                    # grants. For nonprivate traversal consider effective rights.
                    mutation = _TRAVERSAL_MUTATION if traversal else _MUTATION
                    if private or ace.mask & mutation:
                        raise SharedStateError("Windows state DACL grants foreign access")
        finally:
            self.LocalFree(descriptor)

    def info(self, handle: int, path: Path, *, directory: bool) -> FILE_INFO:
        from .shared_state import SharedStateError

        info = FILE_INFO()
        self.ok(self.GetFileInformationByHandle(handle, C.byref(info)))
        if info.attributes & _REPARSE or bool(info.attributes & _DIRECTORY) != directory:
            raise SharedStateError("Windows state path is a reparse point or has the wrong type")
        if not directory and info.links != 1:
            raise SharedStateError("Windows state file has a hardlink alias")
        # Reject short-name aliases and case collisions in exact session IDs.
        buffer = C.create_unicode_buffer(32768)
        count = self.GetFinalPathNameByHandleW(handle, buffer, len(buffer), 0)
        if not count or count >= len(buffer):
            raise C.WinError(C.get_last_error())
        actual = buffer.value.removeprefix("\\\\?\\")
        if actual.casefold() != str(path).casefold():
            raise SharedStateError("Windows state path has an alternate namespace alias")
        if not directory and PureWindowsPath(actual).name != path.name:
            raise SharedStateError("Windows state file has an exact-name collision")
        return info

    def open(self, path: Path, *, directory: bool = False, create: bool = False,
             exclusive: bool = False, write: bool = False, advisory_read: bool = False,
             owner_temporary: bool = False) -> int:
        if advisory_read and (path.name != "owner.json" or directory or create or exclusive or write):
            raise ValueError("delete-sharing is restricted to a replaceable advisory owner reader")
        if owner_temporary and (directory or create or advisory_read or not exclusive or not write
                                or not re.fullmatch(r"\.owner-[0-9a-f]{32}\.tmp", path.name)):
            raise ValueError("rename access is restricted to a new private owner temporary file")
        access = _READ_CONTROL | _ATTRIBUTES | (0 if directory else _READ)
        if write:
            access |= _WRITE
        if owner_temporary:
            access |= 0x10000  # DELETE on this source handle, not the stable lock.
        with self.private_security(directory=directory) as security:
            # Pins deny deletion/rename. Sharing restrictions do NOT cover
            # FILE_WRITE_ATTRIBUTES: traversal ACL checks prevent untrusted
            # reparse conversion, not this share mode (MS-FSA 2.1.5.10.37).
            share = 1 if directory else _SHARE_RW
            if advisory_read:
                share |= 4  # FILE_SHARE_DELETE permits atomic owner replacement.
            handle = self.CreateFileW(str(path), access, share, C.byref(security),
                                     1 if exclusive else (4 if create else 3),
                                     _OPEN_REPARSE | _BACKUP_SEMANTICS, None)
            # LocalFree on context exit is another Win32 call. Preserve this
            # error before cleanup so missing/access-denied are not misreported.
            error = C.get_last_error() if handle == _INVALID else 0
        if handle == _INVALID:
            raise C.WinError(error)
        try:
            self.ok(self.SetHandleInformation(handle, 1, 0))
            self.info(handle, path, directory=directory)
            return handle
        except BaseException:
            self.close(handle)
            raise

    def replace_owner(self, handle: int, path: Path) -> None:
        """Supersede only advisory owner.json, keeping existing readers valid.

        FileRenameInfoEx + REPLACE_IF_EXISTS | POSIX_SEMANTICS is available from
        Windows 10 1607. Unlike MoveFileExW, it explicitly allows an open target:
        old handles keep reading the old file, new opens see the complete new
        file. The source stays open with DELETE access through the rename.
        https://learn.microsoft.com/windows-hardware/drivers/ddi/ntifs/ns-ntifs-_file_rename_information
        """
        if path.name != "owner.json":
            raise ValueError("POSIX replacement is restricted to advisory owner.json")
        encoded = str(_absolute(path)).encode("utf-16-le")
        buffer = C.create_string_buffer(C.sizeof(FILE_RENAME_INFO) + len(encoded))
        info = FILE_RENAME_INFO.from_buffer(buffer)
        info.Flags = 3  # FILE_RENAME_REPLACE_IF_EXISTS | FILE_RENAME_POSIX_SEMANTICS
        info.FileNameLength = len(encoded)  # Bytes, excluding any NUL terminator.
        C.memmove(C.addressof(buffer) + FILE_RENAME_INFO.FileName.offset, encoded, len(encoded))
        # RootDirectory is NULL: the absolute target is in the same pinned
        # directory/volume as our freshly created source. No copy/delete fallback.
        try:
            self.ok(self.SetFileInformationByHandle(handle, 22, buffer, len(buffer)))  # FileRenameInfoEx
        except OSError as exc:
            if exc.winerror in {1, 50, 87, 120}:
                exc.add_note("Advisory replacement requires Windows 10 1607+ local NTFS with "
                             "FileRenameInfoEx POSIX semantics; no legacy fallback is attempted.")
            raise


class _PinnedDirectories:
    def __init__(self, api: _API, directory: Path, *, owned_from: Path, private: bool,
                 create: bool = False) -> None:
        from .shared_state import SharedStateError

        self.api, self.handles, self.missing = api, [], False
        directory = _absolute(directory)
        owned_from = _absolute(owned_from)
        if not directory.is_relative_to(owned_from):
            raise ValueError("owned path is outside its configured root")
        chain = list(reversed([directory, *directory.parents]))
        try:
            if api.GetDriveTypeW(str(chain[0])) != 3:  # DRIVE_FIXED
                raise SharedStateError("Windows ownership requires a local fixed NTFS volume")
            for path in chain:
                try:
                    handle = api.open(path, directory=True)
                except OSError as exc:
                    if exc.winerror not in {2, 3}:
                        raise
                    if not create:
                        self.missing = True
                        break
                    with api.private_security(directory=True) as security:
                        if not api.CreateDirectoryW(str(path), C.byref(security)):
                            if C.get_last_error() != 183:  # Raced another private creator.
                                raise C.WinError(C.get_last_error())
                    handle = api.open(path, directory=True)
                self.handles.append(handle)
                if len(self.handles) == 1:
                    filesystem = C.create_unicode_buffer(32)
                    api.ok(api.GetVolumeInformationByHandleW(
                        handle, None, 0, None, None, None, filesystem, len(filesystem),
                    ))
                    if filesystem.value != "NTFS":
                        raise SharedStateError("Windows ownership requires local NTFS")
                owned = path.is_relative_to(owned_from)
                # Validate every ancestor before opening/creating its child.
                # The pins prevent replacement; ACLs reject untrusted attribute
                # mutation even where Win32 sharing would allow it.
                try:
                    api.validate_acl(handle, private=private if owned else False, traversal=not owned)
                except SharedStateError as exc:
                    raise SharedStateError(f"unsafe Windows traversal directory {path}: {exc}") from exc
                if owned:
                    # Windows directories may be case-sensitive. Reject an
                    # existing differently-cased owned component, never merge it.
                    buffer = C.create_unicode_buffer(32768)
                    count = api.GetFinalPathNameByHandleW(handle, buffer, len(buffer), 0)
                    if not count or count >= len(buffer):
                        raise C.WinError(C.get_last_error())
                    if PureWindowsPath(buffer.value).name != path.name:
                        raise SharedStateError("Windows state directory has an exact-name collision")
        except BaseException:
            self.close()
            raise

    def close(self) -> None:
        while self.handles:
            self.api.close(self.handles.pop())


@contextmanager
def _pinned(api: _API, directory: Path, *, root: Path, private: bool = True, create: bool = False):
    pins = _PinnedDirectories(api, directory, owned_from=root, private=private, create=create)
    try:
        yield pins
    finally:
        pins.close()


def read_bytes(path: Path, *, root: Path, limit: int, private_dirs: bool = True) -> bytes | None:
    """Read only from validated, pinned handles. Missing ancestors create nothing."""
    api = _API()
    with _pinned(api, path.parent, root=root, private=private_dirs) as pins:
        if pins.missing:
            return None
        try:
            if path.name == "owner.json":
                handle = api.open(_absolute(path), advisory_read=True)
            else:
                handle = api.open(_absolute(path))
        except OSError as exc:
            if exc.winerror in {2, 3}:
                return None
            raise
        try:
            api.validate_acl(handle, private=True)
            result = bytearray()
            while len(result) <= limit:
                buffer = C.create_string_buffer(min(65536, limit + 1 - len(result)))
                count = DWORD()
                api.ok(api.ReadFile(handle, buffer, len(buffer), C.byref(count), None))
                if count.value == 0:
                    break
                result.extend(buffer.raw[:count.value])
            return bytes(result)
        finally:
            api.close(handle)


def stamp(path: Path, *, root: Path):
    """Handle-validated metadata only; the FILETIME epoch is converted to Unix ns."""
    from .shared_state import FileStamp

    api = _API()
    with _pinned(api, path.parent, root=root) as pins:
        if pins.missing:
            return None
        try:
            handle = api.open(_absolute(path))
        except OSError as exc:
            if exc.winerror in {2, 3}:
                return None
            raise
        try:
            api.validate_acl(handle, private=True)
            info = api.info(handle, _absolute(path), directory=False)

            def ns(time: FILETIME) -> int:
                return (((time.high << 32) | time.low) - 116444736000000000) * 100

            return FileStamp(info.volume, (info.index_high << 32) | info.index_low,
                             (info.size_high << 32) | info.size_low, ns(info.write), ns(info.creation))
        finally:
            api.close(handle)


def list_ids(directory: Path, *, root: Path) -> list[str]:
    """Enumerate only pinned private state, validating every returned checkpoint."""
    from .shared_state import _ID_RE

    directory = _absolute(directory)
    api = _API()
    with _pinned(api, directory, root=root) as pins:
        if pins.missing:
            return []
        result = []
        for entry in directory.iterdir():
            if _ID_RE.fullmatch(entry.name):
                validate_session_id(entry.name)
                if stamp(entry / "checkpoint.json", root=root) is not None:
                    result.append(entry.name)
        return sorted(result)


class Ownership:
    """One non-inheritable lock handle plus its delete-denying ancestor pins."""

    def __init__(self, directory: Path, *, root: Path) -> None:
        self.api = _API()
        self.directory = _absolute(directory)
        self.pins = _PinnedDirectories(self.api, self.directory, owned_from=root,
                                      private=True, create=True)
        self.handle = None
        self.overlapped = OVERLAPPED()
        try:
            self.handle = self.api.open(self.directory / "session.lock", create=True, write=True)
            self.api.validate_acl(self.handle, private=True)
            if not self.api.LockFileEx(self.handle, 3, 0, 1, 0, C.byref(self.overlapped)):
                error = C.get_last_error()
                if error == 33:  # ERROR_LOCK_VIOLATION, not access denied.
                    raise BlockingIOError("stable Windows session lock is held")
                raise C.WinError(error)
        except BaseException:
            self.close()
            raise

    def check(self) -> None:
        if self.handle is None:
            raise RuntimeError("Windows session ownership is closed")
        self.api.info(self.handle, self.directory / "session.lock", directory=False)
        self.api.validate_acl(self.handle, private=True)

    def publish_owner(self, encoded: bytes) -> None:
        """Complete atomic advisory publication only; no directory durability claim."""
        self.check()
        path = self.directory / "owner.json"
        # Refuse an unsafe existing target before replacing its name.
        try:
            existing = self.api.open(path, advisory_read=True)
        except OSError as exc:
            if exc.winerror not in {2, 3}:
                raise
        else:
            try:
                self.api.validate_acl(existing, private=True)
            finally:
                self.api.close(existing)
        temporary = self.directory / f".owner-{uuid.uuid4().hex}.tmp"
        handle = self.api.open(temporary, exclusive=True, write=True, owner_temporary=True)
        try:
            written = 0
            while written < len(encoded):
                chunk = encoded[written:written + 65536]
                buffer, count = C.create_string_buffer(chunk), DWORD()
                self.api.ok(self.api.WriteFile(handle, buffer, len(chunk), C.byref(count), None))
                if not count.value:
                    raise OSError("zero-length Windows owner write")
                written += count.value
            self.api.ok(self.api.FlushFileBuffers(handle))
            self.api.replace_owner(handle, path)
        except BaseException as exc:
            # Preserve the actual write/flush/rename failure even when cleanup
            # also fails. Report retained temporary-file cleanup as exception
            # notes, never as a successful publication or a masked native error.
            try:
                self.api.close(handle)
            except OSError as cleanup:
                exc.add_note(f"Owner temporary handle close failed: {cleanup}")
            try:
                self.api.ok(self.api.DeleteFileW(str(temporary)))
            except OSError as cleanup:
                exc.add_note(f"Owner temporary file cleanup failed ({temporary}): {cleanup}")
            raise
        self.api.close(handle)

    def close(self) -> None:
        # Closing releases LockFileEx even on failure paths and process death.
        try:
            if self.handle is not None:
                self.api.close(self.handle)
                self.handle = None
        finally:
            self.pins.close()