"""Focused tests for portable shared root-session checkpoints."""

from __future__ import annotations

import copy
import json
import os
import pickle
import subprocess
import sys
import textwrap
import time
from pathlib import Path
from stat import S_IMODE

import pytest

import amplifier_foundation.session.shared_state as shared_state
from amplifier_foundation.session.shared_state import (
    FileStamp,
    HeldSession,
    SessionBusyError,
    SharedSessionStore,
    SharedStateError,
    file_stamp,
)


requires_posix = pytest.mark.skipif(
    os.name != "posix" or shared_state.fcntl is None,
    reason="shared-state acquisition requires POSIX flock",
)


def _store(tmp_path: Path, session_id: str = "root-1") -> SharedSessionStore:
    workspace = tmp_path / "workspace"
    workspace.mkdir(exist_ok=True)
    return SharedSessionStore(workspace, session_id, root=tmp_path / "state")


def _subprocess(script: str, *arguments: object) -> subprocess.Popen[str]:
    return subprocess.Popen(
        [sys.executable, "-c", textwrap.dedent(script), *(str(argument) for argument in arguments)],
        cwd=Path.cwd(),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )


def test_public_shape_and_paths_do_not_create_state(tmp_path: Path) -> None:
    store = _store(tmp_path)
    expected = store.root / "v1" / __import__("hashlib").sha256(str(store.workspace).encode()).hexdigest() / "root-1"
    assert store.checkpoint_path == expected / "checkpoint.json"
    assert store.stamp() is None
    assert store.read() is None
    assert not store.root.exists()
    assert SharedSessionStore.list_ids(store.workspace, root=store.root) == []


@requires_posix
def test_write_read_and_release_do_not_change_checkpoint(tmp_path: Path) -> None:
    store = _store(tmp_path)
    held = store.acquire(app="test-host", version="1", service="fixture")
    stamp = held.write([{"role": "user", "content": "hello"}], bundle="bundle:portable", metadata={"source": "test"})
    assert isinstance(stamp, FileStamp)
    assert held.read() == {
        "version": 1,
        "workspace": str(store.workspace),
        "session_id": "root-1",
        "bundle": "portable",
        "messages": [{"role": "user", "content": "hello"}],
        "metadata": {"source": "test"},
    }
    assert held.stamp() == stamp
    held.release()
    assert store.stamp() == stamp
    assert SharedSessionStore.list_ids(store.workspace, root=store.root) == ["root-1"]


@requires_posix
def test_contention_is_real_process_and_owner_is_advisory(tmp_path: Path) -> None:
    store = _store(tmp_path)
    held = store.acquire(app="first", version="1")
    process = _subprocess(
        """
        from pathlib import Path
        import sys
        from amplifier_foundation.session.shared_state import SessionBusyError, SharedSessionStore
        store = SharedSessionStore(Path(sys.argv[1]), "root-1", root=Path(sys.argv[2]))
        try:
            store.acquire(app="second")
        except SessionBusyError as error:
            print(error.owner["app"] if error.owner else "unknown")
        """,
        store.workspace,
        store.root,
    )
    stdout, stderr = process.communicate(timeout=5)
    try:
        assert process.returncode == 0, stderr
        assert stdout.strip() == "first"
    finally:
        held.release()


@requires_posix
def test_corrupt_owner_never_prevents_lock_recovery(tmp_path: Path) -> None:
    store = _store(tmp_path)
    first = store.acquire(app="first")
    store._owner_path.write_text("{broken", encoding="utf-8")
    with pytest.raises(SessionBusyError) as blocked:
        store.acquire(app="second")
    assert blocked.value.owner is None
    first.release()
    successor = store.acquire(app="successor")
    successor.release()


@requires_posix
def test_checkpoint_validation_and_reads_have_no_side_effects(tmp_path: Path) -> None:
    store = _store(tmp_path)
    held = store.acquire(app="test")
    held.write([], bundle="portable")
    held.release()
    checkpoint = store.checkpoint_path
    checkpoint.write_text("{broken", encoding="utf-8")
    original = checkpoint.read_bytes()
    with pytest.raises(SharedStateError, match="valid JSON"):
        store.read()
    assert checkpoint.read_bytes() == original


@requires_posix
def test_stamp_does_not_read_json_and_detects_same_size_replacement(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    store = _store(tmp_path)
    held = store.acquire(app="test")
    first = held.write([{"x": "a"}], bundle="portable")
    monkeypatch.setattr(shared_state, "_read_json", lambda *args, **kwargs: pytest.fail("stamp read JSON"))
    assert store.stamp() == first
    monkeypatch.undo()
    second = held.write([{"x": "b"}], bundle="portable")
    assert first.size == second.size
    assert first != second
    held.release()


@requires_posix
def test_held_capability_is_pid_bound_noncopyable_and_old_handle_cannot_write(tmp_path: Path) -> None:
    store = _store(tmp_path)
    first = store.acquire(app="first")
    for operation in (copy.copy, copy.deepcopy, pickle.dumps):
        with pytest.raises(TypeError):
            operation(first)
    first.release()
    successor = store.acquire(app="second")
    with pytest.raises(RuntimeError, match="no longer active"):
        first.write([], bundle="portable")
    successor.write([], bundle="portable")
    successor.release()


@pytest.mark.skipif(not hasattr(os, "fork"), reason="requires POSIX fork")
@requires_posix
def test_forked_child_cannot_use_or_retain_parent_lock(tmp_path: Path) -> None:
    store = _store(tmp_path)
    process = _subprocess(
        """
        from pathlib import Path
        import os, sys, time
        from amplifier_foundation.session.shared_state import SharedSessionStore
        store = SharedSessionStore(Path(sys.argv[1]), "root-1", root=Path(sys.argv[2]))
        held = store.acquire(app="parent")
        child = os.fork()
        if child:
            print(child, flush=True)
            os._exit(0)
        try:
            held.check()
        except RuntimeError:
            print("child-ready", flush=True)
        time.sleep(20)
        """,
        store.workspace,
        store.root,
    )
    assert process.stdout is not None
    child_pid = int(process.stdout.readline().strip())
    try:
        assert process.stdout.readline().strip() == "child-ready"
        process.wait(timeout=5)
        successor = store.acquire(app="successor")
        successor.release()
    finally:
        try:
            os.kill(child_pid, 9)
        except ProcessLookupError:
            pass


@requires_posix
def test_crash_and_exec_release_locks(tmp_path: Path) -> None:
    store = _store(tmp_path)
    crash = _subprocess(
        """
        from pathlib import Path
        import os, sys
        from amplifier_foundation.session.shared_state import SharedSessionStore
        SharedSessionStore(Path(sys.argv[1]), "root-1", root=Path(sys.argv[2])).acquire(app="crashed")
        os._exit(0)
        """,
        store.workspace,
        store.root,
    )
    assert crash.wait(timeout=5) == 0
    successor = store.acquire(app="after-crash")
    successor.release()

    executable = _subprocess(
        """
        from pathlib import Path
        import os, sys, time
        from amplifier_foundation.session.shared_state import SharedSessionStore
        SharedSessionStore(Path(sys.argv[1]), "root-1", root=Path(sys.argv[2])).acquire(app="exec")
        print("locked", flush=True)
        os.execv(sys.executable, [sys.executable, "-c", "import time; time.sleep(20)"])
        """,
        store.workspace,
        store.root,
    )
    assert executable.stdout is not None
    assert executable.stdout.readline().strip() == "locked"
    try:
        deadline = time.monotonic() + 3
        while True:
            try:
                successor = store.acquire(app="after-exec")
                break
            except SessionBusyError:
                assert time.monotonic() < deadline
                time.sleep(0.02)
        successor.release()
    finally:
        executable.kill()
        executable.wait(timeout=5)


@requires_posix
def test_invalid_identity_messages_and_metadata_are_rejected(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    with pytest.raises(ValueError, match="session_id"):
        SharedSessionStore(workspace, "../bad", root=tmp_path / "state")
    store = SharedSessionStore(workspace, "root-1", root=tmp_path / "state")
    with pytest.raises(ValueError, match="app"):
        store.acquire(app="")
    held = store.acquire(app="test")
    with pytest.raises(ValueError, match="messages"):
        held.write(["not a message"], bundle="portable")  # type: ignore[list-item]
    with pytest.raises(ValueError, match="credentials"):
        held.write([], bundle="portable", metadata={"token": "nope"})
    held.release()


def test_file_stamp_returns_none_only_for_missing_path(tmp_path: Path) -> None:
    path = tmp_path / "checkpoint.json"
    assert file_stamp(path) is None
    path.write_text(json.dumps({"ok": True}), encoding="utf-8")
    assert isinstance(file_stamp(path), FileStamp)


def test_acquire_fails_clearly_without_a_posix_locking_primitive(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(shared_state, "fcntl", None)
    with pytest.raises(RuntimeError, match="POSIX local filesystem"):
        _store(tmp_path).acquire(app="unsupported")


@requires_posix
def test_acquire_creates_private_state_and_rejects_unsafe_existing_paths(tmp_path: Path) -> None:
    store = _store(tmp_path)
    held = store.acquire(app="private")
    try:
        for path in (
            store.root,
            store.root / "v1",
            store.root / "v1" / __import__("hashlib").sha256(str(store.workspace).encode()).hexdigest(),
            store.checkpoint_path.parent,
        ):
            assert S_IMODE(path.stat().st_mode) & 0o077 == 0
        for path in (store._lock_path, store._owner_path):
            assert S_IMODE(path.stat().st_mode) & 0o077 == 0
    finally:
        held.release()

    unsafe_root = tmp_path / "unsafe"
    unsafe_root.mkdir(mode=0o700)
    unsafe_root.chmod(0o755)
    with pytest.raises(SharedStateError, match="unsafe permissions"):
        SharedSessionStore(store.workspace, "unsafe-root", root=unsafe_root).acquire(app="test")

    target = tmp_path / "target"
    target.mkdir(mode=0o700)
    symlink_root = tmp_path / "symlink-state"
    symlink_root.symlink_to(target, target_is_directory=True)
    with pytest.raises(SharedStateError, match="safe directory"):
        SharedSessionStore(store.workspace, "symlink-root", root=symlink_root).acquire(app="test")


@requires_posix
def test_delete_checkpoint_retains_lock_allows_recreate_and_denies_old_handle(tmp_path: Path) -> None:
    store = _store(tmp_path)
    first = store.acquire(app="first")
    first.write([{"turn": 1}], bundle="portable")
    contender = _subprocess(
        """
        from pathlib import Path
        import sys, time
        from amplifier_foundation.session.shared_state import SessionBusyError, SharedSessionStore
        store = SharedSessionStore(Path(sys.argv[1]), "root-1", root=Path(sys.argv[2]))
        for _ in range(2):
            try:
                store.acquire(app="contender")
            except SessionBusyError:
                print("busy", flush=True)
            else:
                print("acquired", flush=True)
            time.sleep(0.5)
        """,
        store.workspace,
        store.root,
    )
    assert contender.stdout is not None
    try:
        assert contender.stdout.readline().strip() == "busy"
        lock_inode = store._lock_path.stat().st_ino
        first.delete_checkpoint()
        assert not store.checkpoint_path.exists()
        assert store._lock_path.stat().st_ino == lock_inode
        first.write([{"turn": 2}], bundle="portable")
        assert contender.stdout.readline().strip() == "busy"
        _, stderr = contender.communicate(timeout=5)
        assert contender.returncode == 0, stderr
    finally:
        first.release()

    successor = store.acquire(app="successor")
    try:
        first.delete_checkpoint()
    except RuntimeError as error:
        assert "no longer active" in str(error)
    else:  # pragma: no cover - explicit API safety assertion
        pytest.fail("released held capability deleted a checkpoint")
    successor.release()


@requires_posix
def test_diagnostics_cannot_override_owner_fields_and_user_falls_back_to_uid(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = _store(tmp_path)
    held = store.acquire(app="test", pid=os.getpid())
    held.release()
    with pytest.raises(ValueError, match="library-owned"):
        store.acquire(app="test", pid=os.getpid() + 1)
    with pytest.raises(ValueError, match="library-owned"):
        store.acquire(app="test", active=False)

    monkeypatch.setattr(shared_state.getpass, "getuser", lambda: (_ for _ in ()).throw(KeyError()))
    owner = shared_state._owner_details("test", {}, store.workspace, store.session_id, store.root)
    assert owner["user"] == str(os.getuid())


def test_process_start_identity_handles_spaces_in_proc_comm(tmp_path: Path) -> None:
    proc_stat = tmp_path / "stat"
    proc_stat.write_text(
        "123 (name with spaces) S " + " ".join(str(field) for field in range(4, 22)) + " start-time",
        encoding="utf-8",
    )
    assert shared_state._process_start_identity(proc_stat) == "start-time"
