"""Cooperative, same-user release requests for existing local session locks.

Hosts supply shutdown policy and persistence. A successful callback asserts
that all writers have stopped; only then does this module release the handle.
Control records are ephemeral and never contain conversation state.
"""

from __future__ import annotations

import asyncio
import ctypes
import inspect
import json
import math
import os
from pathlib import Path
import socket
import stat
import struct
import tempfile
import time
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable
import uuid

from .shared_state import (
    HeldSession,
    SharedSessionStore,
    SharedStateError,
    _atomic_json,
    _validate_private_directory,
)

_VERSION = 1
_MAX_FRAME = 8192
_MAX_REQUESTS = 128
_REGISTRATIONS: set[ReleaseRegistration] = set()


def _label(value: Any, limit: int = 256) -> str:
    if not isinstance(value, str) or not value or len(value) > limit:
        raise ValueError("invalid handoff text")
    if any(not c.isprintable() for c in value):
        raise ValueError("handoff text contains control characters")
    return value


def _peer_uid(sock: Any) -> int:
    if hasattr(socket, "SO_PEERCRED"):
        return struct.unpack(
            "3i", sock.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12)
        )[1]
    # Python does not expose getpeereid on every BSD/macOS build.
    libc = ctypes.CDLL(None, use_errno=True)
    function = getattr(libc, "getpeereid", None)
    if function is None:
        raise PermissionError("peer credential verification is unavailable")
    uid, gid = ctypes.c_uint(), ctypes.c_uint()
    if function(sock.fileno(), ctypes.byref(uid), ctypes.byref(gid)) != 0:
        raise PermissionError("cannot verify local peer")
    return uid.value


def _check_peer(writer: asyncio.StreamWriter) -> None:
    if _peer_uid(writer.get_extra_info("socket")) != os.getuid():
        raise PermissionError("release requests require the same operating-system user")


async def _write(writer: asyncio.StreamWriter, value: dict) -> None:
    writer.write(json.dumps(value, allow_nan=False).encode() + b"\n")
    await writer.drain()


async def _read(reader: asyncio.StreamReader) -> dict:
    raw = await reader.readline()
    if not raw or len(raw) > _MAX_FRAME:
        raise ValueError("invalid handoff frame")
    value = json.loads(raw)
    if not isinstance(value, dict):
        raise ValueError("invalid handoff frame")
    return value


@dataclass(frozen=True)
class ReadyToRelease:
    """Host assertion: saved state is resumable and all old writers are stopped."""


@dataclass(frozen=True)
class CannotRelease:
    code: str
    message: str


@dataclass(frozen=True)
class ReleaseResult:
    status: str
    message: str = ""
    code: str = ""


@dataclass
class ReleaseRequest:
    request_id: str
    requester_app: str
    acquisition_id: str
    peer_uid: int
    deadline: float
    _notify: Callable[[str], None] = field(repr=False)

    def report_progress(self, stage: str) -> None:
        if stage not in {"draining", "persisting"}:
            raise ValueError("invalid release stage")
        self._notify(stage)


class ReleaseRegistration:
    """An acquisition-scoped endpoint. Requester cancellation never cancels drain."""

    def __init__(
        self,
        held: HeldSession,
        prepare_release: Callable[
            [ReleaseRequest], Awaitable[ReadyToRelease | CannotRelease]
        ],
        directory: Path,
    ):
        self.held = held
        self.prepare_release = prepare_release
        self.registration_id = uuid.uuid4().hex
        self.directory = directory
        self.path = directory / "control.sock"
        self.server: asyncio.Server | None = None
        self.listener: socket.socket | None = None
        self.active = True
        self.pid = os.getpid()
        self.loop = asyncio.get_running_loop()
        self.records: dict[str, dict] = held._release_requests
        self.pending: asyncio.Task | None = None
        self.connections = 0

    def _deactivate(self) -> None:
        # Called synchronously before the OS lock is released. Existing reply
        # streams may finish; no new request may reach the host callback.
        self.active = False
        if self.pid != os.getpid():
            # A fork shares the parent's kernel selector. Server.close() would
            # unregister its reader there and, on Python 3.13+, unlink its Unix
            # endpoint. Close only our copy of the underlying descriptor instead.
            if self.listener:
                self.listener.close()
            self.server = None
        elif self.server:
            if self.loop.is_closed():
                self.server.close()
            else:
                self.loop.call_soon_threadsafe(self.server.close)
        elif self.listener:
            self.listener.close()
        _REGISTRATIONS.discard(self)
        if os.getpid() == self.pid:
            self.path.unlink(missing_ok=True)
            try:
                self.directory.rmdir()
            except OSError:
                pass

    async def close(self) -> None:
        """Withdraw support without releasing ownership; await an active drain."""
        self._deactivate()
        if self.pending and not self.pending.done():
            await asyncio.shield(self.pending)
        with self.held._mutex:
            if self.held._release_registration is self:
                if self.held.active:
                    owner = self.held.owner
                    owner.pop("handoff", None)
                    _atomic_json(self.held._store._owner_path, owner)
                    self.held._owner = owner
                self.held._release_registration = None
            self._deactivate()
        if self.server:
            await self.server.wait_closed()

    @staticmethod
    def _publish(record: dict, message: dict) -> None:
        record["last"] = message
        for queue in record["listeners"]:
            if queue.full():
                queue.get_nowait()
            queue.put_nowait(message)

    async def _prepare(self, request: ReleaseRequest, record: dict) -> None:
        try:
            if time.monotonic() >= request.deadline:
                result = ReleaseResult(
                    "timed_out", "Release did not start before the deadline."
                )
            elif not self.active or not self.held.active:
                result = ReleaseResult("owner_changed")
            else:
                outcome = await self.prepare_release(request)
                if isinstance(outcome, CannotRelease):
                    result = ReleaseResult(
                        "cannot_release",
                        _label(outcome.message, 1024),
                        _label(outcome.code, 128),
                    )
                elif not isinstance(outcome, ReadyToRelease):
                    result = ReleaseResult(
                        "cannot_release", "The owner did not confirm safe shutdown."
                    )
                else:
                    try:
                        self.held.release()
                        result = ReleaseResult("released")
                    except Exception:
                        # release() can unlock despite a metadata write error.
                        result = ReleaseResult(
                            "released" if not self.held.active else "cannot_release",
                            "Owner metadata could not be updated; acquisition determines ownership.",
                        )
        except BaseException as exc:
            # Handler cancellation must not turn into an implicit unlock.
            result = ReleaseResult(
                "cannot_release", f"Owner preparation failed ({type(exc).__name__})."
            )
        self._publish(
            record,
            {
                "status": result.status,
                "message": result.message,
                "code": result.code,
                "terminal": True,
            },
        )

    async def _serve(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        self.connections += 1
        record, queue = None, None
        try:
            if self.connections > 32:
                await _write(writer, {"status": "unreachable", "terminal": True})
                return
            _check_peer(writer)
            data = await asyncio.wait_for(_read(reader), 5)
            if data.get("version") != _VERSION:
                await _write(writer, {"status": "unsupported", "terminal": True})
                return
            identity = self.held.owner
            target = {
                k: identity[k]
                for k in (
                    "workspace",
                    "session_id",
                    "acquisition_id",
                    "coordination_root",
                )
            }
            target["registration_id"] = self.registration_id
            if not self.active or not self.held.active or data.get("target") != target:
                await _write(writer, {"status": "owner_changed", "terminal": True})
                return
            request_id = _label(data.get("request_id"), 128)
            source = _label(data.get("requester_app"))
            # The deadline may change on a retry; the original request retains
            # its original admission deadline. Content and target may not change.
            fingerprint = (source, json.dumps(target, sort_keys=True))
            record = self.records.get(request_id)
            if record and record["fingerprint"] != fingerprint:
                raise ValueError("request identity was reused with different content")
            if record is None:
                deadline = data.get("deadline")
                if (
                    isinstance(deadline, bool)
                    or not isinstance(deadline, (int, float))
                    or not math.isfinite(deadline)
                ):
                    raise ValueError("invalid deadline")
                if not time.monotonic() < deadline <= time.monotonic() + 300:
                    await _write(writer, {"status": "timed_out", "terminal": True})
                    return
                if self.pending and not self.pending.done():
                    await _write(
                        writer, {"status": "handoff_in_progress", "terminal": True}
                    )
                    return
                if len(self.records) >= _MAX_REQUESTS:
                    await _write(
                        writer,
                        {
                            "status": "cannot_release",
                            "message": "Release request limit reached.",
                            "terminal": True,
                        },
                    )
                    return
                record = {
                    "fingerprint": fingerprint,
                    "listeners": set(),
                    "last": {"status": "received", "terminal": False},
                }
                self.records[request_id] = record
                request = ReleaseRequest(
                    request_id,
                    source,
                    identity["acquisition_id"],
                    os.getuid(),
                    deadline,
                    lambda stage: self._publish(
                        record, {"status": stage, "terminal": False}
                    ),
                )
                self.pending = asyncio.create_task(self._prepare(request, record))
            queue = asyncio.Queue(maxsize=8)
            record["listeners"].add(queue)
            queue.put_nowait(record["last"])
            # Bound a disconnected/stalled client's resources independently of
            # the host's safe shutdown, which may legitimately take longer.
            async with asyncio.timeout(300):
                while True:
                    message = await queue.get()
                    await _write(writer, message)
                    if message.get("terminal"):
                        break
        except PermissionError:
            try:
                await _write(writer, {"status": "unauthorized", "terminal": True})
            except OSError:
                pass
        except (ValueError, TypeError, KeyError, OverflowError):
            try:
                await _write(writer, {"status": "protocol_error", "terminal": True})
            except OSError:
                pass
        except (OSError, TimeoutError):
            pass
        finally:
            if record is not None and queue is not None:
                record["listeners"].discard(queue)
            self.connections -= 1
            writer.close()
            try:
                await writer.wait_closed()
            except OSError:
                pass


async def register_release_handler(
    held: HeldSession,
    *,
    prepare_release: Callable[
        [ReleaseRequest], Awaitable[ReadyToRelease | CannotRelease]
    ],
    runtime_dir: str | Path | None = None,
) -> ReleaseRegistration:
    """Advertise a ready same-user endpoint under a caller-selected private root."""
    held.check()
    root = (
        Path(runtime_dir).expanduser()
        if runtime_dir is not None
        else Path(tempfile.gettempdir()) / f"amplifier-handoff-{os.getuid()}"
    )
    _validate_private_directory(root, create=True)
    directory = Path(tempfile.mkdtemp(prefix="h-", dir=root)).resolve()
    registration = ReleaseRegistration(held, prepare_release, directory)
    try:
        if len(os.fsencode(registration.path)) > 103:
            raise ValueError(
                "control endpoint path is too long; supply a shorter private runtime_dir"
            )
        # Retain the actual socket for descriptor-only cleanup after fork. The
        # server still owns its normal lifecycle; no private asyncio API is used.
        registration.listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        registration.listener.bind(str(registration.path))
        registration.listener.setblocking(False)
        os.chmod(registration.path, 0o600)
        registration.server = await asyncio.start_unix_server(
            registration._serve,
            sock=registration.listener,
            limit=_MAX_FRAME,
            start_serving=False,
        )
        await registration.server.start_serving()
        with held._mutex:
            held.check()
            if held._release_registration is not None:
                raise ValueError("this acquisition already has a release handler")
            owner = held.owner
            owner["handoff"] = {
                "version": _VERSION,
                "transport": "unix",
                "endpoint": str(registration.path),
                "registration_id": registration.registration_id,
            }
            _atomic_json(held._store._owner_path, owner)
            held._owner = owner
            held._release_registration = registration
            _REGISTRATIONS.add(registration)
        return registration
    except BaseException:
        registration._deactivate()
        raise


async def request_release(
    store: SharedSessionStore,
    *,
    expected_owner: dict | None,
    request_id: str,
    requester_app: str,
    timeout: float = 30.0,
    on_progress: Callable[[str], Any] | None = None,
) -> ReleaseResult:
    """Ask one observed acquisition to release. This never acquires the lock."""
    _label(request_id, 128)
    _label(requester_app)
    if (
        isinstance(timeout, bool)
        or not math.isfinite(timeout)
        or not 0 < timeout <= 300
    ):
        raise ValueError("timeout must be positive and no more than 300 seconds")
    owner = expected_owner or {}
    descriptor = owner.get("handoff")
    if (
        not isinstance(descriptor, dict)
        or descriptor.get("version") != _VERSION
        or descriptor.get("transport") != "unix"
    ):
        return ReleaseResult(
            "unsupported", "The current owner does not support release requests."
        )
    if (
        owner.get("workspace") != str(store.workspace)
        or owner.get("session_id") != store.session_id
        or owner.get("coordination_root") != str(store.root.resolve())
    ):
        return ReleaseResult("owner_changed")
    writer = None
    try:
        endpoint = Path(descriptor["endpoint"])
        if not endpoint.is_absolute():
            raise ValueError("endpoint must be absolute")
        _validate_private_directory(endpoint.parent, create=False)
        info = endpoint.lstat()
        if (
            not stat.S_ISSOCK(info.st_mode)
            or info.st_uid != os.getuid()
            or stat.S_IMODE(info.st_mode) & 0o077
        ):
            raise PermissionError("unsafe control endpoint")
        deadline = time.monotonic() + timeout
        async with asyncio.timeout(timeout):
            reader, writer = await asyncio.open_unix_connection(
                str(endpoint), limit=_MAX_FRAME
            )
            _check_peer(writer)
            target = {
                k: owner[k]
                for k in (
                    "workspace",
                    "session_id",
                    "acquisition_id",
                    "coordination_root",
                )
            }
            target["registration_id"] = descriptor["registration_id"]
            await _write(
                writer,
                {
                    "version": _VERSION,
                    "target": target,
                    "request_id": request_id,
                    "requester_app": requester_app,
                    "deadline": deadline,
                },
            )
            while True:
                message = await _read(reader)
                status = _label(message.get("status"))
                if message.get("terminal") is True:
                    explanation = message.get("message", "")
                    if explanation:
                        explanation = _label(explanation, 1024)
                    code = message.get("code", "")
                    if code:
                        code = _label(code, 128)
                    return ReleaseResult(status, explanation, code)
                if on_progress:
                    result = on_progress(status)
                    if inspect.isawaitable(result):
                        await result
    except TimeoutError:
        return ReleaseResult(
            "timed_out", "Release may still finish; ownership has not been acquired."
        )
    except (PermissionError, SharedStateError):
        return ReleaseResult(
            "unauthorized", "The local endpoint could not be authenticated."
        )
    except OSError:
        return ReleaseResult(
            "unreachable", "The owner's control endpoint is unavailable."
        )
    except (ValueError, TypeError, KeyError):
        return ReleaseResult("protocol_error", "Invalid release protocol data.")
    finally:
        if writer:
            writer.close()
            try:
                await writer.wait_closed()
            except OSError:
                pass


def _after_fork() -> None:
    # Close only the child's copies; never unlink the parent's endpoint.
    for registration in tuple(_REGISTRATIONS):
        registration._deactivate()


if hasattr(os, "register_at_fork"):
    os.register_at_fork(after_in_child=_after_fork)
