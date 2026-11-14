"""Actual Core fixtures; private _observe checks only a PARTIAL callback witness.

The public ABI remains incomplete pending an enforceable graph contract.
"""

import asyncio
from copy import deepcopy

import pytest
from amplifier_core import AmplifierSession, HookResult, ModuleCoordinator

from amplifier_foundation.session.media_hook_bridge import install_media_hook_bridge


def event(value=None, call_id="call"):
    if value is None:
        value = {"success": True, "output": {"experimental.media.v1": {"refs": ["staged-fixture"]}}}
    return {"tool_name": "fixture", "tool_call_id": call_id, "result": value}


class CoordinatorObserver:
    def __init__(self, coordinator):
        self.original = coordinator
        self.processed = []

    def __getattr__(self, name):
        return getattr(self.original, name)

    async def process_hook_result(self, result, event_name, name):
        self.processed.append((result, event_name, name))
        return await self.original.process_hook_result(result, event_name, name)


class HooksObserver:
    def __init__(self, hooks):
        self.original = hooks
        self.emitted = []

    async def emit(self, name, data):
        self.emitted.append((name, deepcopy(data)))
        return await self.original.emit(name, data)


@pytest.mark.asyncio
@pytest.mark.parametrize("kind,expected", [
    ("continue", {"original": True}), ("empty", None), ("null", None),
    ("replace", "replacement"), ("list", ["text"]), ("output_removed", {"success": True}),
    ("inplace_delete", None), ("inplace_output", {"original": True, "output": {}}),
    ("data_on_continue", "declared"), ("deny", None),
])
async def test_effective_output_table_and_exact_once(kind, expected):
    coordinator = ModuleCoordinator()
    bridge = install_media_hook_bridge(coordinator)
    calls = []

    async def handler(name, data):
        calls.append(name)
        if kind == "empty":
            return HookResult(action="modify", data={})
        if kind == "null":
            return HookResult(action="modify", data={"result": None})
        if kind == "replace":
            return HookResult(action="modify", data={"result": "replacement"})
        if kind == "list":
            return HookResult(action="modify", data={"result": ["text"]})
        if kind == "output_removed":
            return HookResult(action="modify", data={"result": {"success": True}})
        if kind == "inplace_delete":
            del data["result"]
        if kind == "inplace_output":
            data["result"]["output"] = {}
        if kind == "data_on_continue":
            return HookResult(data={"result": "declared"})
        return HookResult(action="deny" if kind == "deny" else "continue")

    bridge.hooks.register("tool:post", handler, name="handler")
    hooks = HooksObserver(coordinator.hooks)
    observer = CoordinatorObserver(coordinator)
    result = await bridge._observe(event({"original": True}), hooks=hooks, coordinator=observer)
    assert result["complete"] is True
    assert result["result"] == expected
    assert len(calls) == len(hooks.emitted) == len(observer.processed) == 1
    assert isinstance(result["hook_result"], HookResult)
    assert hooks.emitted[0][0] == observer.processed[0][1] == "tool:post"
    assert observer.processed[0][2] == "fixture"


@pytest.mark.asyncio
async def test_null_then_injection_preserves_null_and_injection_processing():
    coordinator = ModuleCoordinator()
    messages = []

    class Context:
        async def add_message(self, message):
            messages.append(message)

    await coordinator.mount("context", Context())
    bridge = install_media_hook_bridge(coordinator)
    bridge.hooks.register("tool:post", lambda e, d: HookResult(action="modify", data={"result": None}), name="null")
    bridge.hooks.register("tool:post", lambda e, d: HookResult(action="inject_context", context_injection="remember"), name="inject")
    result = await bridge._observe(event())
    assert result["complete"] is True
    assert result["result"] is None
    # Core consumes the injection and returns the actual processed Continue.
    assert result["hook_result"].action == "continue"
    assert len(messages) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["throw", "mapping", "none", "malformed"])
async def test_swallowed_failure_or_non_hook_result_cannot_qualify(kind):
    coordinator = ModuleCoordinator()
    bridge = install_media_hook_bridge(coordinator)
    later = []

    def handler(e, d):
        if kind == "throw":
            raise RuntimeError("fixture callback failure")
        if kind == "mapping":
            return {"action": "continue"}
        if kind == "malformed":
            return object()
        return None

    bridge.hooks.register("tool:post", handler, name="failing")
    bridge.hooks.register("tool:post", lambda e, d: later.append(e) or HookResult(), name="later")
    result = await bridge._observe(event())
    assert result["complete"] is False
    assert result["result"] is None
    # Unparseable objects are a native Deny, while callback errors are logged
    # and skipped. The wrapper must not change either Core behavior.
    assert later == ([] if kind == "malformed" else ["tool:post"])


@pytest.mark.asyncio
async def test_priority_deny_and_registration_unregistration_remain_native():
    coordinator = ModuleCoordinator()
    bridge = install_media_hook_bridge(coordinator)
    order = []
    def handler(label, action="continue"):
        return lambda e, d: order.append(label) or HookResult(action=action)
    remove = bridge.hooks.register("tool:post", handler("removed"), -5, "duplicate")
    bridge.hooks.register("tool:post", handler("later"), 10, "later")
    bridge.hooks.on("tool:post", handler("deny", "deny"), 0, "deny")
    bridge.hooks.register("tool:post", handler("first"), -1, "first")
    remove()
    remove()  # Exact native unregistration handles are idempotent.
    assert bridge.hooks.list_handlers() == coordinator.hooks.list_handlers()
    result = await bridge._observe(event())
    assert result["complete"] is True and result["result"] is None
    assert order == ["first", "deny"]
    bridge.hooks.unregister("deny")
    result = await bridge._observe(event())
    assert result["complete"] is True
    assert order == ["first", "deny", "first", "later"]


@pytest.mark.asyncio
async def test_raw_preexisting_and_late_registration_refuse_qualification():
    coordinator = ModuleCoordinator()
    remove = coordinator.hooks.register("tool:post", lambda e, d: HookResult(), name="raw")
    with pytest.raises(ValueError, match="before tool:post"):
        install_media_hook_bridge(coordinator)
    remove()
    bridge = install_media_hook_bridge(coordinator)
    bridge.hooks.register("tool:post", lambda e, d: HookResult(), name="covered")
    remove = coordinator.hooks.register("tool:post", lambda e, d: HookResult(), name="raw")
    result = await bridge._observe(event())
    assert not result["complete"] and result["result"] is None
    remove()
    assert (await bridge._observe(event()))["complete"]
    # A raw same-name substitution cannot masquerade as the wrapped callback.
    coordinator.hooks.unregister("covered")
    coordinator.hooks.register("tool:post", lambda e, d: HookResult(), name="covered")
    assert not (await bridge._observe(event()))["complete"]


@pytest.mark.asyncio
async def test_parallel_and_nested_emissions_isolate_witnesses():
    coordinator = ModuleCoordinator()
    bridge = install_media_hook_bridge(coordinator)
    calls = []
    async def handler(e, d):
        identity = d["tool_call_id"]
        calls.append(identity)
        await asyncio.sleep(0)
        if identity == "outer":
            nested = await bridge._observe(event({"nested": True}, "inner"))
            assert nested["complete"] and nested["result"] == {"call": "inner"}
        return HookResult(action="modify", data={"result": {"call": identity}})
    bridge.hooks.register("tool:post", handler, name="nested")
    results = await asyncio.gather(*(bridge._observe(event(call_id=i)) for i in ("outer", "parallel")))
    assert all(r["complete"] for r in results)
    assert [r["result"] for r in results] == [{"call": "outer"}, {"call": "parallel"}]
    assert sorted(calls) == ["inner", "outer", "parallel"]


@pytest.mark.asyncio
async def test_cancelled_owned_emission_does_not_leak_to_next_call():
    coordinator = ModuleCoordinator()
    bridge = install_media_hook_bridge(coordinator)
    started = asyncio.Event()
    finished = asyncio.Event()
    async def handler(e, d):
        if d["tool_call_id"] == "cancel":
            started.set()
            try:
                await asyncio.Event().wait()
            finally:
                finished.set()
        return HookResult()
    bridge.hooks.register("tool:post", handler, name="cancel")
    task = asyncio.create_task(bridge._observe(event(call_id="cancel")))
    await asyncio.wait_for(started.wait(), 2)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    await asyncio.wait_for(finished.wait(), 2)
    assert (await bridge._observe(event(call_id="next")))["complete"] is True


@pytest.mark.asyncio
async def test_supplied_coordinator_denial_is_not_bypassed():
    coordinator = ModuleCoordinator()
    bridge = install_media_hook_bridge(coordinator)
    class Denial(CoordinatorObserver):
        async def process_hook_result(self, result, event_name, name):
            self.processed.append(name)
            return HookResult(action="deny", reason="attributed denial")
    wrapper = Denial(coordinator)
    result = await bridge._observe(event(), coordinator=wrapper)
    assert result["hook_result"].reason == "attributed denial"
    assert result["result"] is None and result["complete"]
    assert wrapper.processed == ["fixture"]
    with pytest.raises(ValueError, match="does not belong"):
        await bridge._observe(event(), coordinator=ModuleCoordinator())


@pytest.mark.asyncio
async def test_real_rust_session_loader_mount_ready_and_late_host_registration():
    session = AmplifierSession({"session": {"orchestrator": "fixture-loop", "context": "fixture-context"},
                                "hooks": [{"module": "fixture-hooks"}]})
    views = []
    hits = []
    class Loader:
        def __init__(self):
            self.queue = []
        async def load(self, module_id, config, *, source_hint=None, coordinator=None):
            views.append(coordinator)
            async def mounted(current):
                views.append(current)
                if module_id == "fixture-loop":
                    await current.mount("orchestrator", object())
                elif module_id == "fixture-context":
                    await current.mount("context", object())
                else:
                    current.get("hooks").register("tool:post", lambda e, d: hits.append("mount") or HookResult(), name="mount")
            if module_id == "fixture-hooks":
                async def ready(current):
                    views.append(current)
                    current.hooks.register("tool:post", lambda e, d: hits.append("ready") or HookResult(), name="ready")
                mounted.__on_session_ready__ = (module_id, ready)
            return mounted
        def enqueue_on_session_ready(self, name, callback):
            self.queue.append((name, callback))
        def get_on_session_ready_queue(self):
            return self.queue
        def clear_on_session_ready_queue(self):
            self.queue.clear()
    session.coordinator.loader = Loader()
    bridge = install_media_hook_bridge(session.coordinator)
    assert install_media_hook_bridge(session.coordinator) is bridge
    await session.initialize()
    assert session.initialized
    assert all(view is bridge.coordinator for view in views)
    bridge.coordinator.hooks.register("tool:post", lambda e, d: hits.append("host") or HookResult(), name="host")
    result = await bridge._observe(event())
    assert result["complete"] is True
    assert hits == ["mount", "ready", "host"]
    assert session.coordinator.get_capability("session.media.tool_post") is bridge
    await session.cleanup()


@pytest.mark.asyncio
async def test_inplace_redaction_chains_through_actual_registry_once():
    coordinator = ModuleCoordinator()
    bridge = install_media_hook_bridge(coordinator)
    seen = []
    def redact(e, d):
        del d["result"]["output"]
        return HookResult()
    def echo(e, d):
        seen.append(deepcopy(d["result"]))
        return HookResult(action="modify", data=d)
    bridge.hooks.register("tool:post", redact, name="redact")
    bridge.hooks.register("tool:post", echo, name="echo")
    result = await bridge._observe(event())
    assert result["complete"] is True
    assert result["result"] == {"success": True}
    assert seen == [{"success": True}]


@pytest.mark.asyncio
async def test_inplace_plus_special_action_preserves_action_but_refuses_media():
    coordinator = ModuleCoordinator()
    bridge = install_media_hook_bridge(coordinator)
    messages = []
    class Context:
        async def add_message(self, message):
            messages.append(message)
    await coordinator.mount("context", Context())
    def handler(e, d):
        del d["result"]
        return HookResult(action="inject_context", context_injection="still process")
    bridge.hooks.register("tool:post", handler, name="special")
    result = await bridge._observe(event())
    assert not result["complete"] and result["result"] is None
    assert len(messages) == 1


@pytest.mark.asyncio
async def test_actual_approval_and_non_post_lifecycle_remain_ordered():
    calls = []
    class Approval:
        async def request_approval(self, *args):
            calls.append("approval")
            return "deny"
    coordinator = ModuleCoordinator(approval_system=Approval())
    bridge = install_media_hook_bridge(coordinator)
    bridge.hooks.register("session:start", lambda e, d: calls.append("lifecycle") or HookResult(), name="life")
    await bridge.hooks.emit("session:start", {})
    bridge.hooks.register("tool:post", lambda e, d: calls.append("ask") or HookResult(
        action="ask_user", approval_prompt="fixture?"), name="ask")
    bridge.hooks.register("tool:post", lambda e, d: calls.append("later") or HookResult(), name="later")
    result = await bridge._observe(event())
    assert calls == ["lifecycle", "ask", "later", "approval"]
    assert result["hook_result"].action == "deny"
    assert result["result"] is None and result["complete"] is True


@pytest.mark.asyncio
async def test_mid_emit_registration_change_and_serialize_failure_are_incomplete():
    coordinator = ModuleCoordinator()
    bridge = install_media_hook_bridge(coordinator)
    def changing(e, d):
        remove = bridge.hooks.register("tool:post", lambda e, d: HookResult(), name="temporary")
        remove()
        return HookResult()
    remove = bridge.hooks.register("tool:post", changing, name="changing")
    assert not (await bridge._observe(event()))["complete"]
    remove()
    bridge.hooks.register("tool:post", lambda e, d: HookResult(action="modify", data={"result": object()}), name="bad")
    result = await bridge._observe(event())
    assert not result["complete"] and result["result"] is None


@pytest.mark.asyncio
async def test_wrong_emitter_and_empty_chain_foreign_emitter_do_not_self_certify():
    coordinator = ModuleCoordinator()
    bridge = install_media_hook_bridge(coordinator)
    foreign = ModuleCoordinator()
    result = await bridge._observe(event(), hooks=foreign.hooks)
    assert not result["complete"] and result["result"] is None
    bridge.hooks.register("tool:post", lambda e, d: HookResult(), name="actual")
    result = await bridge._observe(event(), hooks=foreign.hooks)
    assert not result["complete"] and result["result"] is None
    assert (await bridge._observe(event(), hooks=bridge.hooks))["complete"]


@pytest.mark.asyncio
async def test_loader_replacement_and_duplicate_emission_fail_closed():
    coordinator = ModuleCoordinator()
    bridge = install_media_hook_bridge(coordinator)
    calls = []
    bridge.hooks.register("tool:post", lambda e, d: calls.append(e) or HookResult(), name="once")
    class Duplicate:
        async def emit(self, name, data):
            await coordinator.hooks.emit(name, data)
            return await coordinator.hooks.emit(name, data)
    result = await bridge._observe(event(), hooks=Duplicate())
    assert not result["complete"] and calls == ["tool:post", "tool:post"]
    # The bridge never retries even when the supplied wrapper violates its
    # one-delegation contract. The invalid wrapper is not used again.
    coordinator.loader = object()
    assert not bridge.readiness()["registration_consistent"]
    assert not (await bridge._observe(event()))["complete"]


@pytest.mark.asyncio
@pytest.mark.parametrize("replacement", [None, {}, "redacted"])
async def test_supplied_emitter_final_modify_is_authoritative(replacement):
    coordinator = ModuleCoordinator()
    bridge = install_media_hook_bridge(coordinator)
    bridge.hooks.register("tool:post", lambda e, d: HookResult(), name="covered")
    class Redaction:
        async def emit(self, name, data):
            await coordinator.hooks.emit(name, data)
            return HookResult(action="modify", data={"result": replacement})
    result = await bridge._observe(event(), hooks=Redaction())
    assert result["complete"] and result["result"] == replacement


@pytest.mark.asyncio
@pytest.mark.parametrize("bad", [float("nan"), object(), ("tuple",), {1: "key"}])
async def test_synthesized_inplace_modify_rejects_lossy_json(bad):
    coordinator = ModuleCoordinator()
    bridge = install_media_hook_bridge(coordinator)
    def mutate(e, data):
        data["result"] = {"bad": bad}
        return HookResult()
    bridge.hooks.register("tool:post", mutate, name="mutate")
    result = await bridge._observe(event())
    assert result["complete"] is False and result["result"] is None


@pytest.mark.asyncio
async def test_public_boundary_cannot_self_certify_even_fully_observed_callbacks():
    coordinator = ModuleCoordinator()
    bridge = install_media_hook_bridge(coordinator)
    calls = []
    bridge.hooks.register("tool:post", lambda e, d: calls.append(e) or HookResult(), name="covered")
    observer = CoordinatorObserver(coordinator)
    result = await bridge(event(), coordinator=observer)
    assert result == {"hook_result": observer.processed[0][0], "result": None, "complete": False}
    assert calls == ["tool:post"] and len(observer.processed) == 1


@pytest.mark.asyncio
async def test_transient_raw_self_removing_failure_is_real_core_boundary():
    coordinator = ModuleCoordinator()
    bridge = install_media_hook_bridge(coordinator)
    calls = []
    bridge.hooks.register("tool:post", lambda e, d: calls.append("covered") or HookResult(), name="covered")
    def raw(e, d):
        calls.append("raw")
        coordinator.hooks.unregister("raw")
        raise RuntimeError("raw self-removing error")
    class Transient:
        async def emit(self, name, data):
            coordinator.hooks.register(name, raw, priority=-100, name="raw")
            return await coordinator.hooks.emit(name, data)
    result = await bridge(event(), hooks=Transient())
    assert calls == ["raw", "covered"]
    assert bridge.readiness()["registration_consistent"] is True
    assert result["complete"] is False and result["result"] is None


@pytest.mark.asyncio
async def test_public_boundary_preserves_wrapper_redaction_without_qualification():
    coordinator = ModuleCoordinator()
    bridge = install_media_hook_bridge(coordinator)
    bridge.hooks.register("tool:post", lambda e, d: HookResult(), name="covered")
    class Redaction:
        async def emit(self, name, data):
            await coordinator.hooks.emit(name, data)
            return HookResult(action="modify", data={"result": None})
    result = await bridge(event(), hooks=Redaction())
    assert result["hook_result"].data == {"result": None}
    assert result["complete"] is False and result["result"] is None
