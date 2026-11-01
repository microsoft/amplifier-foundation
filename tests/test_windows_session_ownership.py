"""Portable failure tests plus actual Windows/NTFS ownership qualification.

Native tests deliberately use only pytest temporary directories, not live state.
Skipped native tests are not evidence that the Win32 implementation ran.
"""

from __future__ import annotations

import copy
import ctypes as C
import json
import os
import pickle
import subprocess
import sys
from pathlib import Path, PureWindowsPath

import pytest

from amplifier_foundation.session import shared_state as shared
from amplifier_foundation.session import _windows_ownership as win
from amplifier_foundation.session import _windows_history as history_win

native = pytest.mark.skipif(os.name != "nt", reason="requires actual Windows local NTFS")


class _WindowsOS:
    name = "nt"

    def __getattr__(self, name):
        return getattr(os, name)


def _store(tmp_path, monkeypatch):
    monkeypatch.setenv("AMPLIFIER_HOME", str(tmp_path / "native"))
    workspace = tmp_path / "workspace"
    workspace.mkdir(exist_ok=True)
    return shared.SharedSessionStore(workspace, "root-1", root=tmp_path / "state")


@pytest.mark.parametrize("value", ["CON", "con", "nul", "aux", "prn", "com1", "lpt9", "Root-1"])
def test_windows_id_aliases_are_rejected_portably(value):
    with pytest.raises(ValueError, match="lowercase|reserved"):
        win.validate_session_id(value)


@pytest.mark.parametrize("value", ["root-1", "018c1234-5678-7abc-8def-0123456789ab", "con-1", "com10"])
def test_lowercase_non_device_ids_keep_exact_spelling(value):
    win.validate_session_id(value)


@pytest.mark.parametrize("value", [
    r"\\server\share\state", r"\\?\C:\state", r"C:state", r"C:\state\..\other",
    r"C:\nul\state", r"C:\state:stream", "C:\\state.\\child", "C:\\state \\child",
    r"C:\COM1.txt", "C:\\" + "\\".join(["d"] * 129),
])
def test_windows_path_aliases_are_rejected_before_io(value):
    with pytest.raises(ValueError):
        win._local_path(PureWindowsPath(value))


def test_win64_struct_layout_does_not_depend_on_host_long_width():
    assert C.sizeof(win.DWORD) == 4
    assert C.sizeof(win.FILE_INFO) == 52
    assert win.ACE.sid.offset == 8
    if C.sizeof(C.c_void_p) == 8:
        assert C.sizeof(win.OVERLAPPED) == 32
        assert win.OVERLAPPED.hEvent.offset == 24
        assert C.sizeof(win.SECURITY_ATTRIBUTES) == 24


def test_rename_info_abi_uses_win32_utf16_and_pointer_sized_handle():
    assert win.FILE_RENAME_INFO.FileName.offset == (20 if C.sizeof(win.HANDLE) == 8 else 12)
    assert win.FILE_RENAME_INFO.RootDirectory.offset == (8 if C.sizeof(win.HANDLE) == 8 else 4)
    assert C.sizeof(win.FILE_RENAME_INFO) == (24 if C.sizeof(win.HANDLE) == 8 else 16)
    assert C.sizeof(dict(win.FILE_RENAME_INFO._fields_)["FileName"]._type_) == 2
    assert C.sizeof(win._IO_STATUS_VALUE) == C.sizeof(win.HANDLE)
    assert win.IO_STATUS_BLOCK.Information.offset == C.sizeof(win.HANDLE)
    assert C.sizeof(win.IO_STATUS_BLOCK) == 2 * C.sizeof(win.HANDLE)
    assert C.sizeof(dict(win._IO_STATUS_VALUE._fields_)["Status"]) == 4


@pytest.mark.parametrize("error", [0, 1, 5, 32, 50, 87, 120])
def test_owner_rename_uses_posix_replace_flags_and_preserves_native_failure(monkeypatch, error):
    api = win._API.__new__(win._API)
    handle = (1 << 40) if C.sizeof(win.HANDLE) == 8 else 123
    target = PureWindowsPath("C:\\private-\N{SNOWMAN}-\U0001f680\\owner.json")
    calls = []

    def source_name(actual, output, size, flags):
        assert actual == handle and flags == 0
        output.value = "\\\\?\\" + str(target.parent / (".owner-" + "a" * 32 + ".tmp"))
        return len(output.value)

    def rename(actual, result, buffer, size, info_class):
        assert actual == handle
        assert info_class == 65  # Native FileRenameInformationEx, NOT Win32 class 22.
        assert isinstance(C.cast(result, C.POINTER(win.IO_STATUS_BLOCK)).contents,
                          win.IO_STATUS_BLOCK)
        info = C.cast(buffer, C.POINTER(win.FILE_RENAME_INFO)).contents
        assert info.Flags == 3  # REPLACE_IF_EXISTS | POSIX_SEMANTICS only.
        assert not info.RootDirectory
        raw = C.string_at(C.cast(buffer, win.PVOID).value + win.FILE_RENAME_INFO.FileName.offset,
                          info.FileNameLength)
        assert raw == target.name.encode("utf-16-le")
        assert size >= win.FILE_RENAME_INFO.FileName.offset + len(raw)
        calls.append("renamed")
        return -1073741790 if error else 0  # A signed NTSTATUS, not BOOL/GetLastError.

    failure = OSError("native rename failure")
    failure.winerror = error
    api.GetFinalPathNameByHandleW = source_name
    api.NtSetInformationFile = rename
    api.RtlNtStatusToDosError = lambda status: error if status == -1073741790 else pytest.fail("wrong status")
    api.SetFileInformationByHandle = lambda *a: pytest.fail("absolute Win32 fallback")
    monkeypatch.setattr(C, "get_last_error", lambda: 999, raising=False)
    monkeypatch.setattr(C, "WinError", lambda code: failure if code == error else pytest.fail("wrong mapped WinError"),
                        raising=False)
    if error:
        with pytest.raises(OSError) as caught:
            api.replace_owner(handle, target)
        assert caught.value is failure
        if error in {1, 50, 87, 120}:
            assert any("FileRenameInformationEx" in note for note in caught.value.__notes__)
        else:
            assert not getattr(caught.value, "__notes__", [])  # Access denied stays access denied.
    else:
        api.replace_owner(handle, target)
    assert calls == ["renamed"]  # No fallback, retry or delete/truncate path.


def test_rename_api_binding_uses_pointer_width_handle_and_fixed_win32_arguments(monkeypatch):
    class Function:
        pass

    class DLL:
        def __init__(self):
            self.functions = {}

        def __getattr__(self, name):
            return self.functions.setdefault(name, Function())

    kernel = DLL()
    monkeypatch.setattr(win, "os", _WindowsOS())
    monkeypatch.setattr(C, "WinDLL", lambda *a, **kw: kernel, raising=False)
    monkeypatch.setattr(win._API, "_user_sid", lambda self: "S-1-5-21-1-2-3-1000")
    api = win._API()
    assert api.SetFileInformationByHandle.restype is win.BOOL
    assert api.SetFileInformationByHandle.argtypes == [win.HANDLE, C.c_int, win.PVOID, win.DWORD]
    assert api.NtSetInformationFile.restype is C.c_int32
    assert api.NtSetInformationFile.argtypes == [
        win.HANDLE, C.POINTER(win.IO_STATUS_BLOCK), win.PVOID, win.DWORD, C.c_int,
    ]
    assert api.RtlNtStatusToDosError.restype is win.DWORD
    assert api.RtlNtStatusToDosError.argtypes == [C.c_int32]


@pytest.mark.parametrize("name", ["session.lock", "checkpoint.json", "transfer-fence.json"])
def test_posix_replace_helper_cannot_target_lock_or_authority(name):
    api = win._API.__new__(win._API)
    with pytest.raises(ValueError, match="advisory"):
        api.replace_owner(123, PureWindowsPath(r"C:\state") / name)


@pytest.mark.parametrize("owner_temporary", [False, True])
def test_delete_access_is_only_requested_for_explicit_owner_temporary(monkeypatch, owner_temporary):
    from contextlib import contextmanager

    api = win._API.__new__(win._API)

    @contextmanager
    def security(**kwargs):
        assert kwargs == {"directory": False}
        yield win.SECURITY_ATTRIBUTES()

    def create(path, access, share, descriptor, disposition, flags, template):
        assert bool(access & 0x10000) is owner_temporary
        assert access & win._WRITE
        assert share == 3 and disposition == 1  # CREATE_NEW, deny delete sharing.
        return 123

    api.private_security, api.CreateFileW = security, create
    api.SetHandleInformation = lambda *a: 1
    api.info = lambda *a, **kw: None
    path = PureWindowsPath(r"C:\state") / (".owner-" + "a" * 32 + ".tmp")
    assert api.open(path, exclusive=True, write=True, owner_temporary=owner_temporary) == 123


@pytest.mark.parametrize("name,kwargs", [
    ("session.lock", {"exclusive": True, "write": True}),
    ("owner.json", {"exclusive": True, "write": True}),
    (".owner-invalid.tmp", {"exclusive": True, "write": True}),
    (".owner-" + "a" * 32 + ".tmp", {"exclusive": True}),
    (".owner-" + "a" * 32 + ".tmp", {"write": True}),
    (".owner-" + "a" * 32 + ".tmp", {"exclusive": True, "write": True, "directory": True}),
])
def test_owner_temporary_delete_access_cannot_be_requested_for_other_objects(name, kwargs):
    api = win._API.__new__(win._API)
    with pytest.raises(ValueError, match="owner temporary"):
        api.open(PureWindowsPath(r"C:\state") / name, owner_temporary=True, **kwargs)


@pytest.mark.parametrize("directory", [False, True])
@pytest.mark.parametrize("error", [0, 2, 5])
def test_creation_supplies_private_descriptor_before_createfile_and_clears_inherit(directory, error, monkeypatch):
    api = win._API.__new__(win._API)
    api.sid = "S-1-5-21-1-2-3-1000"
    events = []
    last_error = [0]
    handle = 1 << 40 if C.sizeof(C.c_void_p) == 8 else 123

    def descriptor(sddl, revision, out, length):
        flags = "OICI" if directory else ""
        assert sddl == f"O:{api.sid}D:P(A;{flags};FA;;;{api.sid})"
        C.cast(out, C.POINTER(win.PVOID))[0] = 456
        events.append("protected-descriptor")
        return 1

    def create(path, access, share, sa, disposition, flags, template):
        security = C.cast(sa, C.POINTER(win.SECURITY_ATTRIBUTES)).contents
        assert security.lpSecurityDescriptor == 456 and not security.bInheritHandle
        # Metadata-only opens do not participate in Windows sharing checks.
        # A directory pin needs FILE_LIST_DIRECTORY, not broad GENERIC_READ.
        assert access == win._READ_CONTROL | win._ATTRIBUTES | (1 if directory else win._READ)
        assert share == (1 if directory else 3) and disposition == 1
        assert flags & win._OPEN_REPARSE
        events.append("create")
        last_error[0] = error
        return win._INVALID if error else handle

    def inheritance(actual, mask, flags):
        assert actual == handle and mask == 1 and flags == 0
        events.append("noninherit")
        return 1

    api.ConvertStringSecurityDescriptorToSecurityDescriptorW = descriptor
    api.CreateFileW, api.SetHandleInformation = create, inheritance

    def free(descriptor):
        events.append("free")
        last_error[0] = 0  # Cleanup must not erase the failing API's error.

    def winerror(code):
        failure = OSError(f"native error {code}")
        failure.winerror = code
        return failure

    api.LocalFree = free
    api.info = lambda *args, **kwargs: None
    monkeypatch.setattr(C, "get_last_error", lambda: last_error[0], raising=False)
    monkeypatch.setattr(C, "WinError", winerror, raising=False)
    if error:
        with pytest.raises(OSError) as caught:
            api.open(PureWindowsPath(r"C:\state\file"), directory=directory, exclusive=True)
        assert caught.value.winerror == error
        assert events == ["protected-descriptor", "create", "free"]
    else:
        assert api.open(PureWindowsPath(r"C:\state\file"), directory=directory, exclusive=True) == handle
        assert events == ["protected-descriptor", "create", "free", "noninherit"]


@pytest.mark.parametrize("error", [0, 5, 33, 50])
def test_only_lock_violation_is_busy_and_failure_closes_all_handles(monkeypatch, error):
    events = []

    class API:
        def open(self, *args, **kwargs):
            events.append("open")
            return 123

        def validate_acl(self, *args, **kwargs):
            pass

        def LockFileEx(self, handle, flags, reserved, low, high, overlapped):
            assert (handle, flags, reserved, low, high) == (123, 3, 0, 1, 0)
            assert C.cast(overlapped, C.POINTER(win.OVERLAPPED)).contents.Offset == 0
            return error == 0

        def close(self, handle):
            events.append(("close", handle))

    class Pins:
        def __init__(self, *args, **kwargs):
            pass

        def close(self):
            events.append("unpin")

    def winerror(code):
        failure = OSError(f"native error {code}")
        failure.winerror = code
        return failure

    monkeypatch.setattr(win, "_API", API)
    monkeypatch.setattr(win, "_PinnedDirectories", Pins)
    monkeypatch.setattr(C, "get_last_error", lambda: error, raising=False)
    monkeypatch.setattr(C, "WinError", winerror, raising=False)
    directory = PureWindowsPath(r"C:\state")
    if error:
        with pytest.raises(BlockingIOError if error == 33 else OSError) as caught:
            win.Ownership(directory, root=directory)
        if error != 33:
            assert not isinstance(caught.value, BlockingIOError)
            assert caught.value.winerror == error
    else:
        ownership = win.Ownership(directory, root=directory)
        ownership.close()
    assert events == ["open", ("close", 123), "unpin"]


class _StubOwnership:
    events = []
    failure = None

    def __init__(self, directory, *, root):
        self.events.append("open")
        if self.failure is not None:
            raise self.failure

    def check(self):
        self.events.append("check")

    def publish_owner(self, encoded):
        self.events.append(("publish", json.loads(encoded)))

    def close(self):
        self.events.append("close")


@pytest.fixture
def simulated(tmp_path, monkeypatch):
    store = _store(tmp_path, monkeypatch)
    monkeypatch.setattr(shared, "os", _WindowsOS())
    monkeypatch.setattr(win, "Ownership", _StubOwnership)
    monkeypatch.setattr(_StubOwnership, "events", [])
    monkeypatch.setattr(_StubOwnership, "failure", None)
    monkeypatch.setattr(store, "transfer_fence", lambda: None)
    monkeypatch.setattr(shared, "_atomic_json", lambda *a: pytest.fail("POSIX persistence reached"))
    return store


def test_first_slice_authority_refusals_precede_any_authority_io(simulated, monkeypatch):
    store = simulated
    held = store.acquire(app="fixture")
    recovery = shared.HeldTransfer(store, -1, {}, "transfer-1")
    operations = [
        lambda: held.write([], bundle="test"), held.delete_checkpoint,
        lambda: held.fence_transfer("transfer-1", "destination"),
        lambda: store._save_transfer_fence({}),
        lambda: store.acquire_transfer("transfer-1", app="test"),
        lambda: store.confirm_transfer_commit("transfer-1", app="test"),
        recovery.commit_transfer, recovery.clear_transfer,
        lambda: store._acquire(app="test", diagnostics={}, transfer_id="transfer-1"),
    ]
    monkeypatch.setattr(store, "_transfer_directory", lambda **kw: pytest.fail("native tree changed"))
    try:
        for operation in operations:
            with pytest.raises(shared.SharedStateError, match="namespace persistence is not qualified"):
                operation()
        assert not store.root.exists()
        assert not store.transfer_fence_path.parent.exists()
        assert _StubOwnership.events.count("open") == 1
    finally:
        held.release()
    assert _StubOwnership.events[-1] == "close"


@pytest.mark.parametrize("failure", ["fenced", "malformed", "access-denied"])
def test_fence_failure_closes_lock_before_owner_publication(simulated, monkeypatch, failure):
    store = simulated
    if failure == "fenced":
        monkeypatch.setattr(store, "transfer_fence", lambda: {"phase": "staged"})
        expected = shared.SessionTransferFencedError
    else:
        error = shared.SharedStateError("malformed") if failure == "malformed" else PermissionError("denied")

        def fail():
            raise error

        monkeypatch.setattr(store, "transfer_fence", fail)
        expected = type(error)
    with pytest.raises(expected):
        store.acquire(app="fixture")
    assert _StubOwnership.events == ["open", "close"]


def test_native_access_denied_is_neither_busy_nor_unsupported(simulated):
    _StubOwnership.failure = PermissionError("genuine access denied")
    try:
        with pytest.raises(PermissionError, match="genuine access denied"):
            simulated.acquire(app="fixture")
    finally:
        _StubOwnership.failure = None


def test_busy_uses_only_safe_advisory_read_and_never_publishes(simulated, monkeypatch):
    _StubOwnership.failure = BlockingIOError("busy")
    monkeypatch.setattr(shared, "_windows_read_json", lambda *a, **kw: {"active": True, "app": "first"})
    try:
        with pytest.raises(shared.SessionBusyError) as error:
            simulated.acquire(app="contender")
        assert error.value.owner["app"] == "first"
        assert _StubOwnership.events == ["open"]
    finally:
        _StubOwnership.failure = None


def test_stale_pid_copy_and_release_failure_cannot_retain_capability(simulated, monkeypatch):
    held = simulated.acquire(app="first")
    for operation in (copy.copy, copy.deepcopy, pickle.dumps):
        with pytest.raises(TypeError):
            operation(held)
    held._pid = os.getpid() + 1
    with pytest.raises(RuntimeError, match="different process"):
        held.check()
    held._pid = os.getpid()

    def fail(*a):
        raise PermissionError("owner publication failed")

    monkeypatch.setattr(held._windows, "publish_owner", fail)
    with pytest.raises(PermissionError):
        held.release()
    assert not held.active and _StubOwnership.events[-1] == "close"
    for operation in (held.check, lambda: held.write([], bundle="test"), held.delete_checkpoint):
        with pytest.raises(RuntimeError, match="no longer active"):
            operation()
    successor = simulated.acquire(app="second")
    successor.release()


def _acl_api(*, protected=True, null=False, owner="current", foreign_mask=None, kind=0,
             trustee="foreign", flags=0):
    """Exercise the real validation loop with in-memory Win32 descriptors."""
    api = win._API.__new__(win._API)
    api.sid = "current"
    ace = win.ACE(kind, flags, C.sizeof(win.ACE), foreign_mask or 0, 0)

    def security(handle, object_type, information, owner_out, group, dacl_out, sacl, sd_out):
        C.cast(owner_out, C.POINTER(win.PVOID))[0] = 100
        C.cast(dacl_out, C.POINTER(win.PVOID))[0] = None if null else 200
        C.cast(sd_out, C.POINTER(win.PVOID))[0] = 300
        return 0

    def control(sd, out, revision):
        C.cast(out, C.POINTER(win.WORD))[0] = 0x1000 if protected else 0
        return 1

    def acl(dacl, out, size, cls):
        C.cast(out, C.POINTER(win.ACL_INFO))[0].count = 0 if foreign_mask is None else 1
        return 1

    def get_ace(dacl, index, out):
        C.cast(out, C.POINTER(win.PVOID))[0] = C.addressof(ace)
        return 1

    api.GetSecurityInfo, api.GetSecurityDescriptorControl = security, control
    api.GetAclInformation, api.GetAce = acl, get_ace
    api.LocalFree = lambda p: None
    api.sid_text = lambda p: owner if p.value == 100 else trustee
    return api


@pytest.mark.parametrize("kwargs", [
    {"protected": False}, {"null": True}, {"owner": "foreign"},
    {"foreign_mask": 0x80000000}, {"foreign_mask": 0x40000000},
    {"foreign_mask": 1, "kind": 5},
])
def test_private_acl_validation_fails_closed(kwargs):
    with pytest.raises(shared.SharedStateError):
        _acl_api(**kwargs).validate_acl(123, private=True)


@pytest.mark.parametrize("mask", [0x40000000, 0x10000000, 2, 4, 16, 64, 256, 0x10000, 0x40000, 0x80000])
def test_native_directory_acl_rejects_each_foreign_mutation_right(mask):
    with pytest.raises(shared.SharedStateError, match="foreign access"):
        _acl_api(foreign_mask=mask).validate_acl(123, private=False)


def test_native_directory_may_have_foreign_read_but_file_may_not():
    api = _acl_api(protected=False, foreign_mask=0x80000000)
    api.validate_acl(123, private=False)
    with pytest.raises(shared.SharedStateError):
        api.validate_acl(123, private=True)


def test_safe_json_limit_and_malformed_values_use_the_windows_reader(tmp_path, monkeypatch):
    monkeypatch.setattr(win, "read_bytes", lambda *a, **kw: b"{")
    with pytest.raises(shared.SharedStateError, match="valid JSON"):
        shared._windows_read_json(tmp_path / "fixture", root=tmp_path)
    monkeypatch.setattr(win, "read_bytes", lambda *a, **kw: b"[]")
    with pytest.raises(shared.SharedStateError, match="JSON object"):
        shared._windows_read_json(tmp_path / "fixture", root=tmp_path)
    monkeypatch.setattr(shared, "_MAX_CHECKPOINT_BYTES", 1)
    monkeypatch.setattr(win, "read_bytes", lambda *a, **kw: b"{}")
    with pytest.raises(shared.SharedStateError, match="size limit"):
        shared._windows_read_json(tmp_path / "fixture", root=tmp_path)


def test_posix_directory_helper_refuses_windows_before_creating(tmp_path, monkeypatch):
    monkeypatch.setattr(shared, "os", _WindowsOS())
    target = tmp_path / "must-not-create"
    with pytest.raises(shared.SharedStateError, match="handoff is not supported"):
        shared._validate_private_directory(target, create=True)
    with pytest.raises(shared.SharedStateError, match="not qualified"):
        shared._atomic_json(target, {})
    assert not target.exists()


@pytest.mark.parametrize("failure", [None, "write", "flush", "replace"])
@pytest.mark.parametrize("cleanup_failure", [False, True])
def test_owner_publication_completes_partial_writes_or_never_replaces(failure, cleanup_failure):
    events, written = [], bytearray()
    error = PermissionError(f"{failure} failed")

    class API:
        ok = staticmethod(win._API.ok)

        def open(self, path, **kwargs):
            assert path.name != "session.lock"
            if path.name == "owner.json":
                error = FileNotFoundError()
                error.winerror = 2
                raise error
            assert kwargs == {"exclusive": True, "write": True, "owner_temporary": True}
            return 123

        def WriteFile(self, handle, buffer, length, count, overlapped):
            if failure == "write":
                raise error
            size = min(2, length)
            written.extend(C.string_at(buffer, size))
            C.cast(count, win.PDWORD)[0] = size
            return 1

        def FlushFileBuffers(self, handle):
            if failure == "flush":
                raise error
            events.append("flushed")
            return 1

        def close(self, handle):
            events.append(("closed", handle))
            if failure and cleanup_failure:
                raise OSError("close cleanup failed")

        def replace_owner(self, handle, target):
            assert target.name == "owner.json" and handle == 123
            assert written == data and events == ["flushed"]  # Still-open source, flushed first.
            if failure == "replace":
                raise error
            events.append("replaced")

        def DeleteFileW(self, path):
            assert PureWindowsPath(path).name.startswith(".owner-")
            assert path != str(ownership.directory / "owner.json")
            events.append("temporary-deleted")
            if cleanup_failure:
                raise OSError("delete cleanup failed")
            return 1

    ownership = win.Ownership.__new__(win.Ownership)
    ownership.directory, ownership.api = PureWindowsPath(r"C:\state"), API()
    ownership.check = lambda: None
    data = b'{"app":"complete-owner","active":true}'
    if failure:
        with pytest.raises(PermissionError) as caught:
            ownership.publish_owner(data)
        assert caught.value is error
        if cleanup_failure:
            assert len(error.__notes__) == 2
            assert any("close cleanup failed" in note for note in error.__notes__)
            assert any("delete cleanup failed" in note for note in error.__notes__)
        assert "replaced" not in events and events[-1] == "temporary-deleted"
        assert events.count(("closed", 123)) == 1
    else:
        ownership.publish_owner(data)
        assert written == data
        assert events == ["flushed", "replaced", ("closed", 123)]


def test_safe_read_rejects_acl_before_reading_any_foreign_bytes(monkeypatch):
    from contextlib import contextmanager

    closed = []

    class API:
        def open(self, path):
            return 123

        def validate_acl(self, handle, **kwargs):
            raise shared.SharedStateError("foreign ACL")

        def ReadFile(self, *args):
            pytest.fail("read bytes before ACL validation")

        def close(self, handle):
            closed.append(handle)

    @contextmanager
    def pinned(*args, **kwargs):
        class Pins:
            missing = False
        yield Pins()

    monkeypatch.setattr(win, "_API", API)
    monkeypatch.setattr(win, "_pinned", pinned)
    with pytest.raises(shared.SharedStateError, match="foreign ACL"):
        win.read_bytes(PureWindowsPath(r"C:\state\checkpoint.json"),
                       root=PureWindowsPath(r"C:\state"), limit=20)
    assert closed == [123]


@pytest.fixture
def native_store(tmp_path, monkeypatch):
    if os.name != "nt":
        pytest.skip("requires actual Windows local NTFS")
    return _store(tmp_path, monkeypatch)


def _child(store, code):
    return subprocess.run(
        [sys.executable, "-I", "-c",
         "import os,sys\nfrom amplifier_foundation.session.shared_state import *\n"
         "s=SharedSessionStore(sys.argv[1], 'root-1', root=sys.argv[2])\n" + code,
         str(store.workspace), str(store.root)],
        capture_output=True, text=True, timeout=15,
    )


def _private_file(path, data):
    api = win._API()
    handle = api.open(path, exclusive=True, write=True)
    try:
        count = win.DWORD()
        buffer = C.create_string_buffer(data)
        api.ok(api.WriteFile(handle, buffer, len(data), C.byref(count), None))
        assert count.value == len(data)
        api.ok(api.FlushFileBuffers(handle))
    finally:
        api.close(handle)


def _fixture_fence(store, value):
    api = win._API()
    with win._pinned(api, store.transfer_fence_path.parent, root=store._native_root, create=True):
        _private_file(store.transfer_fence_path, value)


@native
def test_native_contention_crash_recovery_and_stable_lock(native_store):
    store = native_store
    assert store.read() is None and store.stamp() is None
    assert store.transfer_fence() is None and not store._native_root.exists()
    first = store.acquire(app="first")
    inode = store._lock_path.stat().st_ino
    try:
        with pytest.raises(shared.SessionBusyError):
            store.acquire(app="same-process")
        child = _child(store, "try:\n s.acquire(app='contender')\nexcept SessionBusyError as e:\n print(e.owner['app'])\n")
        assert child.returncode == 0, child.stderr
        assert child.stdout.strip() == "first"
    finally:
        first.release()
    crash = _child(store, "s.acquire(app='crash')\nos._exit(0)\n")
    assert crash.returncode == 0, crash.stderr
    next_owner = store.acquire(app="after-crash")
    assert store._lock_path.stat().st_ino == inode
    assert next_owner.owner["acquisition_id"] != first.owner["acquisition_id"]
    next_owner.release()
    assert not store._native_root.exists()


@native
def test_native_stale_noncopyable_and_all_authority_mutations_refused(native_store):
    store = native_store
    first = store.acquire(app="first")
    try:
        for operation in (copy.copy, copy.deepcopy, pickle.dumps):
            with pytest.raises(TypeError):
                operation(first)
        first.check()
        assert first.read() is None
        for operation in (
            lambda: first.write([], bundle="test"), first.delete_checkpoint,
            lambda: first.fence_transfer("transfer-1", "destination"),
            lambda: store.acquire_transfer("transfer-1", app="test"),
            lambda: store.confirm_transfer_commit("transfer-1", app="test"),
        ):
            with pytest.raises(shared.SharedStateError, match="not qualified"):
                operation()
        assert not store.checkpoint_path.exists() and not store._native_root.exists()
    finally:
        first.release()
    second = store.acquire(app="second")
    try:
        for operation in (first.check, lambda: first.write([], bundle="test"), first.delete_checkpoint):
            with pytest.raises(RuntimeError, match="no longer active"):
                operation()
    finally:
        second.release()


@native
def test_native_all_lock_ancestors_are_pinned_against_delete_and_rename(native_store, tmp_path):
    store = native_store
    held = store.acquire(app="pins")
    try:
        getter = held._windows.api.kernel.GetHandleInformation
        getter.argtypes, getter.restype = [win.HANDLE, win.PDWORD], win.BOOL
        for handle in [held._windows.handle, *held._windows.pins.handles]:
            flags = win.DWORD()
            held._windows.api.ok(getter(handle, C.byref(flags)))
            assert not flags.value & 1
        for path in [store._directory, store._directory.parent, store.root / "v1", store.root]:
            with pytest.raises(OSError):
                path.rename(path.with_name(path.name + "-moved"))
        with pytest.raises(OSError):
            store._lock_path.unlink()
        replacement = tmp_path / "replacement"
        replacement.write_bytes(b"not a lock")
        with pytest.raises(OSError):
            os.replace(replacement, store._lock_path)
        held.check()
    finally:
        held.release()


@native
def test_native_directory_pins_enforce_sharing_without_incidental_file_handles(tmp_path):
    """An empty tree isolates the directory open, not stable-lock side effects."""
    parent = tmp_path / "pin-parent"
    leaf = parent / "leaf"
    api = win._API()
    with win._pinned(api, leaf, root=parent, create=True):
        for directory in (leaf, parent):
            with pytest.raises(OSError) as caught:
                directory.rename(directory.with_name(directory.name + "-moved"))
            assert caught.value.winerror == 32, (
                f"rename must fail with sharing violation for {directory}: {caught.value}"
            )
            assert directory.is_dir()
        with pytest.raises(OSError) as caught:
            leaf.rmdir()
        assert caught.value.winerror == 32, caught.value
        # No file is open (or even present) to provide accidental rename protection.
        assert list(leaf.iterdir()) == []
    moved = leaf.with_name("leaf-moved")
    leaf.rename(moved)
    moved_parent = parent.with_name("pin-parent-moved")
    parent.rename(moved_parent)
    (moved_parent / moved.name).rmdir()
    moved_parent.rmdir()  # Positive release/readback: no leaked directory handle.


@native
def test_native_case_alias_is_rejected_instead_of_merging_exact_ids(native_store):
    store = native_store
    with pytest.raises(ValueError, match="lowercase"):
        shared.SharedSessionStore(store.workspace, "Root-1", root=store.root)
    store.acquire(app="seed").release()
    store._directory.rename(store._directory.with_name("ROOT-1"))
    with pytest.raises(shared.SharedStateError, match="exact-name collision"):
        store.acquire(app="must-not-merge")


@native
def test_native_existing_lock_hardlink_is_rejected(native_store, tmp_path):
    store = native_store
    store.acquire(app="seed").release()
    os.link(store._lock_path, tmp_path / "lock-alias")
    with pytest.raises(shared.SharedStateError, match="hardlink"):
        store.acquire(app="refused")


@native
@pytest.mark.parametrize("kind", ["symlink", "junction"])
def test_native_intermediate_reparse_is_rejected(native_store, tmp_path, kind):
    actual = tmp_path / "actual"
    actual.mkdir()
    alias = tmp_path / "alias"
    if kind == "junction":
        result = subprocess.run(["cmd.exe", "/d", "/c", "mklink", "/J", str(alias), str(actual)],
                                capture_output=True, text=True, timeout=10)
        assert result.returncode == 0, result.stderr
    else:
        try:
            alias.symlink_to(actual, target_is_directory=True)
        except OSError as exc:
            if exc.winerror == 1314:
                pytest.skip("Windows symlink privilege/Developer Mode not enabled")
            raise
    store = shared.SharedSessionStore(native_store.workspace, "root-1", root=alias / "state")
    with pytest.raises(shared.SharedStateError, match="reparse"):
        store.acquire(app="must-not-follow")
    assert not (actual / "state").exists()


@native
@pytest.mark.parametrize("data", [
    b"{", b"{}", json.dumps({"version": 1, "session_id": "root-1", "transfer_id": "transfer-1",
                            "destination_host": "target", "role": "source", "phase": "staged"}).encode(),
])
def test_native_existing_fence_blocks_before_owner_publication(native_store, data):
    store = native_store
    store.acquire(app="seed").release()
    owner_before = store._owner_path.read_bytes()
    _fixture_fence(store, data)
    expected = shared.SessionTransferFencedError if b"staged" in data else shared.SharedStateError
    with pytest.raises(expected):
        store.acquire(app="must-not-execute")
    assert store._owner_path.read_bytes() == owner_before
    assert store.transfer_fence_path.read_bytes() == data


@native
def test_native_fence_hardlink_and_live_check_fail_closed(native_store, tmp_path):
    store = native_store
    held = store.acquire(app="live")
    try:
        record = {"version": 1, "session_id": "root-1", "transfer_id": "transfer-1",
                  "destination_host": "target", "role": "source", "phase": "staged"}
        _fixture_fence(store, json.dumps(record).encode())
        with pytest.raises(shared.SessionTransferFencedError):
            held.check()
    finally:
        held.release()
    before = store._owner_path.read_bytes()
    os.link(store.transfer_fence_path, tmp_path / "fence-alias")
    with pytest.raises(shared.SharedStateError, match="hardlink"):
        store.acquire(app="must-not-read-alias")
    assert store._owner_path.read_bytes() == before


@native
def test_native_private_creation_acl_and_foreign_read_grant_rejected(native_store):
    store = native_store
    held = store.acquire(app="private")
    api = held._windows.api
    try:
        for handle in held._windows.pins.handles[-4:]:
            api.validate_acl(handle, private=True)
        api.validate_acl(held._windows.handle, private=True)
    finally:
        held.release()
    # Add Everyone read to just the temporary lock. Even foreign shared reads
    # are unacceptable for private files; there is no chmod fallback.
    descriptor = win.PVOID()
    api.ok(api.ConvertStringSecurityDescriptorToSecurityDescriptorW(
        f"O:{api.sid}D:P(A;;FA;;;{api.sid})(A;;GR;;;WD)", 1, C.byref(descriptor), None,
    ))
    setter = api.security.SetFileSecurityW
    setter.argtypes, setter.restype = [C.c_wchar_p, win.DWORD, win.PVOID], win.BOOL
    try:
        api.ok(setter(str(store._lock_path), 0x80000005, descriptor))
    finally:
        api.LocalFree(descriptor)
    with pytest.raises(shared.SharedStateError, match="foreign access"):
        store.acquire(app="must-not-read-shared")


@native
def test_native_safe_checkpoint_read_and_hardlink_refusal(native_store, tmp_path):
    store = native_store
    store.acquire(app="seed").release()
    record = {"version": 1, "workspace": str(store.workspace), "session_id": "root-1",
              "bundle": "test", "messages": [{"role": "user", "content": "fixture"}], "metadata": {}}
    _private_file(store.checkpoint_path, json.dumps(record).encode())
    assert store.read() == record
    assert isinstance(store.stamp(), shared.FileStamp)
    assert shared.SharedSessionStore.list_ids(store.workspace, root=store.root) == ["root-1"]
    os.link(store.checkpoint_path, tmp_path / "checkpoint-alias")
    for operation in (store.read, store.stamp):
        with pytest.raises(shared.SharedStateError, match="hardlink"):
            operation()


@pytest.mark.parametrize("retained,resolved", [
    (r"C:\WORK\project", r"C:\work\project"),
    (r"C:\work\project", r"C:\work\PROJECT"),
    (r"C:\WORK\missing", r"C:\work\missing"),
])
def test_retained_windows_spelling_cannot_split_string_hashed_lock(monkeypatch, retained, resolved):
    class WindowsPath(PureWindowsPath):
        def resolve(self, *, strict=False):
            return PureWindowsPath(resolved)

        def exists(self):
            return False

    left, right = PureWindowsPath(retained), PureWindowsPath(resolved)
    assert left == right  # Actual pathlib Windows comparison, not a fake __eq__.
    assert shared._workspace_key(left) != shared._workspace_key(right)
    monkeypatch.setattr(shared, "Path", WindowsPath)
    monkeypatch.setattr(shared, "os", _WindowsOS())
    with pytest.raises(ValueError, match="canonical"):
        shared._retained_workspace(retained)


def test_retained_exact_missing_windows_workspace_still_does_not_require_directory(monkeypatch):
    class WindowsPath(PureWindowsPath):
        def resolve(self, *, strict=False):
            assert strict is False
            return self

        def exists(self):
            return False

    monkeypatch.setattr(shared, "Path", WindowsPath)
    monkeypatch.setattr(shared, "os", _WindowsOS())
    value = r"C:\work\missing"
    assert str(shared._retained_workspace(value)) == value


@pytest.mark.parametrize("filename,advisory,expected", [
    ("owner.json", True, 7), ("owner.json", False, 3),
    ("session.lock", False, 3), ("checkpoint.json", False, 3),
    ("directory", False, 1),
])
def test_only_explicit_advisory_reader_shares_delete(monkeypatch, filename, advisory, expected):
    from contextlib import contextmanager

    api = win._API.__new__(win._API)

    @contextmanager
    def security(**kwargs):
        yield win.SECURITY_ATTRIBUTES()

    def create(path, access, share, sa, disposition, flags, template):
        assert share == expected
        assert access == win._READ_CONTROL | win._ATTRIBUTES | (1 if filename == "directory" else win._READ)
        assert disposition == 3
        return 123

    api.private_security, api.CreateFileW = security, create
    api.SetHandleInformation = lambda *a: 1
    api.info = lambda *a, **kw: None
    assert api.open(PureWindowsPath(r"C:\state") / filename,
                    directory=filename == "directory", advisory_read=advisory) == 123


@pytest.mark.parametrize("filename,kwargs", [
    ("session.lock", {}), ("checkpoint.json", {}), ("owner.json", {"write": True}),
    ("owner.json", {"directory": True}), ("owner.json", {"create": True}),
    ("owner.json", {"exclusive": True}),
])
def test_advisory_sharing_cannot_be_used_for_locks_or_mutation(filename, kwargs):
    api = win._API.__new__(win._API)
    with pytest.raises(ValueError, match="advisory"):
        api.open(PureWindowsPath(r"C:\state") / filename, advisory_read=True, **kwargs)


@pytest.mark.parametrize("owner", [
    "S-1-5-18", "S-1-5-32-544",
    "S-1-5-80-956008885-3418522649-1831038044-1853292631-2271478464",
])
def test_os_authority_may_own_nonprivate_ancestors_not_private_state(owner):
    api = _acl_api(owner=owner, protected=False, foreign_mask=0x1F01FF, trustee=owner)
    api.validate_acl(123, private=False)
    with pytest.raises(shared.SharedStateError):
        api.validate_acl(123, private=True)


@pytest.mark.parametrize("trustee", ["S-1-5-18", "S-1-5-32-544", "S-1-3-4"])
def test_private_dacl_may_retain_explicit_os_authority_not_ordinary_users(trustee):
    _acl_api(foreign_mask=0x1F01FF, trustee=trustee).validate_acl(123, private=True)
    with pytest.raises(shared.SharedStateError, match="foreign access"):
        _acl_api(foreign_mask=1, trustee="S-1-5-32-545").validate_acl(123, private=True)


def test_traversal_distinguishes_inherit_only_and_create_subdirectory_from_reparse():
    # Common system-drive ACL shapes: effective Users RX + create subdirectory;
    # inherit-only CREATOR OWNER full control. No effective attribute mutation.
    _acl_api(protected=False, foreign_mask=0x1200AD).validate_acl(123, private=False, traversal=True)
    _acl_api(protected=False, foreign_mask=0x1F01FF, flags=8).validate_acl(
        123, private=False, traversal=True,
    )
    for mask in (2, 0x100, 0x40, 0x10000, 0x40000, 0x80000, 0x40000000):
        with pytest.raises(shared.SharedStateError, match="foreign access"):
            _acl_api(foreign_mask=mask).validate_acl(123, private=False, traversal=True)
    with pytest.raises(shared.SharedStateError):
        _acl_api(foreign_mask=4).validate_acl(123, private=False)


@pytest.mark.parametrize("bad", ["C:\\", r"C:\shared"])
def test_all_ancestors_are_validated_before_any_descendant_creation(monkeypatch, bad):
    from contextlib import contextmanager

    bad = PureWindowsPath(bad)
    seen, created = [], []
    root = PureWindowsPath(r"C:\shared\state")
    existing = {PureWindowsPath("C:\\"), PureWindowsPath(r"C:\shared")}

    class API:
        ok = staticmethod(win._API.ok)

        def GetDriveTypeW(self, path):
            return 3

        def open(self, path, **kw):
            if path not in existing:
                error = FileNotFoundError()
                error.winerror = 2
                raise error
            return path

        def GetVolumeInformationByHandleW(self, handle, name, size, serial, maxlen, flags, fs, fslen):
            fs.value = "NTFS"
            return 1

        def validate_acl(self, handle, **kwargs):
            seen.append((handle, kwargs))
            if handle == bad:
                raise shared.SharedStateError("Windows traversal ancestor grants foreign access")

        @contextmanager
        def private_security(self, **kw):
            yield win.SECURITY_ATTRIBUTES()

        def CreateDirectoryW(self, path, security):
            created.append(path)
            pytest.fail("created descendant before validating untrusted ancestor")

        def close(self, handle):
            pass

    with pytest.raises(shared.SharedStateError, match="foreign access"):
        win._PinnedDirectories(API(), root, owned_from=root, private=True, create=True)
    assert seen and not created


@native
def test_native_retained_exact_identity_contends_and_rejects_case_alias_and_rename(native_store):
    store = native_store
    identity = store.identity()
    restored = shared.SharedSessionStore.from_identity(identity, root=store.root)
    held = store.acquire(app="normal")
    try:
        with pytest.raises(shared.SessionBusyError):
            restored.acquire(app="retained")
        code = (
            "import json,sys\n"
            "from amplifier_foundation.session.shared_state import SharedSessionStore,SessionBusyError\n"
            "s=SharedSessionStore.from_identity(json.loads(sys.argv[1]),root=sys.argv[2])\n"
            "try:\n s.acquire(app='retained-process')\nexcept SessionBusyError:\n sys.exit(23)\n"
        )
        result = subprocess.run([sys.executable, "-I", "-c", code, json.dumps(identity), str(store.root)],
                                capture_output=True, text=True, timeout=15)
        assert result.returncode == 23, result.stderr
        alias = identity | {"workspace": str(store.workspace.with_name("WORKSPACE"))}
        normal_alias = shared.SharedSessionStore(alias["workspace"], store.session_id, root=store.root)
        assert str(normal_alias.checkpoint_path) == str(store.checkpoint_path)
        with pytest.raises(shared.SessionBusyError):
            normal_alias.acquire(app="normal-case-alias")
        with pytest.raises(ValueError, match="canonical"):
            shared.SharedSessionStore.from_identity(alias, root=store.root)
        # A case-only rename does not authorize silently rewriting an identity.
        store.workspace.rename(store.workspace.with_name("WORKSPACE"))
        with pytest.raises(ValueError, match="canonical"):
            shared.SharedSessionStore.from_identity(identity, root=store.root)
    finally:
        held.release()


@native
def test_native_open_advisory_reader_cannot_block_release_or_successor_publication(native_store):
    store = native_store
    first = store.acquire(app="first")
    original = first.owner
    api = first._windows.api
    reader = api.open(store._owner_path, advisory_read=True)
    try:
        first.release()
        successor = store.acquire(app="successor")
        try:
            assert json.loads(store._owner_path.read_bytes())["app"] == "successor"
            api.validate_acl(reader, private=True)  # Detached old snapshot remains safe.
            buffer, count = C.create_string_buffer(65536), win.DWORD()
            api.ok(api.ReadFile(reader, buffer, len(buffer), C.byref(count), None))
            assert json.loads(buffer.raw[:count.value]) == original
        finally:
            successor.release()
    finally:
        api.close(reader)
        first.release()


@native
@pytest.mark.parametrize("name", ["owner.json", "transcript.jsonl", "metadata.json"])
def test_native_pinned_publication_absolute_control_and_leaf_replace_old_reader(tmp_path, name):
    """Compare absolute vs native leaf form with identical strict pins and payload.

    These controls must actually run on NTFS; portable stubs cannot establish
    the suspected absolute-target-open sharing conflict.
    """
    directory = tmp_path / "publication-\u2603-\U0001f680"
    target = directory / name
    api = history_win._API()
    old, new, final = (json.dumps({"value": value}).encode() + b"\n"
                       for value in ("old", "new", "after-close"))

    def absolute_publish(handle):
        encoded = str(target).encode("utf-16-le")
        buffer = C.create_string_buffer(C.sizeof(win.FILE_RENAME_INFO) + len(encoded))
        info = win.FILE_RENAME_INFO.from_buffer(buffer)
        info.Flags, info.FileNameLength = 3, len(encoded)
        C.memmove(C.addressof(buffer) + win.FILE_RENAME_INFO.FileName.offset, encoded, len(encoded))
        result = api.SetFileInformationByHandle(handle, 22, buffer, len(buffer))
        return bool(result), C.get_last_error() if not result else 0

    def new_source(suffix, payload):
        path = directory / (f".owner-{suffix * 32}.tmp" if name == "owner.json"
                            else f".history-{suffix * 32}.tmp")
        handle = (api.open(path, exclusive=True, write=True, owner_temporary=True)
                  if name == "owner.json" else api.open_history(path, write=True, temporary=True))
        try:
            api.validate_acl(handle, private=True)
            api.write_chunks(handle, [payload])  # Complete payload + FlushFileBuffers.
        except BaseException:
            api.close(handle)
            raise
        return path, handle

    def read(handle):
        buffer, count = C.create_string_buffer(65536), win.DWORD()
        api.ok(api.ReadFile(handle, buffer, len(buffer), C.byref(count), None))
        return buffer.raw[:count.value]

    def assert_pinned():
        with pytest.raises(OSError) as caught:
            directory.rename(directory.with_name("must-not-move"))
        assert caught.value.winerror == 32, caught.value
        assert directory.is_dir()

    after_close = None
    try:
        with win._pinned(api, directory, root=directory, create=True):
            _private_file(target, old)
            reader = (api.open(target, advisory_read=True) if name == "owner.json"
                      else api.open_history(target))
            try:
                source, handle = new_source("a", new)
                try:
                    assert_pinned()
                    assert absolute_publish(handle) == (False, 32)
                    assert target.read_bytes() == old and source.exists()
                    # Same source handle, bytes, target, flags and pins; only the
                    # entry point/name form changes. No pin/sharing fallback.
                    (api.replace_owner if name == "owner.json" else api.replace_history)(handle, target)
                finally:
                    api.close(handle)
                assert not source.exists()
                assert read(reader) == old  # Detached snapshot stays byte-identical.
                assert target.read_bytes() == new
                assert_pinned()  # Publication did not release or weaken the pins.
                after_source, after_close = new_source("b", final)
            finally:
                api.close(reader)
        # Same absolute wrapper succeeds once every directory pin is closed.
        assert absolute_publish(after_close) == (True, 0)
        api.close(after_close)
        after_close = None
        assert not after_source.exists() and target.read_bytes() == final
        moved = directory.with_name(directory.name + "-moved")
        directory.rename(moved)
        assert (moved / name).read_bytes() == final
        moved.rename(directory)
    finally:
        if after_close is not None:
            api.close(after_close)


@pytest.mark.parametrize("name", ["owner.json", "checkpoint.json", "transfer-fence.json"])
def test_owner_json_read_pipeline_is_the_only_replaceable_reader(monkeypatch, name):
    from contextlib import contextmanager

    closed = []

    class API:
        ok = staticmethod(win._API.ok)

        def open(self, path, **kwargs):
            assert kwargs == ({"advisory_read": True} if name == "owner.json" else {})
            return 123

        def validate_acl(self, *a, **kw):
            assert kw == {"private": True}

        def ReadFile(self, handle, buffer, length, count, overlapped):
            C.cast(count, win.PDWORD)[0] = 0
            return 1

        def close(self, handle):
            closed.append(handle)

    @contextmanager
    def pinned(*a, **kw):
        class Pins:
            missing = False
        yield Pins()

    monkeypatch.setattr(win, "_API", API)
    monkeypatch.setattr(win, "_pinned", pinned)
    assert win.read_bytes(PureWindowsPath(r"C:\state") / name,
                          root=PureWindowsPath(r"C:\state"), limit=100) == b""
    assert closed == [123]


@native
def test_native_missing_workspace_retains_exact_identity_without_recreation(native_store):
    store = native_store
    identity = store.identity()
    store.workspace.rmdir()
    restored = shared.SharedSessionStore.from_identity(identity, root=store.root)
    assert restored.identity() == identity
    first = store.acquire(app="original-address")
    try:
        with pytest.raises(shared.SessionBusyError):
            restored.acquire(app="retained-address")
    finally:
        first.release()
    assert not store.workspace.exists()


@native
@pytest.mark.parametrize("mask", [2, 4, 0x100, 0x40])
def test_native_ancestor_reparse_or_delete_grant_refuses_before_state_creation(native_store, tmp_path, mask):
    # Model an untrusted writable ancestor above owned_from, not an unsafe
    # private state object. No reparse operation is needed: reject its authority
    # before CreateDirectory can be redirected by another account.
    ancestor = tmp_path / "ancestor"
    ancestor.mkdir()
    api = win._API()
    descriptor = win.PVOID()
    api.ok(api.ConvertStringSecurityDescriptorToSecurityDescriptorW(
        f"O:{api.sid}D:P(A;;FA;;;{api.sid})(A;;0x{mask:x};;;WD)",
        1, C.byref(descriptor), None,
    ))
    setter = api.security.SetFileSecurityW
    setter.argtypes, setter.restype = [C.c_wchar_p, win.DWORD, win.PVOID], win.BOOL
    try:
        api.ok(setter(str(ancestor), 0x80000005, descriptor))
    finally:
        api.LocalFree(descriptor)
    store = shared.SharedSessionStore(native_store.workspace, "root-1", root=ancestor / "state")
    if mask == 4:  # Creating siblings alone cannot reparse or delete a pinned parent.
        store.acquire(app="bounded-create-subdirectory").release()
    else:
        with pytest.raises(shared.SharedStateError, match="foreign access"):
            store.acquire(app="must-refuse-before-creation")
        assert not store.root.exists()


@pytest.mark.parametrize("name", [
    "session.lock", "owner.json", "checkpoint.json", "transfer-fence.json",
    ".root-1.metadata.lock",
])
def test_history_adapter_cannot_open_or_mutate_ownership_or_stable_locks(name):
    api = history_win._API.__new__(history_win._API)
    path = PureWindowsPath(r"C:\private") / name
    for kwargs in ({}, {"write": True}, {"remove": True}, {"write": True, "temporary": True}):
        with pytest.raises(ValueError):
            api.open_history(path, **kwargs)
    with pytest.raises(ValueError):
        api.replace_history(123, path)
    with pytest.raises(ValueError):
        history_win._replace(path, [b"forbidden"])


@pytest.mark.parametrize("mode", ["read", "append", "temporary", "remove"])
@pytest.mark.parametrize("unsafe", [False, True])
def test_history_open_is_private_noninheritable_and_validated_before_payload(monkeypatch, mode, unsafe):
    from contextlib import contextmanager

    api = history_win._API.__new__(history_win._API)
    events = []
    handle = (1 << 40) if C.sizeof(win.HANDLE) == 8 else 123

    @contextmanager
    def security():
        events.append("private-security")
        yield win.SECURITY_ATTRIBUTES(C.sizeof(win.SECURITY_ATTRIBUTES), 456, False)

    def create(path, access, share, sa, disposition, flags, template):
        assert C.cast(sa, C.POINTER(win.SECURITY_ATTRIBUTES)).contents.lpSecurityDescriptor == 456
        assert share == (3 if mode == "temporary" else 7)
        assert disposition == (1 if mode == "temporary" else 3)
        assert flags & win._OPEN_REPARSE
        assert bool(access & win._WRITE) == (mode in {"append", "temporary"})
        assert bool(access & 0x10000) == (mode in {"temporary", "remove"})
        events.append("open")
        return handle

    def inheritance(actual, mask, flags):
        assert (actual, mask, flags) == (handle, 1, 0)
        events.append("noninherit")
        return True

    def acl(actual, **kwargs):
        assert actual == handle and kwargs == {"private": True}
        events.append("acl")
        if unsafe:
            raise shared.SharedStateError("unsafe ACL")

    api.private_security, api.CreateFileW = security, create
    api.SetHandleInformation, api.validate_acl = inheritance, acl
    api.info = lambda *a, **kw: events.append("type-links-alias")
    api.close = lambda actual: events.append("close")
    path = PureWindowsPath(r"C:\private") / {
        "read": "metadata.json", "append": "transcript.jsonl",
        "temporary": ".history-" + "a" * 32 + ".tmp",
        "remove": "transcript.jsonl.append-pending",
    }[mode]
    kwargs = {
        "read": {}, "append": {"write": True},
        "temporary": {"write": True, "temporary": True}, "remove": {"remove": True},
    }[mode]
    if unsafe:
        with pytest.raises(shared.SharedStateError):
            api.open_history(path, **kwargs)
    else:
        assert api.open_history(path, **kwargs) == handle
    assert events == ["private-security", "open", "noninherit", "type-links-alias", "acl"] + (["close"] if unsafe else [])


@pytest.mark.parametrize("failure", [None, "zero", "write", "flush", "replace"])
def test_history_publication_flushes_complete_partial_writes_before_replace(monkeypatch, failure):
    from contextlib import contextmanager

    events, output = [], bytearray()
    error = OSError("synthetic history failure")

    class API:
        write_chunks = history_win._API.write_chunks

        def open_history(self, path, **kwargs):
            events.append(("open", kwargs))
            if not kwargs:
                raise FileNotFoundError(path)
            return 123

        def WriteFile(self, handle, buffer, size, count, overlapped):
            if failure == "write":
                raise error
            length = 0 if failure == "zero" else min(size, 17)
            C.cast(count, win.PDWORD)[0] = length
            output.extend(C.string_at(buffer, length))
            return True

        def FlushFileBuffers(self, handle):
            events.append("flush")
            if failure == "flush":
                raise error
            return True

        def replace_history(self, handle, path):
            events.append("replace")
            if failure == "replace":
                raise error

        def close(self, handle):
            events.append("close")

        def DeleteFileW(self, path):
            events.append("cleanup")
            return True

        ok = staticmethod(win._API.ok)

    @contextmanager
    def pins(*args, **kwargs):
        assert kwargs["create"] is True
        events.append("pin-before-create")
        yield
        events.append("unpin")

    monkeypatch.setattr(history_win, "_API", API)
    monkeypatch.setattr(win, "_pinned", pins)
    payload = ("private \u2603\U0001f680" * 300).encode()
    if failure:
        with pytest.raises(OSError):
            history_win._replace(PureWindowsPath(r"C:\private\metadata.json"), [payload])
        assert "cleanup" in events
        if failure != "replace":
            assert "replace" not in events
    else:
        history_win._replace(PureWindowsPath(r"C:\private\metadata.json"), [payload])
        assert bytes(output) == payload
        assert events.index("flush") < events.index("replace") < events.index("close") < events.index("unpin")
    assert events[0] == "pin-before-create"


@pytest.mark.parametrize("error", [0, 5, 32, 50, 87])
def test_history_rename_uses_fullwidth_posix_replacement_without_fallback(monkeypatch, error):
    api = history_win._API.__new__(history_win._API)
    handle = (1 << 40) if C.sizeof(win.HANDLE) == 8 else 123
    path = PureWindowsPath("C:\\private-\u2603-\U0001f680\\metadata.json")
    calls = []

    def source_name(actual, output, size, flags):
        assert actual == handle and flags == 0
        output.value = "\\\\?\\" + str(path.parent / (".history-" + "b" * 32 + ".tmp"))
        return len(output.value)

    def replace(actual, result, buffer, size, info_class):
        info = C.cast(buffer, C.POINTER(win.FILE_RENAME_INFO)).contents
        assert (actual, info_class, info.Flags, info.RootDirectory) == (handle, 65, 3, None)
        raw = C.string_at(C.addressof(buffer) + win.FILE_RENAME_INFO.FileName.offset, info.FileNameLength)
        assert raw == path.name.encode("utf-16-le")
        calls.append("replace")
        return 0xC0000022 if error else 0  # Also exercise unsigned-provider conversion.

    api.GetFinalPathNameByHandleW = source_name
    api.NtSetInformationFile = replace
    api.RtlNtStatusToDosError = lambda status: error if status == -1073741790 else pytest.fail("wrong status")
    api.SetFileInformationByHandle = lambda *a: pytest.fail("absolute Win32 fallback")
    failure = OSError("synthetic rename refusal")
    failure.winerror = error
    monkeypatch.setattr(C, "get_last_error", lambda: 999, raising=False)
    monkeypatch.setattr(C, "WinError", lambda code: failure if code == error else pytest.fail("wrong mapped WinError"),
                        raising=False)
    if error:
        with pytest.raises(OSError) as caught:
            api.replace_history(handle, path)
        assert caught.value is failure
    else:
        api.replace_history(handle, path)
    assert calls == ["replace"]


@pytest.mark.parametrize("kind", ["owner", "history"])
@pytest.mark.parametrize("failure", ["other-parent", "missing-source", "truncated-source", "pending"])
def test_native_sibling_publication_refuses_wrong_parent_or_uncertain_completion(monkeypatch, kind, failure):
    api = history_win._API.__new__(history_win._API)
    path = PureWindowsPath(r"C:\private") / ("owner.json" if kind == "owner" else "metadata.json")
    calls = []

    def source_name(handle, output, size, flags):
        output.value = str(path.parent / "source.tmp")
        if failure == "other-parent":
            output.value = r"C:\different\source.tmp"
        return 0 if failure == "missing-source" else size if failure == "truncated-source" else len(output.value)

    def rename(*args):
        calls.append("native")
        return 0x103

    api.GetFinalPathNameByHandleW = source_name
    api.NtSetInformationFile = rename
    api.RtlNtStatusToDosError = lambda *a: pytest.fail("pending is not a completed error")
    monkeypatch.setattr(C, "get_last_error", lambda: 5, raising=False)
    error = OSError("source query failed")
    error.winerror = 5
    monkeypatch.setattr(C, "WinError", lambda code: error, raising=False)
    publish = api.replace_owner if kind == "owner" else api.replace_history
    with pytest.raises(shared.SharedStateError if failure == "other-parent" else OSError) as caught:
        publish(123, path)
    assert calls == (["native"] if failure == "pending" else [])
    if failure == "pending":
        assert "pending" in str(caught.value)


def test_history_stream_read_seek_peek_keep_pins_and_close_handle(monkeypatch):
    import io
    from contextlib import contextmanager

    data, events = io.BytesIO(b"first\nsecond\n"), []

    class API:
        ok = staticmethod(win._API.ok)

        def open_history(self, path):
            events.append("validated-open")
            return 123

        def ReadFile(self, handle, buffer, size, count, overlapped):
            raw = data.read(size)
            C.memmove(buffer, raw, len(raw))
            C.cast(count, win.PDWORD)[0] = len(raw)
            return True

        def seek(self, handle, offset, whence):
            return data.seek(offset, whence)

        def close(self, handle):
            events.append("close")

    @contextmanager
    def pins(*args, **kwargs):
        assert not kwargs.get("create")
        events.append("pin")
        yield type("Pins", (), {"missing": False})()
        events.append("unpin")

    monkeypatch.setattr(history_win, "_API", API)
    monkeypatch.setattr(win, "_pinned", pins)
    with history_win.open_binary(PureWindowsPath(r"C:\private\transcript.jsonl")) as stream:
        assert stream.readline() == b"first\n"
        assert stream.peek(1)
        stream.seek(6)
        assert stream.read() == b"second\n"
        assert events == ["pin", "validated-open"]
    assert events == ["pin", "validated-open", "close", "unpin"]


@pytest.mark.parametrize("error", [5, 33])
def test_history_metadata_lock_native_error_or_timeout_closes_without_unlink(monkeypatch, error):
    from contextlib import contextmanager
    from filelock import Timeout

    events = []

    class API:
        def open(self, path, **kwargs):
            assert path.name == ".session.metadata.lock"
            assert kwargs == {"create": True, "write": True}
            events.append("open-stable")
            return 123

        def validate_acl(self, handle, **kwargs):
            assert kwargs == {"private": True}
            events.append("validate")

        def LockFileEx(self, handle, flags, reserved, low, high, overlapped):
            assert (handle, flags, reserved, low, high) == (123, 3, 0, 1, 0)
            return False

        def close(self, handle):
            events.append("close")

    @contextmanager
    def pins(*args, **kwargs):
        assert kwargs["private"] is False
        try:
            yield
        finally:
            events.append("unpin")

    clock = iter([0, 11])
    monkeypatch.setattr(history_win, "_API", API)
    monkeypatch.setattr(win, "_pinned", pins)
    monkeypatch.setattr(history_win.time, "monotonic", lambda: next(clock))
    monkeypatch.setattr(C, "get_last_error", lambda: error, raising=False)
    monkeypatch.setattr(C, "WinError", lambda code: PermissionError("native access denied"), raising=False)
    with pytest.raises(Timeout if error == 33 else PermissionError):
        with history_win.metadata_lock(PureWindowsPath(r"C:\private\session")):
            pytest.fail("failed acquisition yielded")
    assert events == ["open-stable", "validate", "close", "unpin"]


def test_history_new_bindings_use_fixed_width_and_actual_change_time(monkeypatch):
    class Function:
        pass

    class DLL:
        def __getattr__(self, name):
            function = Function()
            setattr(self, name, function)
            return function

    monkeypatch.setattr(win, "os", _WindowsOS())
    monkeypatch.setattr(C, "WinDLL", lambda *a, **kw: DLL(), raising=False)
    monkeypatch.setattr(win._API, "_user_sid", lambda self: "synthetic-sid")
    api = history_win._API()
    assert api.SetFilePointerEx.argtypes == [win.HANDLE, C.c_int64, C.POINTER(C.c_int64), win.DWORD]
    assert api.GetFileInformationByHandleEx.argtypes == [win.HANDLE, C.c_int, win.PVOID, win.DWORD]
    assert history_win._BASIC_INFO.change.offset == 24
    assert C.sizeof(history_win._BASIC_INFO) == 40


@pytest.mark.parametrize("failure", ["none", "exists-80", "exists-183", "denied", "ancestor", "leaf", "body"])
def test_history_fork_reservation_is_private_create_only_and_pinned(monkeypatch, failure):
    from contextlib import contextmanager

    path = PureWindowsPath(r"C:\parent\new-fork")
    events, last_error = [], [0]
    error_code = {"exists-80": 80, "exists-183": 183, "denied": 5}.get(failure, 0)

    class API:
        @contextmanager
        def private_security(self, **kwargs):
            assert kwargs == {"directory": True}
            events.append("private-security")
            yield win.SECURITY_ATTRIBUTES(C.sizeof(win.SECURITY_ATTRIBUTES), 456, False)
            last_error[0] = 0  # Cleanup must not erase the CreateDirectory error.

        def CreateDirectoryW(self, actual, pointer):
            assert actual == str(path)
            security = C.cast(pointer, C.POINTER(win.SECURITY_ATTRIBUTES)).contents
            assert security.lpSecurityDescriptor == 456 and not security.bInheritHandle
            assert events == ["pin-parent", "private-security"]
            events.append("create-new")
            last_error[0] = error_code
            return not error_code

    @contextmanager
    def pinned(api, directory, **kwargs):
        if directory == path.parent:
            assert kwargs == {"root": path.parent, "private": False, "create": True}
            if failure == "ancestor":
                raise shared.SharedStateError("unsafe ancestor")
            label = "parent"
        else:
            assert directory == path and kwargs == {"root": path}
            if failure == "leaf":
                raise shared.SharedStateError("leaf validation failed")
            label = "leaf"
        events.append("pin-" + label)
        try:
            yield type("Pins", (), {"missing": False})()
        finally:
            events.append("unpin-" + label)

    def native_error(code):
        error = PermissionError("native access denied")
        error.winerror = code
        return error

    monkeypatch.setattr(history_win, "_API", API)
    monkeypatch.setattr(win, "_pinned", pinned)
    monkeypatch.setattr(C, "get_last_error", lambda: last_error[0], raising=False)
    monkeypatch.setattr(C, "WinError", native_error, raising=False)

    def run():
        with history_win.reserve_new_session(path):
            assert events[-1] == "pin-leaf"
            events.append("publish")
            if failure == "body":
                raise OSError("publication failure")

    if failure == "none":
        run()
        assert events[-3:] == ["publish", "unpin-leaf", "unpin-parent"]
    else:
        expected = (FileExistsError if failure.startswith("exists") else
                    PermissionError if failure == "denied" else
                    OSError if failure == "body" else shared.SharedStateError)
        with pytest.raises(expected) as caught:
            run()
        notes = getattr(caught.value, "__notes__", [])
        assert bool(notes) == (failure in {"leaf", "body"})
        if notes:
            assert "retained" in notes[0] and str(path) in notes[0]
        if error_code:
            assert "pin-leaf" not in events
            assert not notes  # Never claim we created somebody else's directory.
        if failure == "ancestor":
            assert not events  # No descendant creation before unsafe-ancestor refusal.
        else:
            assert events[-1] == "unpin-parent"


@pytest.fixture
def native_history(tmp_path):
    if os.name != "nt":
        pytest.skip("requires actual Windows local NTFS")
    from amplifier_foundation.session.history import SessionHistoryStore

    # Missing leaf: production code, not the fixture, creates its protected DACL.
    return SessionHistoryStore(tmp_path / "private-history-\u2603-\U0001f680")


@native
def test_native_history_roundtrip_replace_old_reader_and_private_files(native_history):
    store = native_history
    rows = [{"role": "user", "content": "private \u2603\U0001f680",
             "metadata": {"provider:continuation": "synthetic"}}]
    store.save(rows, {"unknown": {"preserved": True}}, incremental=True, merge_metadata=True)
    with history_win.open_binary(store.transcript_path) as reader:
        store.save_messages(rows + [{"role": "assistant", "content": "answer"}])
        assert [json.loads(line) for line in reader] == rows
    assert store.load(include_events=False).metadata == {"unknown": {"preserved": True}}
    assert list(store.indexed_messages()) == store.load_messages()
    api = history_win._API()
    for path in store.session_dir.iterdir():
        with history_win._file(path) as (api, handle):
            api.validate_acl(handle, private=True)
    assert not list(store.session_dir.glob(".history-*"))


@native
def test_native_history_missing_reads_do_not_create_roots_or_lock(native_history):
    store = native_history
    assert store.load(include_events=False).messages == []
    assert store.load_metadata() == {} and store.indexed_messages() == []
    assert not store.exists()
    assert not store.session_dir.exists()
    assert not (store.session_dir.parent / f".{store.session_dir.name}.metadata.lock").exists()


@native
@pytest.mark.parametrize("complete", [False, True])
def test_native_history_interrupted_append_preserves_complete_backup(native_history, monkeypatch, complete):
    from amplifier_foundation.session import jsonl

    store = native_history
    rows = [{"role": "user", "content": str(i)} for i in range(4)]
    store.save(rows[:2], {}, incremental=True)
    original = history_win.append

    def interrupt(path, chunks):
        if path.name == "transcript.jsonl":
            chunk = next(chunks)
            original(path, iter([chunk if complete else chunk[:8]]))
            raise OSError("synthetic interrupted append")
        return original(path, chunks)

    monkeypatch.setattr(history_win, "append", interrupt)
    with pytest.raises(OSError):
        store.save(rows, {}, incremental=True)
    assert jsonl.intent_path(store.transcript_path).exists()
    assert store.load_messages() == rows[:2]
    assert list(store.indexed_messages()) == rows[:2]
    assert "interrupted_append" in [d.code for d in store.diagnostics]
    monkeypatch.setattr(history_win, "append", original)
    store.save(rows, {}, incremental=True)
    assert store.load_messages() == rows
    assert not jsonl.intent_path(store.transcript_path).exists()


@native
def test_native_history_corrupt_primary_does_not_replace_good_backup(native_history):
    store = native_history
    rows = [{"role": "user", "content": "good"}]
    store.save(rows, {"version": 1})
    store.save(rows * 2, {"version": 2})
    backup = store.transcript_path.with_suffix(".jsonl.backup").read_bytes()
    store.transcript_path.write_bytes(b"corrupt")
    assert store.load_messages() == rows
    store.save_messages(rows * 3)
    assert store.transcript_path.with_suffix(".jsonl.backup").read_bytes() == backup
    assert store.load_messages() == rows * 3


@native
@pytest.mark.parametrize("filename", ["transcript.jsonl", "metadata.json", "transcript.jsonl.backup",
                                      "transcript.jsonl.append-pending"])
def test_native_history_rejects_hardlinks_without_repair(native_history, tmp_path, filename):
    store = native_history
    store.save([{"role": "user", "content": "one"}], {})
    store.save([{"role": "user", "content": "two"}], {})
    target = store.session_dir / filename
    if not target.exists():
        history_win.write_pending(target)
    original = target.read_bytes()
    os.link(target, tmp_path / "forbidden-alias")
    with pytest.raises(shared.SharedStateError, match="hardlink"):
        history_win.stamp(target)
    with pytest.raises(shared.SharedStateError, match="hardlink"):
        history_win._replace(target, [b"must not repair"])
    assert target.read_bytes() == original


@native
def test_native_history_reparse_ancestor_refused_before_descendant_creation(native_history, tmp_path):
    alias, actual = tmp_path / "alias", tmp_path / "actual"
    actual.mkdir()
    result = subprocess.run(["cmd.exe", "/d", "/c", "mklink", "/J", str(alias), str(actual)],
                            capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stderr
    from amplifier_foundation.session.history import SessionHistoryStore

    store = SessionHistoryStore(alias / "must-not-create")
    with pytest.raises(shared.SharedStateError, match="reparse"):
        store.save([], {})
    assert not (actual / "must-not-create").exists()


@native
@pytest.mark.parametrize("target_name", ["metadata.json", "transcript.jsonl", "directory"])
def test_native_history_foreign_read_acl_rejected_without_repair(native_history, target_name):
    store = native_history
    store.save([{"role": "user", "content": "synthetic"}], {})
    target = store.session_dir if target_name == "directory" else store.session_dir / target_name
    original = store.metadata_path.read_bytes()
    api = history_win._API()
    descriptor = win.PVOID()
    api.ok(api.ConvertStringSecurityDescriptorToSecurityDescriptorW(
        f"O:{api.sid}D:P(A;;FA;;;{api.sid})(A;;GR;;;WD)",
        1, C.byref(descriptor), None,
    ))
    setter = api.security.SetFileSecurityW
    setter.argtypes, setter.restype = [C.c_wchar_p, win.DWORD, win.PVOID], win.BOOL
    try:
        api.ok(setter(str(target), 0x80000005, descriptor))
    finally:
        api.LocalFree(descriptor)
    operation = store.load_metadata if target_name != "transcript.jsonl" else store.load_messages
    with pytest.raises(shared.SharedStateError, match="foreign access"):
        operation()
    with pytest.raises(shared.SharedStateError, match="foreign access"):
        store.save([{"role": "user", "content": "must not publish"}], {})
    assert store.metadata_path.read_bytes() == original


def test_history_metadata_replacement_rotates_its_own_backup(monkeypatch):
    """The warm-transcript copy guard must not reject this separate operation."""
    calls = []
    path = PureWindowsPath(r"C:\private\metadata.json")
    monkeypatch.setattr(history_win, "stamp", lambda actual: object())
    monkeypatch.setattr(history_win, "copy_backup", lambda source, target: calls.append(("backup", source, target)))
    monkeypatch.setattr(history_win, "_replace", lambda target, chunks: calls.append(("replace", target, list(chunks))))
    monkeypatch.setattr(history_win, "remove_pending", lambda *a: pytest.fail("metadata must not remove transcript intent"))
    history_win.write_prepared(path, '{"turn":2}', preserve_backup=False)
    assert calls == [
        ("backup", path, path.with_suffix(".json.backup")),
        ("replace", path, [b'{"turn":2}']),
    ]


@native
def test_native_history_warm_append_never_copies_full_prefix(native_history, monkeypatch):
    store = native_history
    rows = [{"role": "user", "content": "x" * 2000 + str(i)} for i in range(100)]
    store.save(rows, {}, incremental=True)
    store.save(rows + rows[:1], {}, incremental=True)
    inode = store.transcript_path.stat().st_ino
    reads, copied, tail_bytes = [], [], []
    expected_start = 0
    original = history_win.open_binary
    copy_backup = history_win.copy_backup
    from contextlib import contextmanager

    class TailReader:
        def __init__(self, stream):
            self.stream = stream

        def seek(self, offset):
            assert offset == expected_start, f"read began at {offset}, expected saved tail at {expected_start}"
            return self.stream.seek(offset)

        def read(self, size):
            raw = self.stream.read(size)
            tail_bytes.append(len(raw))
            return raw

    @contextmanager
    def observed(path):
        if path.name.startswith("transcript"):
            reads.append(path.name)
        with original(path) as stream:
            yield TailReader(stream) if path == store.transcript_path else stream

    def bounded_copy(source, target):
        # Metadata replacement has its own small, required backup. The original
        # blanket trap mistook that for a transcript-prefix copy in Windows CI.
        assert source.name == "metadata.json", f"full-prefix copy on warm append: {source.name}"
        copied.append(source.name)
        return copy_backup(source, target)

    monkeypatch.setattr(history_win, "open_binary", observed)
    monkeypatch.setattr(history_win, "copy_backup", bounded_copy)
    for count in (2, 3):
        reads.clear()
        copied.clear()
        tail_bytes.clear()
        before = store.transcript_path.read_bytes()
        backup = store.transcript_path.with_suffix(".jsonl.backup")
        expected_start = backup.stat().st_size
        store.save(rows + rows[:count], {}, incremental=True)
        assert reads == ["transcript.jsonl"], reads  # No primary/backup reparse.
        assert sum(tail_bytes) == len(before) - expected_start
        assert copied == ["metadata.json"]
        assert store.transcript_path.stat().st_ino == inode
        assert backup.read_bytes() == before
    # Stop read instrumentation before checking complete content.
    monkeypatch.setattr(history_win, "open_binary", original)
    assert store.load_messages() == rows + rows[:3]


@native
def test_native_history_metadata_stable_lock_noninheritance_and_process_contention(native_history):
    from amplifier_foundation.session.metadata import SessionMetadataStore, metadata_lock

    store = native_history
    store.save([], {"unknown": "keep"})
    lock = store.session_dir.parent / f".{store.session_dir.name}.metadata.lock"
    inode = lock.stat().st_ino
    with metadata_lock(store.session_dir):
        api = history_win._API()
        handle = api.open(lock, write=True)
        try:
            getter = api.kernel.GetHandleInformation
            getter.argtypes, getter.restype = [win.HANDLE, win.PDWORD], win.BOOL
            flags = win.DWORD()
            api.ok(getter(handle, C.byref(flags)))
            assert not flags.value & 1
        finally:
            api.close(handle)
        with pytest.raises(OSError):
            lock.unlink()
        result = subprocess.run(
            [sys.executable, "-I", "-B", "-c",
             "import sys,ctypes as C\n"
             "from pathlib import Path\n"
             "from amplifier_foundation.session import _windows_ownership as w\n"
             "a=w._API(); p=Path(sys.argv[1]); h=a.open(p,write=True)\n"
             "o=w.OVERLAPPED(); result=a.LockFileEx(h,3,0,1,0,C.byref(o))\n"
             "error=C.get_last_error(); a.close(h)\n"
             "sys.exit(23 if not result and error==33 else 1)\n", str(lock)],
            capture_output=True, text=True, timeout=15,
        )
        assert result.returncode == 23, result.stderr
    SessionMetadataStore(store.session_dir).set_name("Canonical")
    store.save([], {"turn": 2}, merge_metadata=True, incremental=True)
    assert store.load_metadata()["name"] == "Canonical"
    assert store.load_metadata()["unknown"] == "keep"
    assert lock.stat().st_ino == inode