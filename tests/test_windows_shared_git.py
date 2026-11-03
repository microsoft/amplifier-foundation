"""Inert lifetime models plus native, offline Windows Job/pipe falsifiers.

Portable tests never start processes/threads or call a Windows kernel. Native
checks are selected by the existing matrix, not simulated by portable passes.
"""
from __future__ import annotations

import asyncio
import concurrent.futures
import ctypes as C
import json
import os
from pathlib import Path
import subprocess
import sys
import textwrap

import pytest

from amplifier_foundation.sources import _windows_git as git


class Clock:
    now = 0.0

    def __call__(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds


class Kernel:
    """An inert kernel, not a subprocess double pretending native execution."""
    def __init__(self, clock, *, blocks=(b"ok\r\n", b""), code=0, fault=None):
        self.clock, self.blocks, self.code, self.fault = clock, list(blocks), code, fault
        self.calls, self.terminated, self.owner = [], False, None

    def start(self, owner):
        self.owner = owner
        owner.job, owner.process, owner.thread, owner.pipe = [owner.own(n) for n in (11, 12, 13, 14)]
        owner.read.overlap.event = owner.own(15)
        self.calls.extend(["suspended", "assign"])
        if self.fault == "assign":
            raise PermissionError("synthetic assignment refusal")
        owner.assigned = owner.verified = True
        self.calls.extend(["verify", "resume"])

    def issue(self, owner):
        if self.blocks:
            return self.blocks.pop(0)
        owner.read.pending = True
        return None

    def finish(self, owner):
        if self.terminated and self.fault not in ("io", "cancel"):
            owner.read.pending = False
            return b""
        return None

    def wait(self, handle, seconds):
        self.clock.sleep(min(seconds, 0.05))
        return False

    def exit_code(self, handle):
        return self.code

    def terminate(self, owner):
        self.calls.append("terminate_job" if owner.assigned else "terminate_suspended")
        if self.fault == "terminate":
            raise PermissionError("synthetic secret must not appear in cleanup facts")
        self.terminated = True

    def empty(self, owner):
        return self.terminated or owner.process is None

    def cancel_read(self, owner):
        self.calls.append("cancel_read")
        if self.fault == "cancel":
            raise PermissionError("synthetic cancellation refusal")

    def close(self, handle):
        self.calls.append(("close", handle))
        if self.fault == "close" and handle == 15:
            raise OSError("synthetic close refusal")


def operation(**kwargs):
    clock = Clock()
    kernel = Kernel(clock, **kwargs)
    owner = git._Operation(["synthetic", "argument with spaces"], 35, api=kernel,
                           clock=clock, sleep=clock.sleep)
    return owner, kernel, clock


def test_windows_git_abi_layout_is_win64_not_host_ulong():
    assert C.sizeof(git.DWORD) == C.sizeof(git.BOOL) == 4
    if C.sizeof(C.c_void_p) == 8:
        assert C.sizeof(git._Overlapped) == 32
        assert C.sizeof(git._Startup) == 104
        assert C.sizeof(git._StartupEx) == 112
        assert C.sizeof(git._Process) == 24
        assert C.sizeof(git._Limits) == 144
    assert C.sizeof(git._Accounting) == 48


def test_windows_git_inert_complete_large_output_and_no_leader_exit_shortcut():
    data = (b"a" * 70000 + b"\r\n") * 4
    owner, kernel, _ = operation(blocks=[data[:60000], data[60000:], b""])
    assert owner.run() == data.decode().replace("\r\n", "\n")
    assert kernel.blocks == [] and kernel.calls[:4] == ["suspended", "assign", "verify", "resume"]
    assert owner.cleanup["tree_empty"] and owner.cleanup["io_finalized"]
    assert owner.cleanup["handles_closed"] and not owner.cleanup["errors"]
    assert id(owner) not in git._PENDING


@pytest.mark.parametrize("fault", [None, "terminate", "cancel", "io"])
def test_windows_git_inert_timeout_preserves_original_and_retains_unknown(fault):
    owner, kernel, clock = operation(blocks=[b"partial"], fault=fault)
    storage = owner.read
    try:
        with pytest.raises(subprocess.TimeoutExpired) as caught:
            owner.run()
        assert caught.value.timeout == 35
        assert caught.value.cmd == owner.args
        assert caught.value.cleanup is owner.cleanup
        assert 35 <= clock.now <= 40.051
        assert owner.read is storage
        assert "synthetic secret" not in repr(owner.cleanup)
        if fault:
            assert git._PENDING[id(owner)] is owner
            assert not owner.cleanup["handles_closed"]
            assert not any(isinstance(call, tuple) for call in kernel.calls)
            assert caught.value.__notes__
        else:
            assert owner.cleanup["handles_closed"]
            assert owner.cleanup["tree_empty"] and owner.cleanup["io_finalized"]
    finally:
        # These are integer-only fake handles, not native cleanup or evidence.
        git._PENDING.pop(id(owner), None)


def test_windows_git_inert_close_failure_cannot_return_success_and_same_owner_can_settle():
    owner, kernel, _ = operation(fault="close")
    with pytest.raises(git._CleanupError) as caught:
        owner.run()
    assert caught.value.cleanup["tree_empty"] and caught.value.cleanup["io_finalized"]
    assert not caught.value.cleanup["handles_closed"]
    assert owner.handles == [15] and git._PENDING[id(owner)] is owner
    kernel.fault = None
    assert owner.settle()["handles_closed"]
    assert id(owner) not in git._PENDING


def test_windows_git_inert_assignment_failure_never_resumes_or_falls_back():
    owner, kernel, _ = operation(fault="assign")
    with pytest.raises(PermissionError) as caught:
        owner.run()
    assert "resume" not in kernel.calls
    assert "terminate_suspended" in kernel.calls
    assert not caught.value.cleanup["assigned"]
    assert caught.value.cleanup["handles_closed"]


@pytest.mark.parametrize("cancel", [False, True])
def test_windows_git_inert_primary_failure_after_exit_or_cancellation_is_preserved(cancel):
    owner, kernel, _ = operation(blocks=[b"normal output", b""], code=7)
    if cancel:
        owner.cancel.set()
        expected = git._Cancelled
    else:
        expected = subprocess.CalledProcessError
    with pytest.raises(expected) as caught:
        owner.run()
    assert caught.value.cleanup["handles_closed"]
    if not cancel:
        assert caught.value.returncode == 7 and caught.value.output == "normal output"


@pytest.mark.asyncio
@pytest.mark.parametrize(("state", "repeat", "unknown"), [
    ("running", False, False), ("running", True, False),
    ("running", False, True), ("running", True, True),
    ("queued", False, False),
])
async def test_windows_git_inert_simultaneous_adapter_cancel_requires_worker_ack(
    monkeypatch, state, repeat, unknown,
):
    """Real to_thread/asyncio cancellation, RUNNING executor future, no thread.

    Execution is split at the worker acknowledgement boundary. The executor
    double holds a genuinely RUNNING concurrent Future without invoking native
    work; the same synchronous worker wrapper is tested separately below.
    """
    import threading

    loop = asyncio.get_running_loop()
    submitted = asyncio.Event()
    owners, calls, submission = [], [], {}
    facts = {
        "handles_closed": not unknown, "tree_empty": not unknown,
        "io_finalized": not unknown, "errors": [], "owner_retained": unknown,
    }

    class Owner:
        def __init__(self):
            self.cancel, self.cleanup = threading.Event(), {}

        def run(self):
            calls.append("payload")

    def make(*args, **kwargs):
        owner = Owner()
        owners.append(owner)
        return owner

    def executor(pool, function):
        # asyncio.to_thread supplies partial(context.run, bound worker method).
        assert pool is None
        target = function.args[0].__self__
        native = concurrent.futures.Future()
        if state == "running":
            assert native.set_running_or_notify_cancel()
            # Model a worker already past its independent atomic start gate.
            if hasattr(target, "completion"):
                assert target.completion.set_running_or_notify_cancel()
        submission.update(function=function, target=target, native=native)
        submitted.set()
        return asyncio.wrap_future(native, loop=loop)

    monkeypatch.setattr(git, "_Operation", make)
    monkeypatch.setattr(loop, "run_in_executor", executor)
    writer_lock = asyncio.Lock()

    async def locked_read():
        async with writer_lock:
            return await git.check_output(["synthetic"])

    before = asyncio.all_tasks()
    caller = asyncio.create_task(locked_read())
    await submitted.wait()
    adapters = asyncio.all_tasks() - before - {caller}
    assert len(adapters) == 1
    adapter = adapters.pop()
    caller.cancel("original caller cancellation")
    adapter.cancel("independent adapter shutdown")
    try:
        for _ in range(6):
            await asyncio.sleep(0)
        assert owners[0].cancel.is_set()
        if state == "running":
            assert submission["native"].running()
            assert adapter.cancelled()
            assert not caller.done(), "adapter completion is not worker completion"
            assert writer_lock.locked()
            original = caller.get_coro().cr_await.cr_frame.f_locals["cancelled"]
            if repeat:
                caller.cancel("later cancellation")
                for _ in range(3):
                    await asyncio.sleep(0)
                assert not caller.done() and writer_lock.locked()
            owners[0].cleanup = facts
            if unknown:
                git._PENDING[id(owners[0])] = owners[0]  # Inert owner/storage.
            target = submission["target"]
            target.completion.set_exception(git._Cancelled())
            submission["native"].set_result(None)
        else:
            assert submission["native"].cancelled()
            # The revoked wrapper must be safe even if a queued callback is
            # delivered later: no native payload may begin after lock release.
            submission["function"]()
            assert not calls
        with pytest.raises(asyncio.CancelledError) as caught:
            await caller
        assert not writer_lock.locked()
        assert caught.value.args == ("original caller cancellation",)
        if state == "running":
            assert caught.value is original
            assert caught.value.cleanup is facts
            assert (git._PENDING.get(id(owners[0])) is owners[0]) == unknown
        else:
            assert caught.value.cleanup["worker_never_started"] is True
            assert caught.value.cleanup["handles_closed"] is True
        assert len(owners) == 1 and not calls
    finally:
        # Settle fake execution even when the pre-fix assertion fails.
        target = submission["target"]
        completion = getattr(target, "completion", None)
        if completion is not None and completion.running():
            owners[0].cleanup = facts
            completion.set_exception(git._Cancelled())
        if submission["native"].running():
            submission["native"].set_result(None)
        if not caller.done():
            try:
                await caller
            except asyncio.CancelledError:
                pass
        git._PENDING.pop(id(owners[0]), None)  # Fake only, never native teardown.


@pytest.mark.parametrize("outcome", ["success", "timeout", "cancelled", "unknown", "revoked"])
def test_windows_git_inert_worker_ack_is_after_actual_owned_settlement(outcome):
    """Actual synchronous wrapper + actual operation with an inert kernel."""
    owner, kernel, clock = operation(
        blocks=[b"partial"] if outcome in ("timeout", "unknown") else [b"ok\r\n", b""],
        fault="io" if outcome == "unknown" else None,
    )
    worker = git._Worker(owner)
    close, wait = kernel.close, kernel.wait

    def close_before_ack(handle):
        assert worker.completion.running() and not worker.completion.done()
        close(handle)

    def wait_before_ack(handle, seconds):
        assert worker.completion.running() and not worker.completion.done()
        return wait(handle, seconds)

    kernel.close, kernel.wait = close_before_ack, wait_before_ack
    if outcome == "cancelled":
        owner.cancel.set()
    elif outcome == "revoked":
        assert worker.completion.cancel()
    try:
        worker.run()
        assert worker.completion.done()
        if outcome == "revoked":
            assert worker.completion.cancelled() and not kernel.calls
            assert not owner.cleanup and not owner.handles
        elif outcome == "success":
            assert worker.completion.result() == "ok\n"
            assert owner.cleanup["handles_closed"]
        else:
            expected = git._Cancelled if outcome == "cancelled" else subprocess.TimeoutExpired
            with pytest.raises(expected) as caught:
                worker.completion.result()
            assert caught.value.cleanup is owner.cleanup
            assert clock.now <= 40.051
            assert (git._PENDING.get(id(owner)) is owner) == (outcome == "unknown")
    finally:
        git._PENDING.pop(id(owner), None)  # Fake kernel/storage only.


@pytest.mark.asyncio
@pytest.mark.parametrize("outcome", ["success", "failure"])
async def test_windows_git_inert_async_ack_normal_result_and_failure(monkeypatch, outcome):
    """Actual to_thread with synchronous inert executor delivery (no thread)."""
    loop = asyncio.get_running_loop()
    primary = ValueError("synthetic worker failure")
    calls, owners = [], []

    class Owner:
        def __init__(self):
            import threading
            self.cancel, self.cleanup = threading.Event(), {}

        def run(self):
            calls.append("run")
            if outcome == "failure":
                raise primary
            return "result"

    def make(*args, **kwargs):
        owner = Owner()
        owners.append(owner)
        return owner

    def executor(pool, function):
        future = concurrent.futures.Future()
        assert future.set_running_or_notify_cancel()
        future.set_result(function())  # Actual wrapper executes, inert Owner only.
        return asyncio.wrap_future(future, loop=loop)

    monkeypatch.setattr(git, "_Operation", make)
    monkeypatch.setattr(loop, "run_in_executor", executor)
    caller = asyncio.create_task(git.check_output(["synthetic"]))
    if outcome == "failure":
        with pytest.raises(ValueError) as caught:
            await caller
        assert caught.value is primary and calls == ["run"]
    else:
        assert await caller == "result" and calls == ["run"]
    assert len(owners) == 1


@pytest.mark.parametrize("failure", ["assign", "verify", "cancel", "inheritable", "resume", None])
def test_windows_git_actual_native_launch_body_is_inert_and_assigns_before_payload(monkeypatch, failure):
    """Exercise actual ctypes argument construction without invoking any DLL."""
    calls, last_error = [], [535]
    monkeypatch.setattr(C, "get_last_error", lambda: last_error[0], raising=False)
    monkeypatch.setattr(C, "WinError", lambda n: OSError(n, "synthetic native refusal"), raising=False)

    class Native:
        def CreateJobObjectW(self, security, name):
            assert security is name is None
            return 101

        def SetInformationJobObject(self, job, kind, pointer, size):
            assert job == 101 and kind == 9 and size == C.sizeof(git._Limits)
            assert pointer._obj.basic.flags == 0x2000
            return True

        def CreateNamedPipeW(self, name, flags, mode, instances, *args):
            assert flags == 0x40080001 and mode == 0 and instances == 1
            return 102

        def CreateFileW(self, name, access, share, security, disposition, flags, template):
            assert security._obj.inherit and disposition == 3 and access == 0x40000000
            return 103

        def CreateEventW(self, *args):
            return 104

        def ConnectNamedPipe(self, pipe, overlap):
            assert overlap._obj.event == 104
            return False

        def GetStdHandle(self, which):
            return which

        def GetCurrentProcess(self):
            return -1

        def DuplicateHandle(self, source, handle, target, output, access, inherit, flags):
            assert inherit and flags == 2
            output._obj.value = 110 if handle == 2**32 - 10 else 111
            return True

        def InitializeProcThreadAttributeList(self, attributes, count, flags, size):
            assert count == 1 and flags == 0
            size._obj.value = 128
            return attributes is not None

        def UpdateProcThreadAttribute(self, attributes, flags, key, handles, size, previous, returned):
            assert list(handles) == [110, 103, 111]
            assert key == 0x20002 and size == 3 * C.sizeof(git.HANDLE)
            return True

        def DeleteProcThreadAttributeList(self, attributes):
            calls.append("delete_attributes")

        def CreateProcessW(self, executable, command, pa, ta, inherit, flags, env, cwd, startup, output):
            assert executable is env is cwd is None and inherit and flags == 0x80004
            assert command.value == subprocess.list2cmdline(["git", "space argument"])
            assert startup._obj.startup.cb == C.sizeof(git._StartupEx)
            assert startup._obj.startup.flags == 0x100
            calls.append("create_suspended")
            output._obj.process, output._obj.thread = 105, 106
            return True

        def AssignProcessToJobObject(self, job, process):
            assert (job, process) == (101, 105)
            calls.append("assign")
            return failure != "assign"

        def GetHandleInformation(self, handle, flags):
            assert handle in (101, 102, 104, 105, 106)
            flags._obj.value = 1 if failure == "inheritable" else 0
            return True

        def IsProcessInJob(self, process, job, output):
            calls.append("verify")
            output._obj.value = failure != "verify"
            if failure == "cancel":
                owner.cancel.set()
            return True

        def ResumeThread(self, thread):
            assert thread == 106
            calls.append("resume")
            return 0xFFFFFFFF if failure == "resume" else 1

        def CloseHandle(self, handle):
            calls.append(("close", handle))
            return True

    api = git._API.__new__(git._API)
    api.k = Native()
    owner = git._Operation(["git", "space argument"], 35, api=api)
    owner.deadline = owner.clock() + 35
    if failure:
        with pytest.raises((OSError, RuntimeError, git._Cancelled)):
            api.start(owner)
        assert ("resume" in calls) == (failure == "resume")
    else:
        api.start(owner)
        assert calls.index("assign") < calls.index("verify") < calls.index("resume")
    # Nothing native exists: these handles are model integers only.


@pytest.mark.parametrize("status", ["incomplete", "cancelled", "broken", "success", "unknown_error"])
def test_windows_git_actual_overlapped_body_does_not_free_pending_storage(monkeypatch, status):
    error = {"incomplete": 996, "cancelled": 995, "broken": 109, "success": 0, "unknown_error": 6}[status]
    monkeypatch.setattr(C, "get_last_error", lambda: error, raising=False)
    monkeypatch.setattr(C, "WinError", lambda n: OSError(n, "synthetic"), raising=False)

    class Native:
        def CancelIoEx(self, pipe, overlap):
            assert pipe == 10 and overlap._obj is owner.read.overlap
            return True

        def GetOverlappedResult(self, pipe, overlap, size, wait):
            assert pipe == 10 and not wait
            size._obj.value = 3
            return status == "success"

    api = git._API.__new__(git._API)
    api.k = Native()
    owner = git._Operation(["synthetic"], 35, api=api)
    owner.pipe = 10
    owner.read.pending = True
    owner.read.buffer.value = b"abc"
    storage = owner.read
    api.cancel_read(owner)
    assert owner.read.pending and owner.read is storage
    if status == "unknown_error":
        with pytest.raises(OSError):
            api.finish(owner)
    elif status == "cancelled":
        with pytest.raises(OSError):
            api.finish(owner)  # Unrequested abort cannot be accepted as EOF.
        owner.cleaning, owner.read.pending = True, True
        assert api.finish(owner) == b""
    else:
        assert api.finish(owner) == {"incomplete": None, "broken": b"", "success": b"abc"}[status]
    assert owner.read.pending == (status in ("incomplete", "unknown_error"))


NATIVE = pytest.mark.skipif(os.name != "nt" or C.sizeof(C.c_void_p) != 8,
                            reason="Requires actual Windows x64 Job and overlapped pipe execution")


@NATIVE
def test_native_shared_git_complete_output_and_nested_outer_job():
    """Inner operation runs inside a real outer Job, like an installed worker."""
    root = str(Path(__file__).resolve().parent.parent)
    script = textwrap.dedent(f"""
        import json,sys
        sys.path.insert(0, {root!r})
        from amplifier_foundation.sources._windows_git import _Operation, _PENDING
        block = '0123456789abcdef' * 32768
        # Keep command line below the native limit; generate large output inside.
        child = "import sys; sys.stdout.buffer.write(b'0123456789abcdef'*32768); sys.stdout.buffer.flush()"
        inner = _Operation([sys.executable, '-c', child], 15)
        output = inner.run()
        assert output == block and not _PENDING
        assert inner.cleanup['membership_verified'] and inner.cleanup['tree_empty']
        assert inner.cleanup['io_finalized'] and inner.cleanup['handles_closed']
        print(json.dumps({{'size':len(output), 'cleanup':inner.cleanup}}))
    """)
    outer = git._Operation([sys.executable, "-c", script], 25)
    result = json.loads(outer.run())
    assert result["size"] == 524288
    assert outer.cleanup["tree_empty"] and outer.cleanup["handles_closed"]


@NATIVE
def test_native_shared_git_exited_leader_helper_keeps_stdout_but_timeout_is_bounded(tmp_path):
    """Leader exits; a real Job-member helper holds stdout until timeout cleanup."""
    import time
    ready = tmp_path / "child.json"
    child = (
        "import json,os,time; from pathlib import Path; "
        f"ready=Path({str(ready)!r}); temporary=ready.with_suffix('.tmp'); "
        "temporary.write_text(json.dumps(os.getpid())); temporary.replace(ready); time.sleep(120)"
    )
    leader = (
        "import subprocess,sys; "
        f"subprocess.Popen([sys.executable,'-c',{child!r}]); print('partial',flush=True)"
    )
    api = git._API()
    k = api.k
    k.OpenProcess.restype, k.OpenProcess.argtypes = git.HANDLE, [git.DWORD, git.BOOL, git.DWORD]
    observed = {}
    original_exit, original_close = api.exit_code, api.close

    def inspect_member(process):
        if ready.exists() and "member" not in observed:
            pid = json.loads(ready.read_text())
            handle = api.handle(k.OpenProcess(0x101000, False, pid))
            owner.own(handle)  # Retain actual identity through cleanup, no PID kill.
            member = git.BOOL()
            api.check(k.IsProcessInJob(handle, owner.job, C.byref(member)))
            assert member.value
            observed["member"] = handle
        code = original_exit(process)
        if process == owner.process and code is not None and not owner.cleaning:
            observed["leader_exited"] = True
        return code

    def close(handle):
        if handle == observed.get("member"):
            assert original_exit(handle) is not None
            observed["child_exited"] = True
        original_close(handle)

    api.exit_code, api.close = inspect_member, close
    owner = git._Operation([sys.executable, "-c", leader], 3, api=api)
    before = time.monotonic()
    with pytest.raises(subprocess.TimeoutExpired) as caught:
        owner.run()
    assert caught.value.timeout == 3 and time.monotonic() - before < 9
    assert observed.get("child_exited")
    assert observed.get("leader_exited"), "must actually reach the exited-leader/held-pipe case"
    assert owner.cleanup["tree_empty"] and owner.cleanup["process_exited"]
    assert owner.cleanup["io_finalized"] and owner.cleanup["handles_closed"]
    assert not owner.cleanup["errors"] and not git._PENDING


@NATIVE
def test_native_shared_git_concurrent_owner_and_unrelated_sentinel_survive():
    import concurrent.futures
    import threading

    started = threading.Event()

    class Ready(git._API):
        def start(self, owner):
            super().start(owner)
            started.set()

    other = git._Operation([sys.executable, "-c", "import time; time.sleep(2); print('other')"], 15)
    sentinel = git._Operation([sys.executable, "-c", "import time; time.sleep(120)"], 15, api=Ready())
    doomed = git._Operation([sys.executable, "-c", "import time; time.sleep(120)"], 0.5)
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        first, second = pool.submit(other.run), pool.submit(sentinel.run)
        try:
            assert started.wait(5)
            with pytest.raises(subprocess.TimeoutExpired):
                doomed.run()
            assert sentinel.api.exit_code(sentinel.process) is None
            assert first.result(timeout=20) == "other\n"
            sentinel.cancel.set()
            with pytest.raises(git._Cancelled):
                second.result(timeout=10)
        finally:
            other.cancel.set()
            sentinel.cancel.set()
    for owner in (doomed, other, sentinel):
        assert owner.cleanup["tree_empty"] and owner.cleanup["handles_closed"]
    assert not git._PENDING


@NATIVE
def test_native_shared_git_assignment_refusal_precedes_any_payload(tmp_path):
    marker = tmp_path / "must-not-exist"
    api = git._API()
    original = api.k.AssignProcessToJobObject

    def refuse(job, process):
        C.set_last_error(5)
        return False

    api.k.AssignProcessToJobObject = refuse
    owner = git._Operation(
        [sys.executable, "-c", f"from pathlib import Path; Path({str(marker)!r}).write_text('payload')"],
        10, api=api,
    )
    try:
        with pytest.raises(PermissionError):
            owner.run()
    finally:
        api.k.AssignProcessToJobObject = original
    assert not marker.exists() and not owner.cleanup["assigned"]
    assert owner.cleanup["process_exited"] and owner.cleanup["tree_empty"]
    assert owner.cleanup["handles_closed"] and not owner.cleanup["errors"]


@NATIVE
@pytest.mark.parametrize("fault", ["io_completion", "close"])
def test_native_shared_git_cleanup_fault_retains_same_owner_until_confirmed(fault):
    api = git._API()
    original_finish, original_close = api.finish, api.close

    def finish(owner):
        if owner.cleaning and fault == "io_completion":
            raise OSError("synthetic completion observation failure")
        return original_finish(owner)

    def close(handle):
        if fault == "close" and handle == owner.read.overlap.event:
            raise OSError("synthetic close failure")
        original_close(handle)

    api.finish, api.close = finish, close
    owner = git._Operation([sys.executable, "-c", "import time; time.sleep(120)"], 3, api=api)
    storage = owner.read
    try:
        with pytest.raises(subprocess.TimeoutExpired) as caught:
            owner.run()
        assert caught.value.timeout == 3 and caught.value.cleanup["errors"]
        assert git._PENDING[id(owner)] is owner and owner.read is storage
        assert not caught.value.cleanup["handles_closed"]
    finally:
        api.finish, api.close = original_finish, original_close
        facts = owner.settle()
        assert facts["tree_empty"] and facts["io_finalized"] and facts["handles_closed"]
        assert not facts["errors"] and id(owner) not in git._PENDING


@NATIVE
@pytest.mark.asyncio
@pytest.mark.parametrize("cancel_adapter", [False, True])
async def test_native_shared_git_async_cancel_settles_before_return(monkeypatch, tmp_path, cancel_adapter):
    ready = tmp_path / "ready"
    child = f"import time; from pathlib import Path; Path({str(ready)!r}).write_text('ready'); time.sleep(120)"
    owners = []
    real_operation = git._Operation

    def make(*args, **kwargs):
        owner = real_operation(*args, **kwargs)
        owners.append(owner)
        return owner

    monkeypatch.setattr(git, "_Operation", make)
    before = asyncio.all_tasks()
    task = asyncio.create_task(git.check_output([sys.executable, "-c", child]))
    try:
        deadline = asyncio.get_running_loop().time() + 10
        while not ready.exists():
            assert not task.done(), "native child failed before readiness"
            assert asyncio.get_running_loop().time() < deadline
            await asyncio.sleep(0.02)
        task.cancel()
        if cancel_adapter:
            adapters = asyncio.all_tasks() - before - {task}
            assert len(adapters) == 1
            adapters.pop().cancel()  # Same failure mode as loop-wide shutdown.
        with pytest.raises(asyncio.CancelledError) as caught:
            await asyncio.wait_for(task, 7)
        assert caught.value.cleanup["tree_empty"]
        assert caught.value.cleanup["io_finalized"] and caught.value.cleanup["handles_closed"]
        assert not caught.value.cleanup["errors"] and not git._PENDING
    finally:
        if not task.done():
            task.cancel()
            try:
                await asyncio.wait_for(task, 7)
            except asyncio.CancelledError:
                pass
