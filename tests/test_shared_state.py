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

import pytest

from amplifier_foundation.session.shared_state import (
    FileStamp,
    HeldSession,
    SessionBusyError,
    SharedSessionStore,
    SharedStateError,
    file_stamp,
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


def test_stamp_does_not_read_json_and_detects_same_size_replacement(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    store = _store(tmp_path)
    held = store.acquire(app="test")
    first = held.write([{"x": "a"}], bundle="portable")
    import amplifier_foundation.session.shared_state as shared_state

    monkeypatch.setattr(shared_state, "_read_json", lambda *args, **kwargs: pytest.fail("stamp read JSON"))
    assert store.stamp() == first
    monkeypatch.undo()
    second = held.write([{"x": "b"}], bundle="portable")
    assert first.size == second.size
    assert first != second
    held.release()


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
