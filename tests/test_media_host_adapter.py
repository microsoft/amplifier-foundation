"""Public Core host-created slot adapters; no Unified dependency or child execution."""

import asyncio
from importlib.metadata import version

import pytest

from amplifier_core import AmplifierSession, HookResult
from amplifier_foundation.session.media_hook_bridge import (
    MediaMountLifecycle, install_media_hook_bridge, media_mount_lifecycle,
)


pytestmark = [
    pytest.mark.asyncio,
    pytest.mark.skipif(version("amplifier-core") != "2.0.2",
                       reason="participating graph qualifies exact Core 2.0.2"),
]


class Loader:
    """Deterministic module construction through the actual Core loader seam."""

    def __init__(self):
        self.queue = []

    async def load(self, module_id, config, *, source_hint=None, coordinator=None):
        async def mount(current):
            if module_id == "fixture-loop":
                await current.mount("orchestrator", object())
            elif module_id == "fixture-context":
                await current.mount("context", object())
            elif module_id == "fixture-tool":
                await current.mount("tools", object(), name="configured")
        return mount

    def enqueue_on_session_ready(self, name, callback):
        self.queue.append((name, callback))

    def get_on_session_ready_queue(self):
        return self.queue

    def clear_on_session_ready_queue(self):
        self.queue.clear()


async def test_configured_adapter_remains_supported_inside_actual_module_mount():
    original = object()
    adapter = WorkCheckedTool(original)

    class AdaptingLoader(Loader):
        async def load(self, module_id, config, **kwargs):
            if module_id != "fixture-tool":
                return await super().load(module_id, config, **kwargs)

            async def mount(current):
                await current.mount("tools", original, name="configured")
                await current.mount_adapter(
                    "tools", adapter, previous=original, name="configured")
            return mount

    session = AmplifierSession({
        "session": {"orchestrator": "fixture-loop", "context": "fixture-context"},
        "tools": [{"module": "fixture-tool"}],
    })
    session.coordinator.loader = AdaptingLoader()
    bridge = install_media_hook_bridge(session.coordinator)
    graph = bridge.begin_participating_graph(
        session, owner="configured-adapter-during-initialization",
        participants=("fixture-loop", "fixture-context", "fixture-tool"),
    )
    try:
        await session.initialize()
        graph.seal()
        assert session.coordinator.get("tools", "configured") is adapter
        assert bridge.validate_receipt(bridge.graph_receipt)
    finally:
        await session.cleanup()


class WorkerControl:
    """Host-created endpoint identity only: this fixture never executes work."""


class WorkCheckedTool:
    def __init__(self, previous):
        self.previous = previous


async def construction():
    session = AmplifierSession({
        "session": {"orchestrator": "fixture-loop", "context": "fixture-context"},
        "tools": [{"module": "fixture-tool"}],
    })
    session.coordinator.loader = Loader()
    bridge = install_media_hook_bridge(session.coordinator)
    graph = bridge.begin_participating_graph(
        session, owner="host-adapter-fixture",
        participants=("fixture-loop", "fixture-context", "fixture-tool",
                      "host.children", "host.other"),
    )
    host = graph.participant("host.children")
    graph.participant("host.other")
    await session.initialize()
    return session, bridge, graph, host


async def test_host_created_slot_adapter_seals_and_preserves_actual_identity():
    session, bridge, graph, host = await construction()
    try:
        original = WorkerControl()
        await host.mount("tools", original, name="live_worker")
        # Ordinary saved-settings construction must remain valid before adapters.
        with graph.configure():
            pass
        adapter = WorkCheckedTool(original)
        await host.mount_adapter("tools", adapter, previous=original, name="live_worker")
        receipt = graph.seal()
        assert session.coordinator.get("tools", "live_worker") is adapter
        assert adapter.previous is original
        assert bridge.validate_receipt(receipt)
        evidence = graph.participants_evidence()
        assert [load["module"] for load in evidence["loads"]] == [
            "fixture-loop", "fixture-context", "fixture-tool",
        ]  # No invented configured occurrence for either host endpoint.
        edge = evidence["adapters"][-1]
        assert edge["previous"] is original and edge["installed"] is adapter
        assert edge["participant"] == "host.children"
        assert ("tools", "live_worker") not in graph._slot_sources
        settled = await bridge({"tool_name": "fixture", "tool_call_id": "call",
                                "result": {"success": True, "output": {"preserved": True}}})
        assert settled["complete"] is True
        assert settled["result"]["output"] == {"preserved": True}
    finally:
        await session.cleanup()


async def test_host_created_adapter_chain_survives_owned_same_session_transition():
    session, bridge, graph, host = await construction()
    try:
        original = WorkerControl()
        await host.mount("tools", original, name="live_worker")
        first = WorkCheckedTool(original)
        await host.mount_adapter("tools", first, previous=original, name="live_worker")
        old = graph.seal()
        with graph.transition() as successor:
            second = WorkCheckedTool(first)
            await successor.participant("host.children").mount_adapter(
                "tools", second, previous=first, name="live_worker")
        assert not bridge.validate_receipt(old)
        assert bridge.validate_receipt(bridge.graph_receipt)
        assert session.coordinator.get("tools", "live_worker") is second
        assert second.previous is first
    finally:
        await session.cleanup()


@pytest.mark.parametrize("failure", ["other-owner", "raw-slot", "raw-replacement",
                                     "stale-prior", "unmounted", "configured-remount",
                                     "unmounted-raw-reinsert", "equal-prior",
                                     "wrong-name", "wrong-kind", "foreign-endpoint"])
async def test_host_adapter_rejects_unowned_or_stale_prior_without_replacement(failure):
    session, bridge, graph, host = await construction()
    try:
        original = WorkerControl()
        if failure == "raw-slot":
            await session.coordinator.mount("tools", original, name="live_worker")
        elif failure == "configured-remount":
            original = session.coordinator.get("tools", "configured")
            await host.mount("tools", original, name="configured")
        else:
            await host.mount("tools", original, name="live_worker")
        name = "configured" if failure == "configured-remount" else "live_worker"
        caller, previous = host, original
        if failure == "other-owner":
            caller = graph.participant("host.other")
        elif failure == "raw-replacement":
            previous = WorkerControl()
            await session.coordinator.mount("tools", previous, name=name)
        elif failure == "stale-prior":
            await host.mount("tools", WorkerControl(), name=name)
        elif failure == "unmounted":
            await host.unmount("tools", name=name)
        elif failure == "unmounted-raw-reinsert":
            await host.unmount("tools", name=name)
            await session.coordinator.mount("tools", original, name=name)
        elif failure == "equal-prior":
            class Equal:
                def __eq__(self, other):
                    pytest.fail("Endpoint equality must never be invoked")
            previous = Equal()
        elif failure == "wrong-name":
            name = "another-slot"
        elif failure == "wrong-kind":
            with pytest.raises(ValueError):
                await host.mount_adapter("providers", WorkCheckedTool(original),
                                         previous=original, name=name)
            assert session.coordinator.get("tools", name) is original
            return
        elif failure == "foreign-endpoint":
            other, _, _, other_host = await construction()
            try:
                previous = WorkerControl()
                await other_host.mount("tools", previous, name=name)
            finally:
                await other.cleanup()
        before = session.coordinator.get("tools", name)
        edges_before = graph.participants_evidence()["adapters"]
        with pytest.raises(ValueError):
            await caller.mount_adapter("tools", WorkCheckedTool(previous),
                                       previous=previous, name=name)
        assert session.coordinator.get("tools", name) is before
        assert graph.participants_evidence()["adapters"] == edges_before
        with pytest.raises(ValueError):
            graph.seal()
    finally:
        await session.cleanup()


@pytest.mark.parametrize("failure", ["sealed", "active-request", "open-request",
                                     "cleanup", "reconstructed", "invalid"])
async def test_host_adapter_refuses_inactive_or_nonidle_graph_before_native_mount(failure):
    session, bridge, graph, host = await construction()
    replacement = None
    try:
        original = WorkerControl()
        await host.mount("tools", original, name="live_worker")
        if failure == "cleanup":
            await session.cleanup()
        elif failure == "reconstructed":
            replacement = AmplifierSession(session.config)
            graph.reconstruct(replacement, participants=graph.participants)
        elif failure == "invalid":
            with pytest.raises(RuntimeError):
                with graph.configure():
                    raise RuntimeError("Actual construction failed")
        elif failure == "open-request":
            pass
        else:
            graph.seal()
        before = session.coordinator.get("tools", "live_worker")

        async def refuse():
            with pytest.raises(ValueError):
                await host.mount_adapter("tools", WorkCheckedTool(original),
                                         previous=original, name="live_worker")
            assert session.coordinator.get("tools", "live_worker") is before
        if failure in ("active-request", "open-request"):
            with bridge.request_scope():
                await refuse()
        else:
            await refuse()
    finally:
        if replacement is not None:
            await replacement.cleanup()
        await session.cleanup()


async def test_host_adapter_refuses_during_actual_posthook_callback():
    session, bridge, graph, host = await construction()
    try:
        original = WorkerControl()
        await host.mount("tools", original, name="live_worker")
        calls = []

        async def callback(event, data):
            with pytest.raises(ValueError):
                await host.mount_adapter("tools", WorkCheckedTool(original),
                                         previous=original, name="live_worker")
            calls.append(session.coordinator.get("tools", "live_worker"))
            return HookResult()

        host.hooks.register("tool:post", callback, name="active-host-adapter")
        graph.seal()
        await bridge({"tool_name": "fixture", "tool_call_id": "call", "result": {}})
        assert calls == [original]
    finally:
        await session.cleanup()


async def test_host_adapter_suspend_restore_retains_exact_endpoint_and_owner():
    session, bridge, graph, host = await construction()
    try:
        original = WorkerControl()
        await host.mount("tools", original, name="live_worker")
        adapter = WorkCheckedTool(original)
        await host.mount_adapter("tools", adapter, previous=original, name="live_worker")
        graph.seal()
        lifecycle = media_mount_lifecycle(host)
        with graph.transition() as suspended:
            token = await lifecycle.suspend("tools", name="live_worker")
        assert bridge.validate_receipt(bridge.graph_receipt)
        with suspended.transition():
            assert await lifecycle.restore(token) is adapter
            second = WorkCheckedTool(adapter)
            # The session-scoped host view resolves the actual successor issuer.
            await host.mount_adapter("tools", second, previous=adapter, name="live_worker")
        assert session.coordinator.get("tools", "live_worker") is second
        assert bridge.validate_receipt(bridge.graph_receipt)
        assert ("tools", "live_worker") not in bridge._graph._slot_sources
    finally:
        await session.cleanup()


@pytest.mark.parametrize("failure", ["raise", "cancel", "wrong-slot", "cleanup", "replace"])
async def test_host_adapter_failed_or_changed_await_never_records_success(failure, monkeypatch):
    """Controlled await faults supplement real native construction, not replace it."""
    session, bridge, graph, host = await construction()
    original = WorkerControl()
    await host.mount("tools", original, name="live_worker")
    adapter = WorkCheckedTool(original)
    real_wait = MediaMountLifecycle._await_tracked

    async def changed_wait(current, operation):
        if failure == "raise":
            operation.close()
            raise RuntimeError("Native operation unavailable")
        if failure == "cancel":
            operation.close()
            raise asyncio.CancelledError()
        if failure == "wrong-slot":
            operation.close()
            # Core may apply the mount when creating its awaitable. Replace the
            # actual endpoint, rather than assuming an unawaited call had no effect.
            await session.coordinator.mount("tools", WorkerControl(), name="live_worker")
            return None
        result = await real_wait(current, operation)
        if failure == "cleanup":
            await session.cleanup()
        else:
            await host.mount("tools", WorkerControl(), name="live_worker")
        return result

    monkeypatch.setattr(MediaMountLifecycle, "_await_tracked", staticmethod(changed_wait))
    try:
        with pytest.raises((RuntimeError, ValueError, asyncio.CancelledError)):
            await host.mount_adapter("tools", adapter, previous=original, name="live_worker")
        assert graph.participants_evidence()["adapters"] == ()
        with pytest.raises(ValueError):
            graph.seal()
    finally:
        await session.cleanup()


async def test_host_adapter_pending_native_work_blocks_concurrent_edge(monkeypatch):
    session, bridge, graph, host = await construction()
    original = WorkerControl()
    await host.mount("tools", original, name="live_worker")
    adapter = WorkCheckedTool(original)
    started, release = asyncio.Event(), asyncio.Event()
    real_wait = MediaMountLifecycle._await_tracked

    async def delayed_wait(current, operation):
        async def delayed():
            started.set()
            await release.wait()
            return await operation
        return await real_wait(current, delayed())

    monkeypatch.setattr(MediaMountLifecycle, "_await_tracked", staticmethod(delayed_wait))
    pending = asyncio.create_task(
        host.mount_adapter("tools", adapter, previous=original, name="live_worker"))
    try:
        await asyncio.wait_for(started.wait(), 2)
        # Native dispatch may already have installed the adapter; it is not yet
        # acknowledged by a successful issuer edge while its waiter is pending.
        before = session.coordinator.get("tools", "live_worker")
        with pytest.raises(ValueError, match="idle"):
            await host.mount_adapter("tools", WorkCheckedTool(original),
                                     previous=original, name="live_worker")
        assert session.coordinator.get("tools", "live_worker") is before
        assert graph.participants_evidence()["adapters"] == ()
        release.set()
        await pending
        assert session.coordinator.get("tools", "live_worker") is adapter
        graph.seal()
        assert bridge.validate_receipt(bridge.graph_receipt)
    finally:
        release.set()
        await pending
        await session.cleanup()