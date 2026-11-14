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
    result = await bridge(event(), hooks=bridge.hooks, coordinator=bridge.coordinator)
    assert result["complete"] is False and result["result"] is None
    assert isinstance(result["hook_result"], HookResult)
    assert calls == ["tool:post"]
    with pytest.raises(ValueError, match="unregistered"):
        await bridge(event(), coordinator=CoordinatorObserver(coordinator))


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
    # Preserve the counterexample in the explicitly private partial mechanism.
    partial = await bridge._observe(event(), hooks=Transient())
    assert partial["complete"] is True
    assert calls == ["raw", "covered"]
    assert bridge.readiness()["registration_consistent"] is True
    with pytest.raises(ValueError, match="unregistered"):
        await bridge(event(), hooks=Transient())
    result = await bridge(event())
    assert not result["complete"] and result["result"] is None


@pytest.mark.asyncio
async def test_public_boundary_rejects_unenrolled_wrapper_without_executing_it():
    coordinator = ModuleCoordinator()
    bridge = install_media_hook_bridge(coordinator)
    bridge.hooks.register("tool:post", lambda e, d: HookResult(), name="covered")
    class Redaction:
        async def emit(self, name, data):
            pytest.fail("arbitrary supplied emitter must not run")
    with pytest.raises(ValueError, match="unregistered"):
        await bridge(event(), hooks=Redaction())


ADAPTER = {"component": "loop-streaming", "adapter": "attributed-v1",
           "contract": 1, "module": "amplifier_module_loop_streaming.dispatch"}


class RouteToken:
    """Identity only: no arbitrary wrapper behavior may be invoked."""
    def __getattr__(self, name):
        raise AssertionError(f"wrapper attribute must not be read: {name}")


def route_fixture():
    session = AmplifierSession(
        {"session": {"orchestrator": "fixture-loop", "context": "fixture-context"}},
        session_id="controlled-route-session")
    return session.coordinator, install_media_hook_bridge(session.coordinator)


def call_source(coordinator, call="inner", parent="outer"):
    return {"session_id": coordinator.session_id, "run_id": "controlled-run",
            "parent_tool_call_id": parent, "tool_call_id": call,
            "tool_name": "fixture", "request_id": None}


def enroll(bridge, coordinator, *, call="inner", parent="outer", parent_hooks=None,
           parent_coordinator=None, observation=None):
    hooks, attributed = RouteToken(), RouteToken()
    observation = {} if observation is None else observation
    lease = bridge.enroll_attributed_route(
        hooks=hooks, coordinator=attributed,
        parent_hooks=bridge.hooks if parent_hooks is None else parent_hooks,
        parent_coordinator=bridge.coordinator if parent_coordinator is None else parent_coordinator,
        source=call_source(coordinator, call, parent), observation=observation,
        source_identity=dict(ADAPTER))
    return lease, hooks, attributed, observation


async def partial_enrolled(bridge, data, hooks, coordinator):
    # A controlled candidate probe, never the public media acceptance contract.
    emitter, processor = bridge._bound_route(data, hooks, coordinator)
    return await bridge._observe(data, hooks=emitter, coordinator=processor)


@pytest.mark.asyncio
async def test_enrolled_route_uses_captured_rust_endpoints_and_no_wrapper_methods():
    coordinator, bridge = route_fixture()
    native_events = []
    bridge.hooks.register("tool:post", lambda e, d: native_events.append(deepcopy(d)) or HookResult(),
                          name="actual-native-callback")
    lease, hooks, attributed, observation = enroll(bridge, coordinator)
    assert bridge._native_emit.__self__ is coordinator.hooks
    assert bridge._native_process.__self__ is coordinator
    with lease:
        result = await bridge(event(call_id="inner"), hooks=hooks, coordinator=attributed)
        assert result["complete"] is False and result["result"] is None
        assert observation["post_seen"] is True
        assert observation["post_result"] == event()["result"]
    assert len(native_events) == 1
    assert native_events[0]["dispatch_source"] == call_source(coordinator)
    assert native_events[0]["parent_tool_call_id"] == "outer"
    with pytest.raises(ValueError, match="unregistered"):
        await bridge(event(call_id="inner"), hooks=hooks, coordinator=attributed)


@pytest.mark.asyncio
@pytest.mark.parametrize("coordinator_parent_is_root", [True, False])
async def test_nested_route_enrichment_and_observation_match_separate_parent_chains(coordinator_parent_is_root):
    coordinator, bridge = route_fixture()
    seen = []
    class Approval:
        async def request_approval(self, *args):
            # Both hook observers unwind before root processing begins.
            assert outer_observation["post_seen"] and inner_observation["post_seen"]
            return "deny"
    coordinator.approval_system = Approval()
    bridge.hooks.register("tool:post", lambda e, d: seen.append(deepcopy(d)) or HookResult(
        action="ask_user", approval_prompt="actual denial"), name="approval")
    outer, outer_hooks, outer_coord, outer_observation = enroll(
        bridge, coordinator, call="outer", parent="root-parent")
    with outer:
        inner, inner_hooks, inner_coord, inner_observation = enroll(
            bridge, coordinator, call="inner", parent="outer", parent_hooks=outer_hooks,
            parent_coordinator=bridge.coordinator if coordinator_parent_is_root else outer_coord)
        with inner:
            result = await partial_enrolled(bridge, event(call_id="inner"), inner_hooks, inner_coord)
    assert result["complete"] and result["result"] is None
    assert result["hook_result"].action == "deny"
    assert len(seen) == 1
    # Existing wrapper order is inner->outer enrichment (outer overwrites).
    assert seen[0]["dispatch_source"] == call_source(coordinator, "outer", "root-parent")
    assert seen[0]["parent_tool_call_id"] == "root-parent"
    assert inner_observation["denied"] is True
    assert ("denied" in outer_observation) is not coordinator_parent_is_root


@pytest.mark.asyncio
async def test_enrolled_null_then_injection_observer_never_resurrects():
    coordinator, bridge = route_fixture()
    messages = []
    class Context:
        async def add_message(self, message):
            messages.append(message)
    await coordinator.mount("context", Context())
    bridge.hooks.register("tool:post", lambda e, d: HookResult(action="modify", data={"result": None}), name="null")
    bridge.hooks.register("tool:post", lambda e, d: HookResult(
        action="inject_context", context_injection="actual injection"), name="inject")
    lease, hooks, attributed, observation = enroll(bridge, coordinator)
    with lease:
        result = await partial_enrolled(bridge, event(call_id="inner"), hooks, attributed)
    assert result["complete"] and result["result"] is None
    assert observation == {"post_seen": True, "post_result": None}
    assert result["hook_result"].action == "continue" and len(messages) == 1


@pytest.mark.asyncio
async def test_enrollment_tokens_are_pair_bound_scoped_and_not_a_descriptor_grant():
    coordinator, bridge = route_fixture()
    calls = []
    bridge.hooks.register("tool:post", lambda e, d: calls.append(e) or HookResult(), name="covered")
    one, h1, c1, _ = enroll(bridge, coordinator, call="one")
    two, h2, c2, _ = enroll(bridge, coordinator, call="two")
    with pytest.raises(ValueError, match="not active"):
        await bridge(event(call_id="one"), hooks=h1, coordinator=c1)
    with one, two:
        with pytest.raises(ValueError, match="same enrolled pair"):
            await bridge(event(call_id="one"), hooks=h1, coordinator=c2)
        with pytest.raises(ValueError, match="does not match"):
            await bridge(event(call_id="wrong"), hooks=h1, coordinator=c1)
        with pytest.raises(ValueError, match="already enrolled"):
            bridge.enroll_attributed_route(
                hooks=h1, coordinator=RouteToken(), parent_hooks=bridge.hooks,
                parent_coordinator=bridge.coordinator, source=call_source(coordinator),
                observation={}, source_identity=dict(ADAPTER))
        assert not (await bridge(event(call_id="one"), hooks=h1, coordinator=c1))["complete"]
        assert not (await bridge(event(call_id="two"), hooks=h2, coordinator=c2))["complete"]
    assert calls == ["tool:post", "tool:post"]
    assert not bridge._routes


@pytest.mark.asyncio
async def test_source_detached_and_parent_revocation_invalidates_descendant():
    coordinator, bridge = route_fixture()
    native = []
    bridge.hooks.register("tool:post", lambda e, d: native.append(deepcopy(d)) or HookResult(), name="actual")
    source = call_source(coordinator)
    identity = dict(ADAPTER)
    hooks, attributed, observation = RouteToken(), RouteToken(), {}
    outer = bridge.enroll_attributed_route(
        hooks=hooks, coordinator=attributed, parent_hooks=bridge.hooks,
        parent_coordinator=bridge.coordinator, source=source,
        observation=observation, source_identity=identity)
    source["parent_tool_call_id"] = "mutated-after-enrollment"
    identity["contract"] = False
    with outer:
        await bridge(event(call_id="inner"), hooks=hooks, coordinator=attributed)
        child, child_hooks, child_coord, _ = enroll(
            bridge, coordinator, call="child", parent_hooks=hooks)
        with child:
            outer.revoke()
            with pytest.raises(ValueError, match="unregistered|not active"):
                await bridge(event(call_id="child"), hooks=child_hooks, coordinator=child_coord)
    assert len(native) == 1 and native[0]["parent_tool_call_id"] == "outer"


@pytest.mark.asyncio
async def test_mid_emit_revoke_preserves_processing_once_but_partial_witness_is_false():
    coordinator, bridge = route_fixture()
    calls = []
    class Approval:
        async def request_approval(self, *args):
            calls.append("process")
            return "deny"
    coordinator.approval_system = Approval()
    lease, hooks, attributed, observation = enroll(bridge, coordinator)
    async def revoke(e, d):
        calls.append("emit")
        lease.revoke()
        await asyncio.sleep(0)
        return HookResult(action="ask_user", approval_prompt="still process actual result")
    bridge.hooks.register("tool:post", revoke, name="revoke")
    with lease:
        result = await partial_enrolled(bridge, event(call_id="inner"), hooks, attributed)
    assert calls == ["emit", "process"]
    assert not result["complete"] and result["result"] is None
    assert observation == {"post_seen": True, "post_result": None, "denied": True}


@pytest.mark.asyncio
async def test_enrolled_parallel_and_cancellation_leave_no_route_or_frame_leak():
    coordinator, bridge = route_fixture()
    calls = []
    started, finished = asyncio.Event(), asyncio.Event()
    async def handler(e, d):
        calls.append(d["tool_call_id"])
        if d["tool_call_id"] == "cancel":
            started.set()
            try:
                await asyncio.Event().wait()
            finally:
                finished.set()
        await asyncio.sleep(0)
        return HookResult()
    bridge.hooks.register("tool:post", handler, name="actual")
    async def run(call):
        lease, hooks, attributed, observation = enroll(bridge, coordinator, call=call)
        with lease:
            result = await bridge(event(call_id=call), hooks=hooks, coordinator=attributed)
            assert observation["post_seen"]
            return result
    cancelled = asyncio.create_task(run("cancel"))
    await asyncio.wait_for(started.wait(), 2)
    results = await asyncio.gather(run("one"), run("two"))
    cancelled.cancel()
    with pytest.raises(asyncio.CancelledError):
        await cancelled
    await asyncio.wait_for(finished.wait(), 2)
    assert all(not result["complete"] and result["result"] is None for result in results)
    assert sorted(calls) == ["cancel", "one", "two"] and not bridge._routes


@pytest.mark.asyncio
@pytest.mark.parametrize("bad", ["session", "missing", "extra", "empty", "request", "adapter", "boolean"])
async def test_invalid_dispatcher_descriptor_refuses_before_any_emission(bad):
    coordinator, bridge = route_fixture()
    source, identity = call_source(coordinator), dict(ADAPTER)
    if bad == "session":
        source["session_id"] = "other-session"
    elif bad == "missing":
        del source["run_id"]
    elif bad == "extra":
        source["authority"] = True
    elif bad == "empty":
        source["parent_tool_call_id"] = ""
    elif bad == "request":
        source["request_id"] = 42
    elif bad == "adapter":
        identity["module"] = "arbitrary.wrapper"
    elif bad == "boolean":
        identity["contract"] = True
    with pytest.raises(ValueError):
        bridge.enroll_attributed_route(
            hooks=RouteToken(), coordinator=RouteToken(), parent_hooks=bridge.hooks,
            parent_coordinator=bridge.coordinator, source=source, observation={},
            source_identity=identity)
    assert not bridge._routes and not coordinator.hooks.list_handlers("tool:post")["tool:post"]


@pytest.mark.asyncio
async def test_revoked_parent_generation_cannot_rebind_old_child_when_tokens_reused():
    coordinator, bridge = route_fixture()
    actual = []
    bridge.hooks.register("tool:post", lambda e, d: actual.append(d) or HookResult(), name="covered")
    parent, h1, c1, _ = enroll(bridge, coordinator, call="parent")
    with parent:
        child, h2, c2, _ = enroll(
            bridge, coordinator, call="child", parent_hooks=h1, parent_coordinator=c1)
        with child:
            parent.revoke()
            replacement = bridge.enroll_attributed_route(
                hooks=h1, coordinator=c1, parent_hooks=bridge.hooks,
                parent_coordinator=bridge.coordinator,
                source=call_source(coordinator, "replacement", "different-parent"),
                observation={}, source_identity=dict(ADAPTER))
            with replacement:
                with pytest.raises(ValueError, match="not active"):
                    await bridge(event(call_id="child"), hooks=h2, coordinator=c2)
    assert actual == [] and not bridge._routes


@pytest.mark.asyncio
async def test_observer_clears_result_before_process_for_missing_callback_identity():
    coordinator, bridge = route_fixture()
    bridge.hooks.register("tool:post", lambda e, d: HookResult(), name="covered")
    coordinator.hooks.unregister("covered")
    coordinator.hooks.register("tool:post", lambda e, d: HookResult(
        action="ask_user", approval_prompt="check observer before processing"), name="covered")
    lease, hooks, attributed, observation = enroll(bridge, coordinator)
    class Approval:
        async def request_approval(self, *args):
            assert observation == {"post_seen": True, "post_result": None}
            return "deny"
    coordinator.approval_system = Approval()
    with lease:
        result = await partial_enrolled(bridge, event(call_id="inner"), hooks, attributed)
    assert not result["complete"] and result["result"] is None
    assert observation == {"post_seen": True, "post_result": None, "denied": True}


@pytest.mark.asyncio
async def test_revocation_during_processing_clears_earlier_partial_observer():
    coordinator, bridge = route_fixture()
    lease, hooks, attributed, observation = enroll(bridge, coordinator)
    bridge.hooks.register("tool:post", lambda e, d: HookResult(
        action="ask_user", approval_prompt="revoke during process"), name="ask")
    class Approval:
        async def request_approval(self, *args):
            assert observation["post_result"] == event()["result"]
            lease.revoke()
            return "allow"
    coordinator.approval_system = Approval()
    with lease:
        result = await partial_enrolled(bridge, event(call_id="inner"), hooks, attributed)
    assert not result["complete"] and result["result"] is None
    assert observation["post_result"] is None


def test_spoofed_builtin_names_are_not_supported_core_endpoint_types():
    class FakeRegistry(list):
        emit = list.append
        def list_handlers(self, event):
            return {event: []}
    class FakeCoordinator(list):
        process_hook_result = list.append
        def __init__(self):
            self.hooks = FakeRegistry()
        def get_capability(self, name):
            return None
    FakeRegistry.__module__, FakeRegistry.__name__ = "builtins", "RustHookRegistry"
    FakeCoordinator.__module__, FakeCoordinator.__name__ = "builtins", "RustCoordinator"
    with pytest.raises(ValueError, match="actual bound Core Rust endpoints"):
        install_media_hook_bridge(FakeCoordinator())
