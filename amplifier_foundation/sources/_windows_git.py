"""Private Windows lifetime boundary for shared-source ls-remote only.

No PID-based termination, subprocess reader threads, or unbounded pipe drains.
Git runs only after assignment to an owned, non-breakaway Job. The operation
budget includes stdout EOF, not just leader exit. One additional cleanup budget
covers tree exit AND pending overlapped I/O. A failed cleanup retains its owner
and native storage; cancellation requested is not completion. This is process
lifetime management, not a sandbox against external services/brokers.
"""

from __future__ import annotations

import asyncio
from concurrent.futures import Future
import ctypes as C
import io
import locale
import math
import os
import subprocess
import sys
import threading
import time
import uuid
from dataclasses import dataclass, field

# Fixed-width Windows ABI, also inspectable by portable tests (c_ulong is not
# a Windows DWORD on LP64 hosts). Do not import the session security backend.
DWORD, BOOL, WORD = C.c_uint32, C.c_int32, C.c_uint16
HANDLE, SIZE_T = C.c_void_p, C.c_size_t
INVALID_HANDLE = C.c_void_p(-1).value
WAIT_TIMEOUT = 258
ERROR_IO_PENDING, ERROR_IO_INCOMPLETE = 997, 996
ERROR_BROKEN_PIPE, ERROR_NOT_FOUND = 109, 1168
CLEANUP_SECONDS = 5.0


class _Security(C.Structure):
    _fields_ = [("length", DWORD), ("descriptor", HANDLE), ("inherit", BOOL)]


class _Startup(C.Structure):
    _fields_ = [
        ("cb", DWORD), ("reserved", C.c_wchar_p), ("desktop", C.c_wchar_p),
        ("title", C.c_wchar_p), ("x", DWORD), ("y", DWORD),
        ("xsize", DWORD), ("ysize", DWORD), ("xchars", DWORD),
        ("ychars", DWORD), ("fill", DWORD), ("flags", DWORD),
        ("show", WORD), ("reserved_size", WORD), ("reserved_bytes", HANDLE),
        ("stdin", HANDLE), ("stdout", HANDLE), ("stderr", HANDLE),
    ]


class _StartupEx(C.Structure):
    _fields_ = [("startup", _Startup), ("attributes", HANDLE)]


class _Process(C.Structure):
    _fields_ = [("process", HANDLE), ("thread", HANDLE), ("pid", DWORD), ("tid", DWORD)]


class _Offset(C.Structure):
    _fields_ = [("low", DWORD), ("high", DWORD)]


class _Position(C.Union):
    _fields_ = [("offset", _Offset), ("pointer", HANDLE)]


class _Overlapped(C.Structure):
    _fields_ = [("internal", SIZE_T), ("internal_high", SIZE_T),
                ("position", _Position), ("event", HANDLE)]


class _BasicLimit(C.Structure):
    _fields_ = [("process_time", C.c_int64), ("job_time", C.c_int64),
                ("flags", DWORD), ("min_working", SIZE_T), ("max_working", SIZE_T),
                ("active_limit", DWORD), ("affinity", SIZE_T),
                ("priority", DWORD), ("scheduling", DWORD)]


class _IoCounters(C.Structure):
    _fields_ = [(name, C.c_uint64) for name in
                ("read_ops", "write_ops", "other_ops", "read_bytes", "write_bytes", "other_bytes")]


class _Limits(C.Structure):
    _fields_ = [("basic", _BasicLimit), ("io", _IoCounters),
                ("process_memory", SIZE_T), ("job_memory", SIZE_T),
                ("peak_process", SIZE_T), ("peak_job", SIZE_T)]


class _Accounting(C.Structure):
    _fields_ = [(name, C.c_int64) for name in
                ("user", "kernel", "period_user", "period_kernel")] + [
                    ("faults", DWORD), ("total", DWORD),
                    ("active", DWORD), ("terminated", DWORD)]


@dataclass
class _Read:
    # These objects MUST outlive pending native I/O, including error unwinds.
    buffer: object = field(default_factory=lambda: C.create_string_buffer(65536))
    overlap: _Overlapped = field(default_factory=_Overlapped)
    pending: bool = False


class _Cancelled(Exception):
    """Internal worker-thread signal; the async caller retains CancelledError."""


class _CleanupError(RuntimeError):
    def __init__(self, facts):
        super().__init__("Windows shared Git cleanup was not confirmed")
        self.cleanup = facts


# Strong ownership, deliberately not a WeakSet or background reaper. An unknown
# operation must remain inspectable and cannot release pending I/O storage.
# Entries are removed only by that same owner's later confirmed settlement.
_PENDING: dict[int, _Operation] = {}


class _API:
    def __init__(self):
        if os.name != "nt":
            raise OSError("Windows shared Git requires native Windows")
        k = self.k = C.WinDLL("kernel32", use_last_error=True)
        signatures = {
            "CreateJobObjectW": (HANDLE, [HANDLE, C.c_wchar_p]),
            "SetInformationJobObject": (BOOL, [HANDLE, C.c_int32, HANDLE, DWORD]),
            "QueryInformationJobObject": (BOOL, [HANDLE, C.c_int32, HANDLE, DWORD, HANDLE]),
            "AssignProcessToJobObject": (BOOL, [HANDLE, HANDLE]),
            "IsProcessInJob": (BOOL, [HANDLE, HANDLE, C.POINTER(BOOL)]),
            "TerminateJobObject": (BOOL, [HANDLE, DWORD]),
            "TerminateProcess": (BOOL, [HANDLE, DWORD]),
            "CreateNamedPipeW": (HANDLE, [C.c_wchar_p, DWORD, DWORD, DWORD, DWORD, DWORD, DWORD, HANDLE]),
            "CreateFileW": (HANDLE, [C.c_wchar_p, DWORD, DWORD, HANDLE, DWORD, DWORD, HANDLE]),
            "ConnectNamedPipe": (BOOL, [HANDLE, HANDLE]),
            "CreatePipe": (BOOL, [C.POINTER(HANDLE), C.POINTER(HANDLE), HANDLE, DWORD]),
            "GetStdHandle": (HANDLE, [DWORD]),
            "GetCurrentProcess": (HANDLE, []),
            "DuplicateHandle": (BOOL, [HANDLE, HANDLE, HANDLE, C.POINTER(HANDLE), DWORD, BOOL, DWORD]),
            "InitializeProcThreadAttributeList": (BOOL, [HANDLE, DWORD, DWORD, C.POINTER(SIZE_T)]),
            "UpdateProcThreadAttribute": (BOOL, [HANDLE, DWORD, SIZE_T, HANDLE, SIZE_T, HANDLE, HANDLE]),
            "DeleteProcThreadAttributeList": (None, [HANDLE]),
            "CreateProcessW": (BOOL, [C.c_wchar_p, C.c_wchar_p, HANDLE, HANDLE, BOOL,
                                      DWORD, HANDLE, C.c_wchar_p, HANDLE, C.POINTER(_Process)]),
            "ResumeThread": (DWORD, [HANDLE]),
            "CreateEventW": (HANDLE, [HANDLE, BOOL, BOOL, C.c_wchar_p]),
            "ResetEvent": (BOOL, [HANDLE]),
            "ReadFile": (BOOL, [HANDLE, HANDLE, DWORD, C.POINTER(DWORD), C.POINTER(_Overlapped)]),
            "GetOverlappedResult": (BOOL, [HANDLE, C.POINTER(_Overlapped), C.POINTER(DWORD), BOOL]),
            "CancelIoEx": (BOOL, [HANDLE, C.POINTER(_Overlapped)]),
            "WaitForSingleObject": (DWORD, [HANDLE, DWORD]),
            "GetExitCodeProcess": (BOOL, [HANDLE, C.POINTER(DWORD)]),
            "GetHandleInformation": (BOOL, [HANDLE, C.POINTER(DWORD)]),
            "CloseHandle": (BOOL, [HANDLE]),
        }
        for name, (restype, argtypes) in signatures.items():
            function = getattr(k, name)
            function.restype, function.argtypes = restype, argtypes

    def check(self, value):
        if not value:
            raise C.WinError(C.get_last_error())
        return value

    def handle(self, value):
        if value in (None, 0, INVALID_HANDLE):
            raise C.WinError(C.get_last_error())
        return value

    def close(self, handle):
        self.check(self.k.CloseHandle(handle))

    def _standard(self, owner, which):
        handle = self.k.GetStdHandle(DWORD(which).value)
        if handle in (None, 0, INVALID_HANDLE):
            # Match Popen(stdin=None/stderr=None) when no standard handle exists:
            # disconnected pipe, not a new console, NUL policy, or prompt change.
            read, write = HANDLE(), HANDLE()
            security = _Security(C.sizeof(_Security), None, True)
            self.check(self.k.CreatePipe(C.byref(read), C.byref(write), C.byref(security), 0))
            owner.own(read.value)
            owner.own(write.value)
            keep, discard = (read.value, write.value) if which == -10 else (write.value, read.value)
            owner.close(discard)
            return keep
        duplicate = HANDLE()
        current = self.k.GetCurrentProcess()
        self.check(self.k.DuplicateHandle(current, handle, current, C.byref(duplicate), 0, True, 2))
        return owner.own(duplicate.value)

    def start(self, owner):
        k = self.k
        owner.job = owner.own(self.handle(k.CreateJobObjectW(None, None)))
        limits = _Limits()
        limits.basic.flags = 0x2000  # KILL_ON_JOB_CLOSE; no breakaway flags.
        self.check(k.SetInformationJobObject(owner.job, 9, C.byref(limits), C.sizeof(limits)))
        name = "\\\\.\\pipe\\amplifier-shared-git-" + uuid.uuid4().hex
        owner.pipe = owner.own(self.handle(k.CreateNamedPipeW(
            name, 0x40080001, 0, 1, 65536, 65536, 0, None,
        )))  # INBOUND | OVERLAPPED | FIRST_PIPE_INSTANCE, byte mode.
        security = _Security(C.sizeof(_Security), None, True)
        writer = owner.own(self.handle(k.CreateFileW(
            name, 0x40000000, 0, C.byref(security), 3, 0, None,
        )))
        event = owner.own(self.handle(k.CreateEventW(None, True, False, None)))
        owner.read.overlap.event = event
        # The client is already connected. Still provide valid OVERLAPPED
        # storage: passing NULL on an overlapped server is not a safe shortcut.
        owner.read.pending = True
        if not k.ConnectNamedPipe(owner.pipe, C.byref(owner.read.overlap)):
            error = C.get_last_error()
            if error != 535:  # ERROR_PIPE_CONNECTED
                raise C.WinError(error)
        owner.read.pending = False
        stdin, stderr = self._standard(owner, -10), self._standard(owner, -12)
        attributes_size = SIZE_T()
        k.InitializeProcThreadAttributeList(None, 1, 0, C.byref(attributes_size))
        if not attributes_size.value:
            raise C.WinError(C.get_last_error())
        attributes = C.create_string_buffer(attributes_size.value)
        self.check(k.InitializeProcThreadAttributeList(attributes, 1, 0, C.byref(attributes_size)))
        try:
            handles = (HANDLE * 3)(stdin, writer, stderr)
            self.check(k.UpdateProcThreadAttribute(attributes, 0, 0x20002, handles,
                                                 C.sizeof(handles), None, None))
            startup = _StartupEx()
            startup.startup.cb = C.sizeof(startup)
            startup.startup.flags = 0x100  # STARTF_USESTDHANDLES
            startup.startup.stdin, startup.startup.stdout, startup.startup.stderr = stdin, writer, stderr
            startup.attributes = C.cast(attributes, HANDLE)
            process = _Process()
            command = C.create_unicode_buffer(subprocess.list2cmdline(owner.args))
            self.check(k.CreateProcessW(
                None, command, None, None, True, 0x08080004, None, None,
                C.byref(startup), C.byref(process),
            ))  # EXTENDED_STARTUPINFO_PRESENT | CREATE_SUSPENDED | CREATE_NO_WINDOW
            owner.process = owner.own(process.process)
            owner.thread = owner.own(process.thread)
        finally:
            k.DeleteProcThreadAttributeList(attributes)
        for handle in (owner.job, owner.pipe, event, owner.process, owner.thread):
            flags = DWORD()
            self.check(k.GetHandleInformation(handle, C.byref(flags)))
            if flags.value & 1:  # HANDLE_FLAG_INHERIT
                raise RuntimeError("Windows shared Git custody handle was inheritable")
        self.check(k.AssignProcessToJobObject(owner.job, owner.process))
        owner.assigned = True
        member = BOOL()
        self.check(k.IsProcessInJob(owner.process, owner.job, C.byref(member)))
        if not member.value:
            raise RuntimeError("Windows shared Git Job membership was not confirmed")
        owner.verified = True
        # Parent copies must not hold pipe writers alive. Do this before resume.
        for handle in (stdin, writer, stderr):
            owner.close(handle)
        owner.check_budget()
        previous_count = k.ResumeThread(owner.thread)
        if previous_count == 0xFFFFFFFF:
            raise C.WinError(C.get_last_error())
        if previous_count != 1:
            raise RuntimeError("Windows shared Git suspended launch was not confirmed")

    def issue(self, owner):
        read = owner.read
        self.check(self.k.ResetEvent(read.overlap.event))
        read.overlap.internal = read.overlap.internal_high = 0
        read.pending = True  # Retain storage even if a native call is interrupted.
        count = DWORD()
        if not self.k.ReadFile(owner.pipe, read.buffer, len(read.buffer), C.byref(count), C.byref(read.overlap)):
            error = C.get_last_error()
            if error == ERROR_IO_PENDING:
                return None
            if error == ERROR_BROKEN_PIPE:
                read.pending = False
                return b""
            raise C.WinError(error)
        read.pending = False
        return read.buffer.raw[:count.value]

    def finish(self, owner):
        count = DWORD()
        if not self.k.GetOverlappedResult(owner.pipe, C.byref(owner.read.overlap), C.byref(count), False):
            error = C.get_last_error()
            if error == ERROR_IO_INCOMPLETE:
                return None
            if error in (ERROR_BROKEN_PIPE, 995):
                owner.read.pending = False  # An observed completed/aborted read.
                if error == ERROR_BROKEN_PIPE or owner.cleaning:
                    return b""
            # An invalid handle/parameter or other query error does not prove
            # that native I/O released its OVERLAPPED/buffer storage.
            raise C.WinError(error)
        owner.read.pending = False
        return owner.read.buffer.raw[:count.value]

    def wait(self, handle, seconds):
        result = self.k.WaitForSingleObject(handle, max(0, min(50, math.ceil(seconds * 1000))))
        if result not in (0, WAIT_TIMEOUT):
            raise C.WinError(C.get_last_error())
        return result == 0

    def exit_code(self, process):
        if not self.wait(process, 0):
            return None
        code = DWORD()
        self.check(self.k.GetExitCodeProcess(process, C.byref(code)))
        return code.value

    def terminate(self, owner):
        if owner.assigned:
            self.check(self.k.TerminateJobObject(owner.job, 1))
        elif owner.process:
            # Assignment failed before resume: only this retained suspended
            # process can exist; never fall back to a PID or launch uncontained.
            self.check(self.k.TerminateProcess(owner.process, 1))

    def empty(self, owner):
        if not owner.job:
            return True
        accounting = _Accounting()
        self.check(self.k.QueryInformationJobObject(owner.job, 1, C.byref(accounting),
                                                   C.sizeof(accounting), None))
        return accounting.active == 0

    def cancel_read(self, owner):
        if not self.k.CancelIoEx(owner.pipe, C.byref(owner.read.overlap)):
            error = C.get_last_error()
            if error != ERROR_NOT_FOUND:
                raise C.WinError(error)
        # Cancellation requested != completed. finish() must settle the request.


class _Operation:
    def __init__(self, args, timeout, *, api=None, clock=time.monotonic, sleep=time.sleep):
        self.args, self.timeout = args, timeout
        self.cancel = threading.Event()
        self.api, self.clock = api, clock
        self.sleep = sleep
        self.job = self.process = self.thread = self.pipe = None
        self.assigned = self.verified = False
        self.handles = []
        self.read = _Read()
        self.cleanup = {}
        self.cleaning = False

    def own(self, handle):
        self.handles.append(handle)
        return handle

    def close(self, handle):
        self.api.close(handle)
        self.handles.remove(handle)
        for name in ("job", "process", "thread", "pipe"):
            if getattr(self, name) == handle:
                setattr(self, name, None)
        if self.read.overlap.event == handle:
            self.read.overlap.event = None

    def check_budget(self):
        if self.cancel.is_set():
            raise _Cancelled()
        if self.clock() >= self.deadline:
            raise subprocess.TimeoutExpired(self.args, self.timeout)

    def settle(self):
        """One shared cleanup deadline, including failure/unknown retention."""
        self.cleaning = True
        deadline = self.clock() + CLEANUP_SECONDS
        facts = self.cleanup = {
            "assigned": self.assigned, "membership_verified": self.verified,
            "termination_requested": False, "process_exited": self.process is None,
            "tree_empty": False, "io_finalized": not self.read.pending,
            "handles_closed": False, "errors": [],
        }

        def attempt(stage, function):
            try:
                return function()
            except BaseException as error:
                facts["errors"].append({"stage": stage, "winerror": getattr(error, "winerror", None)})
                return None

        if self.process:
            attempt("terminate", lambda: self.api.terminate(self))
            facts["termination_requested"] = not any(e["stage"] == "terminate" for e in facts["errors"])
        if self.read.pending:
            attempt("cancel_read", lambda: self.api.cancel_read(self))
        while True:
            facts["tree_empty"] = attempt("query_job", lambda: self.api.empty(self)) is True
            if self.process:
                facts["process_exited"] = attempt("wait_process", lambda: self.api.exit_code(self.process)) is not None
            if self.read.pending:
                attempt("finish_read", lambda: self.api.finish(self))
            facts["io_finalized"] = not self.read.pending
            if facts["tree_empty"] and facts["process_exited"] and facts["io_finalized"]:
                break
            remaining = deadline - self.clock()
            if remaining <= 0 or facts["errors"]:
                break
            # A process handle is signalled forever after exit; wait on the I/O
            # event then, or sleep briefly for Job accounting, not a busy loop.
            if self.read.pending:
                attempt("wait_io", lambda: self.api.wait(self.read.overlap.event, remaining))
            elif not facts["process_exited"]:
                attempt("wait_process", lambda: self.api.wait(self.process, remaining))
            else:
                self.sleep(min(0.01, remaining))
        if facts["tree_empty"] and facts["process_exited"] and facts["io_finalized"]:
            for handle in list(reversed(self.handles)):
                attempt("close_handle", lambda h=handle: self.close(h))
        facts["handles_closed"] = not self.handles
        if self.handles or self.read.pending:
            _PENDING[id(self)] = self
        else:
            _PENDING.pop(id(self), None)
        facts["owner_retained"] = id(self) in _PENDING
        return facts

    def run(self):
        data = bytearray()
        self.deadline = self.clock() + self.timeout
        self.api = self.api or _API()
        try:
            self.check_budget()
            self.api.start(self)
            eof = False
            while True:
                self.check_budget()
                block = self.api.finish(self) if self.read.pending else (b"" if eof else self.api.issue(self))
                if block is not None:
                    if block:
                        data.extend(block)
                    else:
                        eof = True
                code = self.api.exit_code(self.process)
                if eof and code is not None:
                    break
                if block is None or eof:
                    self.api.wait(self.process if eof else self.read.overlap.event,
                                  max(0, self.deadline - self.clock()))
            # Same locale/newline policy as Popen(text=True), no UTF-8 override.
            encoding = "utf-8" if sys.flags.utf8_mode else locale.getencoding()
            with io.TextIOWrapper(io.BytesIO(data), encoding=encoding) as text:
                output = text.read()
            if code:
                raise subprocess.CalledProcessError(code, self.args, output=output)
        except BaseException as error:
            facts = self.settle()
            error.cleanup = facts
            if not all(facts[key] for key in ("tree_empty", "process_exited", "io_finalized", "handles_closed")) or facts["errors"]:
                error.add_note("Windows shared Git cleanup reported an error or unresolved state; inspect cleanup facts")
            raise
        facts = self.settle()
        if facts["errors"] or not all(facts[key] for key in ("tree_empty", "process_exited", "io_finalized", "handles_closed")):
            raise _CleanupError(facts)
        return output


class _Worker:
    """Atomic start/revoke gate and acknowledgement independent of asyncio.

    Cancelling a to_thread Task can finish its asyncio adapter while its executor
    callback is still running. Only this acknowledgement says the owned operation
    returned (including bounded settlement), or was atomically prevented from
    starting. It neither retries the operation nor extends its 35+5 budgets.
    """
    def __init__(self, operation):
        self.operation = operation
        self.completion = Future()

    def run(self):
        if not self.completion.set_running_or_notify_cancel():
            return  # Revoked before start; even a late executor callback is safe.
        try:
            result = self.operation.run()
        except BaseException as error:
            self.completion.set_exception(error)
        else:
            self.completion.set_result(result)


async def check_output(args, *, timeout=35):
    """Cancellation waits for worker acknowledgement, never adapter completion."""
    operation = _Operation(args, timeout)
    worker = _Worker(operation)
    task = asyncio.create_task(asyncio.to_thread(worker.run))
    try:
        await asyncio.shield(task)
        return worker.completion.result()
    except asyncio.CancelledError as cancelled:
        operation.cancel.set()
        if worker.completion.cancel():
            # Atomic proof that operation.run cannot begin, not an inference
            # from Task.done()/cancelled() or the executor's queue state.
            task.cancel()
            operation.cleanup = {
                "worker_never_started": True, "assigned": False,
                "membership_verified": False, "termination_requested": False,
                "process_exited": True, "tree_empty": True, "io_finalized": True,
                "handles_closed": True, "owner_retained": False, "errors": [],
            }
        else:
            # The running callback owns its unchanged operation+cleanup budgets.
            # This is a Future, not a Task caught by loop-wide task cancellation.
            # Shield prevents repeated caller cancellation from revoking the
            # acknowledgement. Do not wait on/retry the cancelled adapter.
            completion = asyncio.wrap_future(worker.completion)
            while not completion.done():
                try:
                    await asyncio.shield(completion)
                except asyncio.CancelledError:
                    operation.cancel.set()
                except BaseException:
                    break  # Worker returned a failure; result consumed below.
            try:
                completion.result()
            except BaseException:
                pass  # Preserve the exact original caller CancelledError.
        cancelled.cleanup = operation.cleanup
        if not operation.cleanup or operation.cleanup.get("errors") or not operation.cleanup.get("handles_closed"):
            cancelled.add_note("Windows shared Git cancellation cleanup was not confirmed")
        raise
