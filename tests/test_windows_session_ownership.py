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
def test_owner_publication_completes_partial_writes_or_never_replaces(failure):
    events, written = [], bytearray()

    class API:
        ok = staticmethod(win._API.ok)

        def open(self, path, **kwargs):
            assert path.name != "session.lock"
            if path.name == "owner.json":
                error = FileNotFoundError()
                error.winerror = 2
                raise error
            assert kwargs == {"exclusive": True, "write": True}
            return 123

        def WriteFile(self, handle, buffer, length, count, overlapped):
            if failure == "write":
                raise PermissionError("write failed")
            size = min(2, length)
            written.extend(C.string_at(buffer, size))
            C.cast(count, win.PDWORD)[0] = size
            return 1

        def FlushFileBuffers(self, handle):
            if failure == "flush":
                raise PermissionError("flush failed")
            events.append("flushed")
            return 1

        def close(self, handle):
            events.append(("closed", handle))

        def MoveFileExW(self, source, target, flags):
            assert target.endswith("owner.json") and flags == 1
            if failure == "replace":
                raise PermissionError("replace failed")
            events.append("replaced")
            return 1

        def DeleteFileW(self, path):
            events.append("temporary-deleted")
            return 1

    ownership = win.Ownership.__new__(win.Ownership)
    ownership.directory, ownership.api = PureWindowsPath(r"C:\state"), API()
    ownership.check = lambda: None
    data = b'{"app":"complete-owner","active":true}'
    if failure:
        with pytest.raises(PermissionError):
            ownership.publish_owner(data)
        assert "replaced" not in events and events[-1] == "temporary-deleted"
    else:
        ownership.publish_owner(data)
        assert written == data
        assert events == ["flushed", ("closed", 123), "replaced"]


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
    api = first._windows.api
    reader = api.open(store._owner_path, advisory_read=True)
    try:
        first.release()
        successor = store.acquire(app="successor")
        try:
            assert json.loads(store._owner_path.read_bytes())["app"] == "successor"
            api.validate_acl(reader, private=True)  # Detached old snapshot remains safe.
        finally:
            successor.release()
    finally:
        api.close(reader)
        first.release()


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