"""Portable, process-exclusive checkpoints for shared root sessions.

This is a narrow POSIX/local-filesystem mechanism.  Applications decide when a
session is shared and which provider records belong in a checkpoint.
"""

from __future__ import annotations

import copy
import errno
import getpass
import hashlib
import json
import os
import re
import socket
import stat
import tempfile
import threading
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, cast

try:  # Fail loudly on platforms without the locking primitive.
    import fcntl
except ImportError:  # pragma: no cover - non-POSIX platform
    fcntl = None  # type: ignore[assignment]


_VERSION = 1
_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9-]{0,127}$")
_MAX_CHECKPOINT_BYTES = 16 * 1024 * 1024
_PROCESS_LOCKS: set[str] = set()
_PROCESS_LOCKS_GUARD = threading.RLock()
_LOCK_FDS: set[int] = set()


@dataclass(frozen=True)
class FileStamp:
    """Metadata that identifies a checkpoint without reading its content."""

    dev: int
    ino: int
    size: int
    mtime_ns: int
    ctime_ns: int


def file_stamp(path: Path) -> FileStamp | None:
    """Return metadata for *path*, or ``None`` only when it does not exist."""

    try:
        result = path.stat()
    except FileNotFoundError:
        return None
    except OSError as exc:
        if exc.errno == errno.ENOENT:
            return None
        raise
    return FileStamp(
        dev=result.st_dev,
        ino=result.st_ino,
        size=result.st_size,
        mtime_ns=result.st_mtime_ns,
        ctime_ns=result.st_ctime_ns,
    )


class SessionBusyError(RuntimeError):
    """Raised when another process holds a session's stable OS lock."""

    def __init__(self, owner: dict[str, Any] | None = None) -> None:
        self.owner = owner
        super().__init__("shared session is busy")


class SharedStateError(RuntimeError):
    """Raised when shared-state storage or a checkpoint is invalid."""


class SessionTransferFencedError(RuntimeError):
    """A durable transfer marker prevents ordinary session execution."""

    def __init__(self, fence: dict[str, Any]) -> None:
        self.fence = copy.deepcopy(fence)
        super().__init__("session execution is fenced by a durable transfer")


def _default_root() -> Path:
    configured = os.environ.get("AMPLIFIER_SESSION_STATE_HOME")
    if configured:
        return Path(configured).expanduser()
    state_home = os.environ.get("XDG_STATE_HOME")
    if state_home:
        return Path(state_home).expanduser() / "amplifier" / "sessions"
    return Path.home() / ".local" / "state" / "amplifier" / "sessions"


def _canonical_workspace(workspace: str | os.PathLike[str]) -> Path:
    try:
        canonical = Path(workspace).expanduser().resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise ValueError("workspace must be an existing resolvable directory") from exc
    if not canonical.is_dir():
        raise ValueError("workspace must be a directory")
    return canonical


def _session_id(session_id: str) -> str:
    if not isinstance(session_id, str) or not _ID_RE.fullmatch(session_id):
        raise ValueError("session_id must be 1-128 ASCII letters, digits, or hyphens")
    return session_id


def _workspace_key(workspace: Path) -> str:
    return hashlib.sha256(str(workspace).encode("utf-8")).hexdigest()


def _ensure_supported() -> None:
    if os.name != "posix" or fcntl is None:
        raise RuntimeError("SharedSessionStore writing requires a POSIX local filesystem")


def _validate_private_directory(path: Path, *, create: bool) -> None:
    """Create or validate one state-owned directory without following a link."""

    try:
        result = path.lstat()
    except FileNotFoundError:
        if not create:
            return
        try:
            path.mkdir(mode=0o700, parents=True)
        except FileExistsError:
            pass
        result = path.lstat()
    except OSError as exc:
        raise SharedStateError(f"cannot inspect state directory {path.name}") from exc

    if stat.S_ISLNK(result.st_mode) or not stat.S_ISDIR(result.st_mode):
        raise SharedStateError(f"state directory {path.name} is not a safe directory")
    if result.st_uid != os.getuid():
        raise SharedStateError(f"state directory {path.name} is not owned by this user")
    if stat.S_IMODE(result.st_mode) & 0o077:
        raise SharedStateError(f"state directory {path.name} has unsafe permissions")


def _validate_private_file(path: Path) -> None:
    """Reject an existing state file that is not private regular data."""

    try:
        result = path.lstat()
    except FileNotFoundError:
        return
    except OSError as exc:
        raise SharedStateError(f"cannot inspect state file {path.name}") from exc
    if stat.S_ISLNK(result.st_mode) or not stat.S_ISREG(result.st_mode):
        raise SharedStateError(f"state file {path.name} is not a safe regular file")
    if result.st_uid != os.getuid():
        raise SharedStateError(f"state file {path.name} is not owned by this user")
    if stat.S_IMODE(result.st_mode) & 0o077:
        raise SharedStateError(f"state file {path.name} has unsafe permissions")


def _mkdir_private_durable(path: Path) -> None:
    """Create at most 128 private ancestors, persisting each new directory entry.

    A marker's own parent fsync cannot make a newly created ancestor durable.
    Build from the existing ancestor outward and sync each containing directory
    before publishing any descendant. Existing directory modes are untouched.
    """
    missing = []
    current = path
    while True:
        try:
            current.lstat()
        except FileNotFoundError:
            if len(missing) >= 128 or current == current.parent:
                raise SharedStateError("native transfer directory exceeds the creation depth limit") from None
            missing.append(current)
            current = current.parent
        else:
            if not current.is_dir():
                raise SharedStateError("native transfer ancestor is not a directory")
            break
    for directory in reversed(missing):
        try:
            directory.mkdir(mode=0o700)
        except FileExistsError:
            pass
        _validate_private_directory(directory, create=False)
        _fsync_directory(directory.parent)


def _ensure_private_state_path(root: Path, directory: Path) -> None:
    """Create the state-root chain, validating each state-owned component."""

    _validate_private_directory(root, create=True)
    _validate_private_directory(root / "v1", create=True)
    _validate_private_directory(root / "v1" / directory.parent.name, create=True)
    _validate_private_directory(directory, create=True)
    for name in ("session.lock", "owner.json", "checkpoint.json"):
        _validate_private_file(directory / name)


def _open_nofollow(path: Path, flags: int, mode: int = 0o600) -> int:
    nofollow = getattr(os, "O_NOFOLLOW", 0)
    return os.open(path, flags | nofollow, mode)


def _validate_json(value: Any, *, label: str) -> None:
    try:
        json.dumps(value, ensure_ascii=False, allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{label} must be JSON serializable") from exc


def _validate_metadata(value: Any) -> dict[str, Any]:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ValueError("metadata must be a JSON object")
    forbidden = {"credential", "credentials", "secret", "secrets", "token", "password", "api_key", "apikey", "env", "environment"}

    def visit(item: Any) -> None:
        if isinstance(item, dict):
            for key, nested in item.items():
                if not isinstance(key, str):
                    raise ValueError("metadata keys must be strings")
                if key.lower() in forbidden:
                    raise ValueError("metadata must not contain credentials")
                visit(nested)
        elif isinstance(item, list):
            for nested in item:
                visit(nested)

    visit(value)
    _validate_json(value, label="metadata")
    return copy.deepcopy(value)


def _read_json(path: Path, *, missing_ok: bool = False) -> dict[str, Any] | None:
    try:
        fd = _open_nofollow(path, os.O_RDONLY)
    except FileNotFoundError:
        if missing_ok:
            return None
        raise
    except OSError as exc:
        if missing_ok and exc.errno == errno.ENOENT:
            return None
        raise SharedStateError(f"cannot safely read {path.name}") from exc
    try:
        with os.fdopen(fd, "rb") as handle:
            raw = handle.read(_MAX_CHECKPOINT_BYTES + 1)
    except OSError as exc:
        raise SharedStateError(f"cannot read {path.name}") from exc
    if len(raw) > _MAX_CHECKPOINT_BYTES:
        raise SharedStateError(f"{path.name} exceeds the size limit")
    try:
        value = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise SharedStateError(f"{path.name} is not valid JSON") from exc
    if not isinstance(value, dict):
        raise SharedStateError(f"{path.name} must contain a JSON object")
    return value


def _atomic_json(path: Path, value: dict[str, Any]) -> None:
    encoded = json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode("utf-8")
    if len(encoded) > _MAX_CHECKPOINT_BYTES:
        raise ValueError("checkpoint exceeds the size limit")
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "wb") as handle:
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        _fsync_directory(path.parent)
    except BaseException:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
        raise


def _fsync_directory(directory: Path) -> None:
    """Persist a directory entry where the local POSIX filesystem supports it."""

    try:
        directory_fd = os.open(directory, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(directory_fd)
    except OSError:
        pass  # Some POSIX filesystems do not support directory fsync.
    finally:
        os.close(directory_fd)


def _process_start_identity(path: Path = Path("/proc/self/stat")) -> str | None:
    """Read Linux procfs field 22, handling process names containing spaces."""

    try:
        raw = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        return None
    closing_paren = raw.rfind(")")
    if closing_paren < 0:
        return None
    fields = raw[closing_paren + 1 :].split()
    return fields[19] if len(fields) > 19 else None


def _owner_details(app: str, diagnostics: dict[str, Any], workspace: Path, session_id: str, root: Path) -> dict[str, Any]:
    if not isinstance(app, str) or not app or len(app) > 256:
        raise ValueError("app must be a non-empty string up to 256 characters")
    forbidden = ("argv", "env", "secret", "token", "credential", "prompt", "password")
    for key, value in diagnostics.items():
        if not isinstance(key, str) or not key or any(word in key.lower() for word in forbidden):
            raise ValueError("diagnostic name is unsafe")
        if not isinstance(value, (str, int, float, bool, type(None))) or (isinstance(value, str) and len(value) > 1024):
            raise ValueError("diagnostics must be bounded JSON scalar values")
    try:
        user = getpass.getuser()
    except (KeyError, OSError, ImportError):
        user = str(os.getuid())
    owner: dict[str, Any] = {
        "app": app,
        "hostname": socket.gethostname(),
        "user": user,
        "pid": os.getpid(),
        "process_start_identity": _process_start_identity(),
        "acquired_at": datetime.now(timezone.utc).isoformat(),
        "workspace": str(workspace),
        "session_id": session_id,
        "active": True,
        "acquisition_id": uuid.uuid4().hex,
        "coordination_root": str(root.resolve()),
    }
    for key, value in diagnostics.items():
        if key in owner:
            if type(value) is not type(owner[key]) or value != owner[key]:
                raise ValueError(f"diagnostic {key!r} cannot override library-owned state")
            continue
        owner[key] = value
    return owner


def _advisory_owner(path: Path) -> dict[str, Any] | None:
    try:
        value = _read_json(path, missing_ok=True)
    except (OSError, SharedStateError):
        return None
    if value is None or value.get("active") is not True:
        return None
    required = {"app", "hostname", "user", "pid", "acquired_at", "workspace", "session_id"}
    if not required.issubset(value) or not isinstance(value.get("pid"), int):
        return None
    return value


def _close_inherited_locks() -> None:
    global _LOCK_FDS, _PROCESS_LOCKS, _PROCESS_LOCKS_GUARD
    for fd in _LOCK_FDS:
        try:
            os.close(fd)
        except OSError:
            pass
    _LOCK_FDS = set()
    _PROCESS_LOCKS = set()
    _PROCESS_LOCKS_GUARD = threading.RLock()


def _before_fork() -> None:
    _PROCESS_LOCKS_GUARD.acquire()


def _after_parent_fork() -> None:
    _PROCESS_LOCKS_GUARD.release()


if hasattr(os, "register_at_fork"):
    os.register_at_fork(before=_before_fork, after_in_parent=_after_parent_fork, after_in_child=_close_inherited_locks)


class SharedSessionStore:
    """Address and coordinate one portable checkpoint for a workspace/session ID."""

    def __init__(self, workspace: str | os.PathLike[str], session_id: str, *, root: str | os.PathLike[str] | None = None) -> None:
        self.workspace = _canonical_workspace(workspace)
        self.session_id = _session_id(session_id)
        self.root = Path(root).expanduser() if root is not None else _default_root()
        self._directory = self.root / "v1" / _workspace_key(self.workspace) / self.session_id
        home = Path(os.environ.get("AMPLIFIER_HOME") or Path.home() / ".amplifier").expanduser().absolute()
        slug = str(self.workspace).replace("/", "-").replace("\\", "-").replace(":", "")
        self._native_root = home
        self._native_directory = home / "projects" / (slug if slug.startswith("-") else "-" + slug) / "sessions" / self.session_id

    @property
    def transfer_fence_path(self) -> Path:
        """Native-history marker; reading this property creates no files."""
        return self._native_directory / "transfer-fence.json"

    def _transfer_directory(self, *, create: bool = False) -> None:
        # Native histories predate private coordination roots, so do not change
        # existing directory modes. Reject links, non-directories and foreign
        # owners; the marker itself must always be private regular data.
        relative = self._native_directory.relative_to(self._native_root)
        directories = [self._native_root]
        for part in relative.parts:
            directories.append(directories[-1] / part)
        for directory in directories:
            try:
                info = directory.lstat()
            except FileNotFoundError:
                if not create:
                    return
                _mkdir_private_durable(directory)
                info = directory.lstat()
            if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid():
                raise SharedStateError("native session transfer directory is unsafe")
        _validate_private_file(self.transfer_fence_path)

    def transfer_fence(self) -> dict[str, Any] | None:
        """Inspect durable transfer state without acquiring or starting work."""
        self._transfer_directory()
        value = _read_json(self.transfer_fence_path, missing_ok=True)
        if value is None:
            return None
        required = {"version", "session_id", "transfer_id", "destination_host", "role", "phase"}
        if (set(value) != required or type(value["version"]) is not int or value["version"] != 1
                or value["session_id"] != self.session_id
                or not isinstance(value["transfer_id"], str) or not _ID_RE.fullmatch(value["transfer_id"])
                or not isinstance(value["destination_host"], str) or not 1 <= len(value["destination_host"]) <= 255
                or any(ord(char) < 32 for char in value["destination_host"])
                or not isinstance(value["role"], str) or value["role"] not in {"source", "destination"}
                or not isinstance(value["phase"], str) or value["phase"] not in {"staged", "committed"}
                or (value["role"] == "destination" and value["phase"] != "staged")):
            raise SharedStateError("invalid session transfer fence")
        return value

    @property
    def checkpoint_path(self) -> Path:
        """The authoritative checkpoint path; obtaining it never creates directories."""
        return self._directory / "checkpoint.json"

    @property
    def _lock_path(self) -> Path:
        return self._directory / "session.lock"

    @property
    def _owner_path(self) -> Path:
        return self._directory / "owner.json"

    def stamp(self) -> FileStamp | None:
        """Return checkpoint metadata only; this does not parse JSON or create paths."""
        return file_stamp(self.checkpoint_path)

    def read(self) -> dict[str, Any] | None:
        """Read and validate a complete atomic checkpoint without taking ownership."""
        value = _read_json(self.checkpoint_path, missing_ok=True)
        if value is None:
            return None
        required = {"version", "workspace", "session_id", "bundle", "messages", "metadata"}
        if set(value) != required or value["version"] != _VERSION:
            raise SharedStateError("checkpoint has an unsupported schema")
        if value["workspace"] != str(self.workspace) or value["session_id"] != self.session_id:
            raise SharedStateError("checkpoint identity does not match this store")
        if not isinstance(value["bundle"], str) or not value["bundle"]:
            raise SharedStateError("checkpoint bundle must be a non-empty string")
        if not isinstance(value["messages"], list) or not all(isinstance(message, dict) for message in value["messages"]):
            raise SharedStateError("checkpoint messages must be a list of objects")
        try:
            value["metadata"] = _validate_metadata(value["metadata"])
        except ValueError as exc:
            raise SharedStateError(str(exc)) from exc
        return copy.deepcopy(value)

    def acquire(self, *, app: str, **diagnostics: Any) -> "HeldSession":
        """Acquire the stable OS lock and publish advisory owner diagnostics."""
        return self._acquire(app=app, diagnostics=diagnostics)

    def acquire_transfer(self, transfer_id: str, *, app: str, **diagnostics: Any) -> "HeldTransfer":
        """Acquire only a matching staged fence for explicit adapter recovery.

        This does not grant execution or validate remote release evidence. The
        application must authenticate the transfer before clearing/committing.
        Committed source markers cannot be acquired through this API.
        """
        _session_id(transfer_id)
        return cast("HeldTransfer", self._acquire(app=app, diagnostics=diagnostics, transfer_id=transfer_id))

    def _acquire(self, *, app: str, diagnostics: dict[str, Any], transfer_id: str | None = None) -> "HeldSession":
        _ensure_supported()
        owner = _owner_details(app, diagnostics, self.workspace, self.session_id, self.root)
        _ensure_private_state_path(self.root, self._directory)
        lock_name = str(self._lock_path)
        with _PROCESS_LOCKS_GUARD:
            if lock_name in _PROCESS_LOCKS:
                raise SessionBusyError(_advisory_owner(self._owner_path))
            try:
                fd = _open_nofollow(self._lock_path, os.O_RDWR | os.O_CREAT, 0o600)
                os.set_inheritable(fd, False)
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                try:
                    os.close(fd)
                except UnboundLocalError:
                    pass
                raise SessionBusyError(_advisory_owner(self._owner_path)) from None
            except OSError as exc:
                try:
                    os.close(fd)
                except UnboundLocalError:
                    pass
                raise SharedStateError("cannot acquire stable session lock") from exc
            try:
                fence = self.transfer_fence()
                if transfer_id is None:
                    if fence is not None:
                        raise SessionTransferFencedError(fence)
                elif fence is None or fence["transfer_id"] != transfer_id or fence["phase"] != "staged":
                    raise SharedStateError("transfer recovery requires the exact staged fence")
                _atomic_json(self._owner_path, owner)
            except BaseException:
                fcntl.flock(fd, fcntl.LOCK_UN)
                os.close(fd)
                raise
            _PROCESS_LOCKS.add(lock_name)
            _LOCK_FDS.add(fd)
        if transfer_id is not None:
            return HeldTransfer(self, fd, owner, transfer_id)
        return HeldSession(self, fd, owner)

    @classmethod
    def list_ids(cls, workspace: str | os.PathLike[str], *, root: str | os.PathLike[str] | None = None) -> list[str]:
        """List checkpoint IDs for an existing workspace without creating state paths."""
        canonical = _canonical_workspace(workspace)
        root_path = Path(root).expanduser() if root is not None else _default_root()
        directory = root_path / "v1" / _workspace_key(canonical)
        try:
            entries = list(directory.iterdir())
        except FileNotFoundError:
            return []
        except OSError as exc:
            if exc.errno == errno.ENOENT:
                return []
            raise
        return sorted(entry.name for entry in entries if entry.is_dir() and _ID_RE.fullmatch(entry.name) and (entry / "checkpoint.json").is_file())


class HeldSession:
    """A non-copyable, PID-bound capability to write one shared checkpoint."""

    def __init__(self, store: SharedSessionStore, fd: int, owner: dict[str, Any]) -> None:
        self._store = store
        self._fd = fd
        self._owner = owner
        self._pid = os.getpid()
        self._active = True
        self._mutex = threading.RLock()
        self._release_registration = None
        self._release_requests: dict[str, Any] = {}

    @property
    def owner(self) -> dict[str, Any]:
        """A detached view of this acquisition's advisory identity."""
        return copy.deepcopy(self._owner)

    def __copy__(self) -> "HeldSession":
        raise TypeError("HeldSession capabilities cannot be copied")

    def __deepcopy__(self, memo: dict[int, Any]) -> "HeldSession":
        raise TypeError("HeldSession capabilities cannot be copied")

    def __reduce__(self) -> None:
        raise TypeError("HeldSession capabilities cannot be serialized")

    @property
    def active(self) -> bool:
        return self._active and self._pid == os.getpid()

    def check(self) -> None:
        self._check_active()
        fence = self._store.transfer_fence()
        if fence is not None:
            raise SessionTransferFencedError(fence)

    def _check_active(self) -> None:
        if self._pid != os.getpid():
            raise RuntimeError("HeldSession belongs to a different process")
        if not self._active:
            raise RuntimeError("HeldSession is no longer active")

    def fence_transfer(self, transfer_id: str, destination_host: str, *, role: str = "source") -> dict[str, Any]:
        """Fence this saved, quiescent writer before releasing it for transfer.

        The host must settle work and persist native history first. The marker
        does not stop a runtime or prove any remote outcome. Once written this
        handle cannot authorize further execution or checkpoint writes.
        """
        _session_id(transfer_id)
        if (not isinstance(destination_host, str) or not 1 <= len(destination_host) <= 255
                or any(ord(char) < 32 for char in destination_host) or role not in {"source", "destination"}):
            raise ValueError("invalid transfer destination or role")
        record = {"version": 1, "session_id": self._store.session_id, "transfer_id": transfer_id,
                  "destination_host": destination_host, "role": role, "phase": "staged"}
        with self._mutex:
            self._check_active()
            existing = self._store.transfer_fence()
            if existing is not None:
                if existing == record:
                    return existing
                raise SharedStateError("a different transfer fence already exists")
            self._store._transfer_directory(create=True)
            _atomic_json(self._store.transfer_fence_path, record)
            return copy.deepcopy(record)

    def read(self) -> dict[str, Any] | None:
        self.check()
        return self._store.read()

    def stamp(self) -> FileStamp | None:
        self.check()
        return self._store.stamp()

    def write(self, messages: list[dict[str, Any]], *, bundle: str, metadata: dict[str, Any] | None = None) -> FileStamp:
        """Atomically replace the checkpoint while this handle retains the OS lock."""
        self.check()
        if not isinstance(bundle, str) or not bundle:
            raise ValueError("bundle must be a non-empty string")
        bundle = bundle.removeprefix("bundle:")
        if not bundle:
            raise ValueError("bundle must not be the legacy prefix alone")
        if not isinstance(messages, list) or not all(isinstance(message, dict) for message in messages):
            raise ValueError("messages must be a list of objects")
        _validate_json(messages, label="messages")
        checkpoint = {
            "version": _VERSION,
            "workspace": str(self._store.workspace),
            "session_id": self._store.session_id,
            "bundle": bundle,
            "messages": copy.deepcopy(messages),
            "metadata": _validate_metadata(metadata),
        }
        with self._mutex:
            self.check()
            _atomic_json(self._store.checkpoint_path, checkpoint)
            result = self._store.stamp()
            if result is None:  # pragma: no cover - replace succeeded but file vanished externally
                raise SharedStateError("checkpoint disappeared immediately after write")
            return result

    def delete_checkpoint(self) -> None:
        """Delete only the checkpoint while retaining this session's stable lock."""

        self.check()
        with self._mutex:
            self.check()
            _validate_private_file(self._store.checkpoint_path)
            try:
                os.unlink(self._store.checkpoint_path)
            except FileNotFoundError:
                return
            except OSError as exc:
                raise SharedStateError("cannot delete checkpoint") from exc
            _fsync_directory(self._store.checkpoint_path.parent)

    def release(self) -> None:
        """Release once.  It changes only advisory owner metadata, never a checkpoint."""
        if self._pid != os.getpid():
            self._active = False
            return
        with self._mutex:
            if not self._active:
                return
            if self._release_registration is not None:
                self._release_registration._deactivate()
            self._active = False
            released = dict(self._owner)
            released["active"] = False
            released["released_at"] = datetime.now(timezone.utc).isoformat()
            try:
                _atomic_json(self._store._owner_path, released)
            finally:
                lock_name = str(self._store._lock_path)
                try:
                    fcntl.flock(self._fd, fcntl.LOCK_UN)
                finally:
                    with _PROCESS_LOCKS_GUARD:
                        _PROCESS_LOCKS.discard(lock_name)
                        _LOCK_FDS.discard(self._fd)
                    os.close(self._fd)


class HeldTransfer(HeldSession):
    """A receipt-matched lock for resolving a staged fence, never execution."""

    def __init__(self, store: SharedSessionStore, fd: int, owner: dict[str, Any], transfer_id: str) -> None:
        super().__init__(store, fd, owner)
        self._transfer_id = transfer_id

    def check(self) -> None:
        self._check_active()
        raise SharedStateError("transfer recovery capability cannot authorize execution")

    def fence_transfer(self, transfer_id: str, destination_host: str, *, role: str = "source") -> dict[str, Any]:
        raise SharedStateError("transfer recovery capability cannot replace a fence")

    def _staged(self) -> dict[str, Any]:
        self._check_active()
        record = self._store.transfer_fence()
        if record is None or record["transfer_id"] != self._transfer_id or record["phase"] != "staged":
            raise SharedStateError("transfer recovery requires the exact staged fence")
        return record

    def commit_transfer(self) -> dict[str, Any]:
        """Permanently fence the source after the host verifies its transfer."""
        with self._mutex:
            record = self._staged()
            if record["role"] != "source":
                raise SharedStateError("only a source transfer fence can be committed")
            record["phase"] = "committed"
            _atomic_json(self._store.transfer_fence_path, record)
            return record

    def clear_transfer(self) -> None:
        """Explicitly cancel a staged source or activate a staged destination.

        Authenticate the corresponding cancellation or release receipt before
        calling. Release this capability and acquire normally before execution.
        """
        with self._mutex:
            self._staged()
            self._store.transfer_fence_path.unlink()
            _fsync_directory(self._store.transfer_fence_path.parent)
