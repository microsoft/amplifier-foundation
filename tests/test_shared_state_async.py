"""Cancellation owns late acquisitions; passive history is never changed."""

from __future__ import annotations

import asyncio
from contextvars import ContextVar
import json
import os
from pathlib import Path
import subprocess
import sys
import threading

import pytest

from amplifier_foundation.session import shared_state
from amplifier_foundation.session.shared_state import (
    SessionBusyError,
    SharedSessionStore,
    SharedStateError,
)


requires_posix = pytest.mark.skipif(
    os.name != "posix" or shared_state.fcntl is None,
    reason="real process-lock assertions require POSIX flock",
)


async def reached(event: threading.Event) -> None:
    async with asyncio.timeout(5):
        while not event.is_set():
            await asyncio.sleep(0.001)


def wait(event: threading.Event) -> None:
    assert event.wait(8), "test barrier did not open"


@pytest.mark.asyncio
async def test_normal_result_preserves_context_diagnostics_and_identity(monkeypatch):
    # This logic is platform-neutral; physical lock behavior is tested below.
    store = object.__new__(SharedSessionStore)
    context = ContextVar("acquire-test", default=None)
    identity, diagnostic, held = object(), object(), object()
    calls = []

    def acquire(**kwargs):
        calls.append((threading.get_ident(), context.get(), kwargs))
        return held

    monkeypatch.setattr(store, "acquire", acquire)
    token = context.set(identity)
    try:
        result = await store.acquire_async(app="fixture", marker=diagnostic)
    finally:
        context.reset(token)
    assert result is held
    assert len(calls) == 1
    assert calls[0][0] != threading.get_ident()
    assert calls[0][1] is identity
    assert calls[0][2] == {"app": "fixture", "marker": diagnostic}


@pytest.mark.asyncio
@pytest.mark.parametrize("error", [
    SessionBusyError({"app": "existing-owner"}),
    SharedStateError("fixture ownership failure"),
    ValueError("fixture invalid argument"),
])
async def test_normal_acquisition_exception_is_not_wrapped_or_retried(monkeypatch, error):
    store = object.__new__(SharedSessionStore)
    calls = []

    def acquire(**kwargs):
        calls.append(kwargs)
        raise error

    monkeypatch.setattr(store, "acquire", acquire)
    with pytest.raises(type(error)) as raised:
        await store.acquire_async(app="fixture")
    assert raised.value is error
    assert calls == [{"app": "fixture"}]


@pytest.mark.asyncio
async def test_repeated_cancellation_waits_for_acquire_and_release(monkeypatch):
    store = object.__new__(SharedSessionStore)
    acquiring, acquired, releasing, released = (threading.Event() for _ in range(4))
    context = ContextVar("cleanup-context", default=None)
    identity = object()
    calls = []

    class Handle:
        def release(self):
            calls.append(("release", threading.get_ident(), context.get()))
            releasing.set()
            wait(released)

    def acquire(**kwargs):
        calls.append(("acquire", threading.get_ident(), context.get()))
        acquiring.set()
        wait(acquired)
        return Handle()

    monkeypatch.setattr(store, "acquire", acquire)
    token = context.set(identity)
    task = asyncio.create_task(store.acquire_async(app="fixture"))
    context.reset(token)
    try:
        await reached(acquiring)
        task.cancel("original cancellation")
        await asyncio.sleep(0)
        task.cancel("repeat during acquire")
        await asyncio.sleep(0)
        assert not task.done()
        acquired.set()
        await reached(releasing)
        task.cancel("repeat during release")
        await asyncio.sleep(0)
        assert not task.done()
        released.set()
        with pytest.raises(asyncio.CancelledError) as raised:
            await task
        assert raised.value.args == ("original cancellation",)
        assert task.cancelled()
        assert [row[0] for row in calls] == ["acquire", "release"]
        assert all(row[1] != threading.get_ident() and row[2] is identity for row in calls)
    finally:
        acquired.set()
        released.set()
        await asyncio.gather(task, return_exceptions=True)


@pytest.mark.asyncio
@pytest.mark.parametrize("failure_at", ["acquire", "release"])
async def test_late_failure_is_cause_without_replacing_original_cancellation(monkeypatch, failure_at):
    store = object.__new__(SharedSessionStore)
    entered, proceed = threading.Event(), threading.Event()
    error = SharedStateError("late fixture failure")
    calls = []

    class Handle:
        def release(self):
            calls.append("release")
            raise error

    def acquire(**kwargs):
        calls.append("acquire")
        entered.set()
        wait(proceed)
        if failure_at == "acquire":
            raise error
        return Handle()

    monkeypatch.setattr(store, "acquire", acquire)
    task = asyncio.create_task(store.acquire_async(app="fixture"))
    try:
        await reached(entered)
        task.cancel("primary")
        await asyncio.sleep(0)
        assert not task.done()
        proceed.set()
        with pytest.raises(asyncio.CancelledError) as raised:
            await task
        assert raised.value.args == ("primary",)
        assert raised.value.__cause__ is error
        assert task.cancelled()
        assert calls == (["acquire"] if failure_at == "acquire" else ["acquire", "release"])
    finally:
        proceed.set()
        await asyncio.gather(task, return_exceptions=True)


def independent_acquire(store):
    # Import the same source/installed Foundation package as this test process.
    package_root = Path(shared_state.__file__).resolve().parents[2]
    script = """
import json,sys
sys.path.insert(0,sys.argv[1])
from amplifier_foundation.session.shared_state import SessionBusyError, SharedSessionStore
store=SharedSessionStore(sys.argv[2],sys.argv[3],root=sys.argv[4])
try:
    held=store.acquire(app='independent')
except SessionBusyError:
    print(json.dumps({'state':'busy'}))
else:
    try: print(json.dumps({'state':'acquired','active':held.active}))
    finally: held.release()
"""
    result = subprocess.run([sys.executable, "-B", "-c", script, str(package_root),
                             str(store.workspace), store.session_id, str(store.root)],
                            capture_output=True, text=True, timeout=8)
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


@requires_posix
@pytest.mark.asyncio
async def test_normal_real_acquisition_remains_exclusive_until_caller_release(tmp_path, monkeypatch):
    monkeypatch.setenv("AMPLIFIER_HOME", str(tmp_path / "native-home"))
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    store = SharedSessionStore(workspace, "normal-owner", root=tmp_path / "state")
    original, handles = store.acquire, []

    def acquire(**kwargs):
        held = original(**kwargs)
        handles.append(held)
        return held

    monkeypatch.setattr(store, "acquire", acquire)
    held = await store.acquire_async(app="async-fixture", version="test")
    try:
        assert handles == [held] and held.active
        assert held.owner["app"] == "async-fixture" and held.owner["version"] == "test"
        assert independent_acquire(store) == {"state": "busy"}
        assert not store.checkpoint_path.exists()
    finally:
        held.release()
    assert independent_acquire(store) == {"state": "acquired", "active": True}


@requires_posix
@pytest.mark.asyncio
@pytest.mark.parametrize("barrier", ["before-acquire", "before-delivery"])
async def test_cancelled_real_acquisition_releases_before_return_without_history_changes(tmp_path, monkeypatch, barrier):
    monkeypatch.setenv("AMPLIFIER_HOME", str(tmp_path / "native-home"))
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    store = SharedSessionStore(workspace, "async-owner", root=tmp_path / "state")
    seed = store.acquire(app="seed")
    try:
        seed.write([{"role": "user", "content": "retained"}], bundle="fixture")
    finally:
        seed.release()
    checkpoint = store.checkpoint_path.read_bytes()
    entered, proceed, finished = (threading.Event() for _ in range(3))
    original = store.acquire
    handles, releases, acquisitions = [], [], []

    def acquire(**kwargs):
        acquisitions.append(kwargs)
        try:
            if barrier == "before-acquire":
                entered.set()
                wait(proceed)
            held = original(**kwargs)
            handles.append(held)
            release = held.release

            def counted_release():
                releases.append(held)
                return release()

            held.release = counted_release
            if barrier == "before-delivery":
                entered.set()
                wait(proceed)
            return held
        finally:
            finished.set()

    monkeypatch.setattr(store, "acquire", acquire)
    task = asyncio.create_task(store.acquire_async(app="async-fixture", version="test"))
    try:
        await reached(entered)
        if barrier == "before-delivery":
            assert independent_acquire(store) == {"state": "busy"}
        task.cancel("stop acquiring")
        await asyncio.sleep(0)
        assert not task.done(), "cancellation returned before ownership retirement"
        proceed.set()
        with pytest.raises(asyncio.CancelledError) as raised:
            await task
        assert raised.value.args == ("stop acquiring",)
        assert finished.is_set() and len(handles) == 1 and releases == handles
        assert not handles[0].active
        assert handles[0]._fd not in shared_state._LOCK_FDS
        assert str(store._lock_path) not in shared_state._PROCESS_LOCKS
        assert acquisitions == [{"app": "async-fixture", "version": "test"}]
        successor = original(app="same-process-successor")
        successor.release()
        assert independent_acquire(store) == {"state": "acquired", "active": True}
        assert store.checkpoint_path.read_bytes() == checkpoint
        assert not list((tmp_path / "native-home").rglob("transcript.jsonl"))
    finally:
        proceed.set()
        await asyncio.gather(task, return_exceptions=True)
        await reached(finished)
        for held in handles:
            if held.active:
                held.release()
