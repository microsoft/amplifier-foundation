"""Durable execution fences across host adapters and process lifetimes."""

from __future__ import annotations

import json
import errno
import os
from pathlib import Path
import subprocess
import stat
import sys

import pytest

from amplifier_foundation.session import (
    HeldTransfer,
    SessionTransferFencedError,
    SharedSessionStore,
)
from amplifier_foundation.session.shared_state import SharedStateError


pytestmark = pytest.mark.skipif(os.name != "posix", reason="requires POSIX locks")


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setenv("AMPLIFIER_HOME", str(tmp_path / "native"))
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    return SharedSessionStore(workspace, "task-1", root=tmp_path / "locks")


def stage(store, *, role="source"):
    held = store.acquire(app="exporter")
    try:
        return held.fence_transfer("transfer-1", "destination", role=role)
    finally:
        held.release()


def test_marker_is_native_private_and_does_not_change_saved_history(store):
    assert store.transfer_fence() is None
    assert not store.transfer_fence_path.parent.exists()
    held = store.acquire(app="source")
    try:
        held.write([{"role": "user", "content": "keep"}], bundle="portable")
        before = store.checkpoint_path.read_bytes()
        directory = store.transfer_fence_path.parent
        directory.mkdir(mode=0o700, parents=True)
        native = {"transcript.jsonl": b'{"role":"user","content":"original"}\n',
                  "metadata.json": b'{"session_id":"task-1","unknown":"retained"}'}
        for name, content in native.items():
            (directory / name).write_bytes(content)
        marker = held.fence_transfer("transfer-1", "destination")
        assert marker == held.fence_transfer("transfer-1", "destination")
        assert store.checkpoint_path.read_bytes() == before
        assert store.transfer_fence_path.name == "transfer-fence.json"
        assert store.transfer_fence_path.parent.name == "task-1"
        assert store.transfer_fence_path.stat().st_mode & 0o777 == 0o600
        assert all((directory / name).read_bytes() == content for name, content in native.items())
        for operation in (held.check, lambda: held.write([], bundle="bad"), held.delete_checkpoint):
            with pytest.raises(SessionTransferFencedError):
                operation()
    finally:
        held.release()
    assert store.read()["messages"] == [{"role": "user", "content": "keep"}]


def test_fence_survives_process_death_and_blocks_other_adapter(store):
    script = """
import os, sys
from amplifier_foundation.session import SharedSessionStore
store = SharedSessionStore(sys.argv[1], 'task-1', root=sys.argv[2])
held = store.acquire(app='source')
held.write([{'role':'user','content':'saved'}], bundle='portable')
held.fence_transfer('transfer-1', 'destination')
os._exit(0)
"""
    result = subprocess.run([sys.executable, "-c", script, str(store.workspace), str(store.root)], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    owner_before = store._owner_path.read_bytes()
    for _ in range(2):
        with pytest.raises(SessionTransferFencedError) as error:
            store.acquire(app="different-adapter")
        assert error.value.fence["transfer_id"] == "transfer-1"
    assert store._owner_path.read_bytes() == owner_before
    assert store.read()["messages"][0]["content"] == "saved"


def test_matching_recovery_cannot_execute_and_committed_source_cannot_reopen(store):
    stage(store)
    with pytest.raises(SharedStateError, match="exact staged"):
        store.acquire_transfer("wrong-receipt", app="recovery")
    held = store.acquire_transfer("transfer-1", app="recovery")
    assert isinstance(held, HeldTransfer)
    try:
        for operation in (held.check, held.read, lambda: held.write([], bundle="bad"), held.delete_checkpoint):
            with pytest.raises(SharedStateError, match="cannot authorize execution"):
                operation()
        assert held.commit_transfer()["phase"] == "committed"
        with pytest.raises(SharedStateError, match="exact staged"):
            held.clear_transfer()
    finally:
        held.release()
    fresh = SharedSessionStore(store.workspace, store.session_id, root=store.root)
    with pytest.raises(SharedStateError, match="exact staged"):
        fresh.acquire_transfer("transfer-1", app="recovery")
    with pytest.raises(SessionTransferFencedError):
        fresh.acquire(app="ordinary")
    # Native storage owns the fence even if an adapter selects another local
    # coordination root. The marker is not advisory owner.json metadata.
    alternate = SharedSessionStore(store.workspace, store.session_id, root=store.root.parent / "other-locks")
    with pytest.raises(SessionTransferFencedError):
        alternate.acquire(app="other-adapter")


@pytest.mark.parametrize("role", ["source", "destination"])
def test_explicit_clear_requires_new_normal_acquisition(store, role):
    stage(store, role=role)
    with pytest.raises(SessionTransferFencedError):
        store.acquire(app="ordinary", transfer_id="transfer-1")
    held = store.acquire_transfer("transfer-1", app="recovery")
    try:
        if role == "destination":
            with pytest.raises(SharedStateError, match="only a source"):
                held.commit_transfer()
        held.clear_transfer()
        with pytest.raises(SharedStateError, match="cannot authorize execution"):
            held.check()
    finally:
        held.release()
    assert store.transfer_fence() is None
    ordinary = store.acquire(app="destination")
    try:
        ordinary.write([], bundle="portable")
    finally:
        ordinary.release()


@pytest.mark.parametrize("mutation", ["missing-role", "unknown-phase", "wrong-session", "non-string-role", "invalid-json"])
def test_corrupt_marker_fails_closed_without_changing_it(store, mutation):
    stage(store)
    record = store.transfer_fence()
    if mutation == "missing-role":
        record.pop("role")
    elif mutation == "unknown-phase":
        record["phase"] = "released"
    elif mutation == "wrong-session":
        record["session_id"] = "other"
    elif mutation == "non-string-role":
        record["role"] = []
    store.transfer_fence_path.write_text("{" if mutation == "invalid-json" else json.dumps(record))
    before = store.transfer_fence_path.read_bytes()
    for acquire in (lambda: store.acquire(app="ordinary"), lambda: store.acquire_transfer("transfer-1", app="recovery")):
        with pytest.raises(SharedStateError):
            acquire()
    assert store.transfer_fence_path.read_bytes() == before


def test_marker_and_native_directory_symlinks_are_rejected(store, tmp_path):
    stage(store)
    original = store.transfer_fence_path.read_bytes()
    target = tmp_path / "marker.json"
    target.write_bytes(original)
    target.chmod(0o600)
    store.transfer_fence_path.unlink()
    store.transfer_fence_path.symlink_to(target)
    with pytest.raises(SharedStateError, match="safe regular"):
        store.acquire(app="ordinary")
    store.transfer_fence_path.unlink()
    directory = store.transfer_fence_path.parent
    moved = directory.with_name("moved")
    directory.rename(moved)
    directory.symlink_to(moved, target_is_directory=True)
    with pytest.raises(SharedStateError, match="directory is unsafe"):
        store.acquire(app="ordinary")
    assert target.read_bytes() == original


def test_released_source_handle_and_mismatched_fence_cannot_change_state(store):
    held = store.acquire(app="source")
    first = held.fence_transfer("transfer-1", "destination")
    with pytest.raises(SharedStateError, match="different transfer"):
        held.fence_transfer("transfer-2", "destination")
    held.release()
    with pytest.raises(RuntimeError, match="no longer active"):
        held.fence_transfer("transfer-1", "destination")
    assert store.transfer_fence() == first


def test_recovery_requires_an_existing_marker(store):
    with pytest.raises(SharedStateError, match="exact staged"):
        store.acquire_transfer("transfer-1", app="recovery")
    held = store.acquire(app="ordinary")
    held.release()


def test_nonprivate_marker_fails_closed(store):
    stage(store)
    store.transfer_fence_path.chmod(0o644)
    with pytest.raises(SharedStateError, match="unsafe permissions"):
        store.acquire(app="ordinary")


def test_fresh_native_root_persists_all_ancestors_before_marker(tmp_path, monkeypatch):
    import amplifier_foundation.session.shared_state as shared_state

    native = tmp_path / "new-parent" / "new-account" / "native"
    monkeypatch.setenv("AMPLIFIER_HOME", str(native))
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    store = SharedSessionStore(workspace, "fresh-task", root=tmp_path / "locks")
    held = store.acquire(app="destination")
    events = []
    sync = shared_state._fsync_directory
    atomic = shared_state._atomic_json

    def recorded_sync(directory):
        sync(directory)
        events.append(("synced", Path(directory)))

    def recorded_atomic(path, value):
        events.append(("published", Path(path)))
        return atomic(path, value)

    monkeypatch.setattr(shared_state, "_fsync_directory", recorded_sync)
    monkeypatch.setattr(shared_state, "_atomic_json", recorded_atomic)
    try:
        held.fence_transfer("fresh-transfer", "destination", role="destination")
        marker_index = events.index(("published", store.transfer_fence_path))
        created = []
        directory = store.transfer_fence_path.parent
        while directory != tmp_path:
            created.append(directory)
            directory = directory.parent
        expected = [("synced", directory.parent) for directory in reversed(created)]
        assert events[:len(expected)] == expected
        ancestors = [store.transfer_fence_path.parent, *store.transfer_fence_path.parent.parents]
        assert events[len(expected):marker_index] == [("synced", path) for path in reversed(ancestors)]
        assert ("synced", store.transfer_fence_path.parent) in events[marker_index + 1:]
        assert all(directory.stat().st_mode & 0o777 == 0o700 for directory in created)
        assert not (store.transfer_fence_path.parent / "transcript.jsonl").exists()
    finally:
        held.release()
    with pytest.raises(SessionTransferFencedError):
        SharedSessionStore(workspace, "fresh-task", root=store.root).acquire(app="another-host")


def test_existing_native_directories_keep_modes_and_skip_creation_sync(store, monkeypatch):
    import amplifier_foundation.session.shared_state as shared_state

    directory = store.transfer_fence_path.parent
    directory.mkdir(parents=True, mode=0o755)
    directory.chmod(0o755)
    root = Path(os.environ["AMPLIFIER_HOME"])
    existing = [directory, *directory.parents]
    existing = existing[:existing.index(root) + 1]
    before = {path: path.stat().st_mode for path in existing}

    def no_creation(path):
        pytest.fail("existing native history must not create ancestors")

    monkeypatch.setattr(shared_state, "_mkdir_private_durable", no_creation)
    stage(store)
    assert {path: path.stat().st_mode for path in existing} == before


def test_private_ancestor_creation_is_bounded_and_has_no_partial_tree(tmp_path):
    from amplifier_foundation.session.shared_state import _mkdir_private_durable

    requested = tmp_path.joinpath(*["d"] * 129)
    with pytest.raises(SharedStateError, match="depth limit"):
        _mkdir_private_durable(requested)
    assert not (tmp_path / "d").exists()


@pytest.mark.parametrize("error", [errno.EIO, errno.ENOSPC, errno.EACCES, errno.EBADF])
def test_directory_fsync_real_failures_propagate_and_close_descriptor(tmp_path, monkeypatch, error):
    import amplifier_foundation.session.shared_state as shared_state

    descriptors = []

    def fail(fd):
        descriptors.append(fd)
        raise OSError(error, "injected persistence failure")

    monkeypatch.setattr(shared_state.os, "fsync", fail)
    with pytest.raises(OSError) as failure:
        shared_state._fsync_directory(tmp_path)
    assert failure.value.errno == error
    with pytest.raises(OSError) as closed:
        os.fstat(descriptors[0])
    assert closed.value.errno == errno.EBADF


def test_directory_open_failure_is_not_treated_as_unsupported(tmp_path, monkeypatch):
    import amplifier_foundation.session.shared_state as shared_state

    def fail(*args, **kwargs):
        raise OSError(errno.EACCES, "injected open failure")

    monkeypatch.setattr(shared_state.os, "open", fail)
    with pytest.raises(OSError) as failure:
        shared_state._fsync_directory(tmp_path)
    assert failure.value.errno == errno.EACCES


@pytest.mark.parametrize("error", sorted({errno.EINVAL, errno.ENOSYS, errno.ENOTSUP, errno.EOPNOTSUPP}))
def test_only_explicitly_unsupported_directory_fsync_is_tolerated(tmp_path, monkeypatch, error):
    import amplifier_foundation.session.shared_state as shared_state

    def unsupported(fd):
        raise OSError(error, "directory fsync unsupported")

    monkeypatch.setattr(shared_state.os, "fsync", unsupported)
    shared_state._fsync_directory(tmp_path)


def test_failed_ancestor_sync_never_acks_and_retry_resyncs_existing_residue(store, monkeypatch):
    import amplifier_foundation.session.shared_state as shared_state

    held = store.acquire(app="source")
    sync = shared_state._fsync_directory
    home = Path(os.environ["AMPLIFIER_HOME"])
    events = []
    try:
        def fail_parent(directory):
            if directory == home.parent:
                raise OSError(errno.EIO, "injected ancestor failure")
            sync(directory)

        monkeypatch.setattr(shared_state, "_fsync_directory", fail_parent)
        with pytest.raises(OSError, match="ancestor failure"):
            held.fence_transfer("transfer-1", "destination")
        assert home.exists() and not store.transfer_fence_path.exists()

        def record(directory):
            sync(directory)
            events.append(directory)

        monkeypatch.setattr(shared_state, "_fsync_directory", record)
        assert held.fence_transfer("transfer-1", "destination")["phase"] == "staged"
        # The root existed on retry, but its earlier parent sync never succeeded.
        assert home.parent in events
        assert store.transfer_fence_path.parent in events
    finally:
        monkeypatch.setattr(shared_state, "_fsync_directory", sync)
        held.release()


def test_staged_marker_visible_after_failed_sync_requires_new_durability_ack(store, monkeypatch):
    import amplifier_foundation.session.shared_state as shared_state

    held = store.acquire(app="source")
    sync = os.fsync
    temporary_marker = store.transfer_fence_path
    try:
        def fail_after_replace(fd):
            if stat.S_ISDIR(os.fstat(fd).st_mode) and temporary_marker.exists():
                raise OSError(errno.EIO, "injected marker-directory failure")
            sync(fd)

        monkeypatch.setattr(shared_state.os, "fsync", fail_after_replace)
        for _ in range(2):
            with pytest.raises(OSError, match="marker-directory failure"):
                held.fence_transfer("transfer-1", "destination")
            assert store.transfer_fence()["phase"] == "staged"
        monkeypatch.setattr(shared_state.os, "fsync", sync)
        file_syncs = []

        def record(fd):
            file_syncs.append(stat.S_ISREG(os.fstat(fd).st_mode))
            sync(fd)

        monkeypatch.setattr(shared_state.os, "fsync", record)
        assert held.fence_transfer("transfer-1", "destination")["phase"] == "staged"
        assert True in file_syncs and False in file_syncs
    finally:
        monkeypatch.setattr(shared_state.os, "fsync", sync)
        held.release()


def test_commit_sync_failure_never_acks_and_exact_live_retry_can_confirm(store, monkeypatch):
    import amplifier_foundation.session.shared_state as shared_state

    stage(store)
    held = store.acquire_transfer("transfer-1", app="source")
    sync = os.fsync
    try:
        def fail_committed(fd):
            if stat.S_ISDIR(os.fstat(fd).st_mode) and store.transfer_fence()["phase"] == "committed":
                raise OSError(errno.EIO, "injected commit failure")
            sync(fd)

        monkeypatch.setattr(shared_state.os, "fsync", fail_committed)
        for _ in range(2):
            with pytest.raises(OSError, match="commit failure"):
                held.commit_transfer()
            assert store.transfer_fence()["phase"] == "committed"
        monkeypatch.setattr(shared_state.os, "fsync", sync)
        assert held.commit_transfer()["phase"] == "committed"
        with pytest.raises(SharedStateError, match="exact staged"):
            held.clear_transfer()
    finally:
        monkeypatch.setattr(shared_state.os, "fsync", sync)
        held.release()


def test_restart_commit_confirmation_checks_exact_fence_and_repeats_durability(store, monkeypatch):
    import amplifier_foundation.session.shared_state as shared_state

    stage(store)
    with pytest.raises(SharedStateError, match="exact committed source"):
        store.confirm_transfer_commit("transfer-1", app="confirmer")
    held = store.acquire_transfer("transfer-1", app="source")
    held.commit_transfer()
    held.release()
    fresh = SharedSessionStore(store.workspace, store.session_id, root=store.root)
    with pytest.raises(SharedStateError, match="exact committed source"):
        fresh.confirm_transfer_commit("different-transfer", app="confirmer")
    with pytest.raises(SharedStateError, match="exact staged"):
        fresh.acquire_transfer("transfer-1", app="recovery")
    sync = shared_state._fsync_directory
    events = []

    def fail(directory):
        if directory == fresh.transfer_fence_path.parent:
            raise OSError(errno.EIO, "injected confirmation failure")
        sync(directory)

    monkeypatch.setattr(shared_state, "_fsync_directory", fail)
    with pytest.raises(OSError, match="confirmation failure"):
        fresh.confirm_transfer_commit("transfer-1", app="confirmer")

    def record(directory):
        sync(directory)
        events.append(directory)

    monkeypatch.setattr(shared_state, "_fsync_directory", record)
    confirmed = fresh.confirm_transfer_commit("transfer-1", app="confirmer")
    assert confirmed["phase"] == "committed"
    assert fresh.transfer_fence_path.parent in events
    assert Path(os.environ["AMPLIFIER_HOME"]).parent in events
    with pytest.raises(SessionTransferFencedError):
        fresh.acquire(app="ordinary")
