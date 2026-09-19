"""Ownership tests use actual OS locks and local peer-authenticated sockets."""

import asyncio
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import textwrap
import time

import pytest

from amplifier_foundation.session import (
    CannotRelease,
    ReadyToRelease,
    SessionBusyError,
    SharedSessionStore,
    register_release_handler,
    request_release,
)

pytestmark = [
    pytest.mark.asyncio,
    pytest.mark.skipif(os.name != "posix", reason="POSIX locking"),
]


@pytest.fixture
def paths(tmp_path):
    # macOS's socket path budget is shorter than a pytest tmp directory.
    with tempfile.TemporaryDirectory(prefix="ah-") as ipc:
        yield tmp_path, Path(ipc)


def address(paths, family="one"):
    directory, _ = paths
    return SharedSessionStore(directory, "session-1", root=directory / family)


async def test_safe_release_and_new_generation(paths):
    store = address(paths)
    held = store.acquire(app="first")
    old_id = held.owner["acquisition_id"]
    observed = []

    async def prepare(request):
        with pytest.raises(SessionBusyError):
            store.acquire(app="too-early")
        observed.append((request.requester_app, request.peer_uid))
        request.report_progress("draining")
        await asyncio.sleep(0)
        return ReadyToRelease()

    registration = await register_release_handler(
        held, prepare_release=prepare, runtime_dir=paths[1]
    )
    result = await request_release(
        store, expected_owner=held.owner, request_id="r1", requester_app="second"
    )
    assert result.status == "released"
    assert observed == [("second", os.getuid())]
    next_owner = store.acquire(app="second")
    assert next_owner.owner["acquisition_id"] != old_id
    next_owner.release()
    await registration.close()


async def test_failure_duplicates_and_changed_content_do_not_release(paths):
    store = address(paths)
    held = store.acquire(app="first")
    calls = []

    async def prepare(request):
        calls.append(request.request_id)
        return CannotRelease("save_failed", "Unable to save history.")

    registration = await register_release_handler(
        held, prepare_release=prepare, runtime_dir=paths[1]
    )
    for _ in range(2):
        result = await request_release(
            store, expected_owner=held.owner, request_id="r1", requester_app="second"
        )
        assert result.status == "cannot_release"
        assert result.code == "save_failed"
    assert calls == ["r1"]
    result = await request_release(
        store, expected_owner=held.owner, request_id="r1", requester_app="different"
    )
    assert result.status == "protocol_error"
    with pytest.raises(SessionBusyError):
        store.acquire(app="second")
    await registration.close()
    held.release()


async def test_request_timeout_does_not_cancel_drain_and_contenders_are_bounded(paths):
    store = address(paths)
    held = store.acquire(app="first")
    entered, finish = asyncio.Event(), asyncio.Event()

    async def prepare(request):
        entered.set()
        await finish.wait()
        return ReadyToRelease()

    registration = await register_release_handler(
        held, prepare_release=prepare, runtime_dir=paths[1]
    )
    task = asyncio.create_task(
        request_release(
            store,
            expected_owner=held.owner,
            request_id="r1",
            requester_app="second",
            timeout=0.15,
        )
    )
    await asyncio.wait_for(entered.wait(), 2)
    other = await request_release(
        store, expected_owner=held.owner, request_id="r2", requester_app="third"
    )
    assert other.status == "handoff_in_progress"
    assert (await task).status == "timed_out"
    assert held.active
    finish.set()
    await asyncio.wait_for(asyncio.shield(registration.pending), 2)
    assert not held.active
    store.acquire(app="next").release()
    await registration.close()


async def test_independent_roots_and_stale_request(paths):
    first, other = address(paths), address(paths, "agent")
    held, separate = first.acquire(app="cli"), other.acquire(app="agent")
    calls = []

    async def prepare(request):
        calls.append(request.request_id)
        return ReadyToRelease()

    registration = await register_release_handler(
        held, prepare_release=prepare, runtime_dir=paths[1]
    )
    previous = held.owner
    wrong_root = await request_release(
        other, expected_owner=previous, request_id="wrong-root", requester_app="web"
    )
    assert wrong_root.status == "owner_changed" and held.active and separate.active
    held.release()
    successor = first.acquire(app="new-cli")
    next_registration = await register_release_handler(
        successor, prepare_release=prepare, runtime_dir=paths[1]
    )
    # Force delivery to the live endpoint while retaining the old acquisition.
    previous["handoff"] = successor.owner["handoff"]
    result = await request_release(
        first, expected_owner=previous, request_id="late", requester_app="web"
    )
    assert result.status == "owner_changed"
    assert not calls and successor.active and separate.active
    await next_registration.close()
    successor.release()
    separate.release()
    await registration.close()


async def test_handler_exception_retains_lock_and_registration_failure_is_clean(paths):
    store = address(paths)
    held = store.acquire(app="owner")

    async def fail(request):
        raise OSError("save failed")

    with pytest.raises(ValueError, match="too long"):
        await register_release_handler(
            held, prepare_release=fail, runtime_dir=paths[0] / ("long" * 30)
        )
    assert held.active and "handoff" not in held.owner
    registration = await register_release_handler(
        held, prepare_release=fail, runtime_dir=paths[1]
    )
    result = await request_release(
        store, expected_owner=held.owner, request_id="request", requester_app="web"
    )
    assert result.status == "cannot_release" and held.active
    await registration.close()
    held.release()


async def test_real_process_handoff_under_custom_root(paths):
    store = address(paths, "agent")
    child = subprocess.Popen(
        [
            sys.executable,
            "-u",
            "-c",
            textwrap.dedent("""
        import asyncio, json, sys
        from amplifier_foundation.session import SharedSessionStore, ReadyToRelease, register_release_handler
        async def main():
            held = SharedSessionStore(sys.argv[1], 'session-1', root=sys.argv[2]).acquire(app='third-host')
            async def release(request):
                return ReadyToRelease()
            registration = await register_release_handler(held, prepare_release=release, runtime_dir=sys.argv[3])
            print(json.dumps(held.owner), flush=True)
            while held.active:
                await asyncio.sleep(.01)
            await registration.close()
            await asyncio.sleep(.05)
        asyncio.run(main())
    """),
            str(store.workspace),
            str(store.root),
            str(paths[1]),
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        line = await asyncio.wait_for(asyncio.to_thread(child.stdout.readline), 10)
        assert line, child.stderr.read()
        owner = json.loads(line)
        with pytest.raises(SessionBusyError):
            store.acquire(app="parent")
        result = await request_release(
            store, expected_owner=owner, request_id="r1", requester_app="parent"
        )
        assert result.status == "released"
        store.acquire(app="parent").release()
        assert await asyncio.to_thread(child.wait, 5) == 0
    finally:
        if child.poll() is None:
            child.kill()
            child.wait()


async def test_expired_request_never_invokes_callback(paths):
    store = address(paths)
    held = store.acquire(app="owner")
    calls = []

    async def prepare(request):
        calls.append(request)
        return ReadyToRelease()

    registration = await register_release_handler(
        held, prepare_release=prepare, runtime_dir=paths[1]
    )
    owner = held.owner
    reader, writer = await asyncio.open_unix_connection(owner["handoff"]["endpoint"])
    target = {
        key: owner[key]
        for key in ("workspace", "session_id", "acquisition_id", "coordination_root")
    }
    target["registration_id"] = owner["handoff"]["registration_id"]
    writer.write(
        json.dumps(
            {
                "version": 1,
                "request_id": "expired",
                "requester_app": "requester",
                "target": target,
                "deadline": time.monotonic() - 1,
            }
        ).encode()
        + b"\n"
    )
    await writer.drain()
    result = json.loads(await asyncio.wait_for(reader.readline(), 2))
    writer.close()
    await writer.wait_closed()
    assert result["status"] == "timed_out" and not calls and held.active
    await registration.close()
    held.release()


async def test_request_registry_does_not_evict_completed_identities(paths, monkeypatch):
    import amplifier_foundation.session.handoff as handoff

    monkeypatch.setattr(handoff, "_MAX_REQUESTS", 1)
    store = address(paths)
    held = store.acquire(app="owner")
    calls = []

    async def prepare(request):
        calls.append(request.request_id)
        return CannotRelease("failed", "Cannot save yet.")

    registration = await register_release_handler(
        held, prepare_release=prepare, runtime_dir=paths[1]
    )
    for identity in ("original", "new", "original"):
        result = await request_release(
            store,
            expected_owner=held.owner,
            request_id=identity,
            requester_app="requester",
        )
        assert result.status == "cannot_release"
    assert calls == ["original"]
    await registration.close()
    held.release()


async def test_other_user_and_unsafe_endpoint_are_refused(paths, monkeypatch):
    import amplifier_foundation.session.handoff as handoff

    store = address(paths)
    held = store.acquire(app="owner")
    calls = []

    async def prepare(request):
        calls.append(request)
        return ReadyToRelease()

    registration = await register_release_handler(
        held, prepare_release=prepare, runtime_dir=paths[1]
    )
    os.chmod(registration.path, 0o666)
    result = await request_release(
        store, expected_owner=held.owner, request_id="unsafe", requester_app="requester"
    )
    assert result.status == "unauthorized"
    os.chmod(registration.path, 0o600)
    monkeypatch.setattr(handoff, "_peer_uid", lambda sock: os.getuid() + 1)
    result = await request_release(
        store,
        expected_owner=held.owner,
        request_id="other-user",
        requester_app="requester",
    )
    assert result.status == "unauthorized" and held.active and not calls
    await registration.close()
    held.release()
