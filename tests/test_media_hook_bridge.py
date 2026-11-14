"""Actual Core public positive/negative participating cooperative graph fixtures.

Earlier private _observe tests remain explicitly PARTIAL mechanism checks; they
do not replace public construction/seal/boundary/transition qualification.
"""

import asyncio
from copy import deepcopy
from importlib.metadata import version as package_version

import pytest
from amplifier_core import AmplifierSession, HookResult, ModuleCoordinator

from amplifier_foundation.session.media_hook_bridge import install_media_hook_bridge, media_mount_lifecycle


CORE_ABI = package_version("amplifier-core")
requires_core2 = pytest.mark.skipif(
    CORE_ABI != "2.0.2", reason="cooperative graph semantics require exact Core ABI 2.0.2")


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
    assert result["hook_result"].action == ("continue" if CORE_ABI == "2.0.2" else "inject_context")
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
    assert later == ([] if kind == "malformed" and CORE_ABI == "2.0.2" else ["tool:post"])


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
@requires_core2
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
    assert hits == (["mount", "ready", "host"] if CORE_ABI == "2.0.2" else ["mount", "host"])
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
    assert result["hook_result"].action == ("continue" if CORE_ABI == "2.0.2" else "inject_context")
    assert len(messages) == 1


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
@requires_core2
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


class ParticipatingFixtureLoader:
    def __init__(self, handlers=()):
        self.queue, self.handlers, self.views = [], handlers, []
    async def load(self, module_id, config, *, source_hint=None, coordinator=None):
        self.views.append(coordinator)
        async def mount(current):
            if module_id == "fixture-loop":
                await current.mount("orchestrator", object())
            elif module_id == "fixture-context":
                await current.mount("context", object())
            elif module_id == "fixture-hooks":
                for index, handler in enumerate(self.handlers):
                    current.hooks.register("tool:post", handler, name=f"participant-{index}")
        if module_id == "fixture-hooks":
            async def ready(current):
                current.hooks.register("tool:post", lambda e, d: HookResult(), name="ready")
            mount.__on_session_ready__ = (module_id, ready)
        return mount
    def enqueue_on_session_ready(self, name, callback):
        self.queue.append((name, callback))
    def get_on_session_ready_queue(self):
        return self.queue
    def clear_on_session_ready_queue(self):
        self.queue.clear()


async def positive_graph(handlers=(), late=None, session_config=None):
    session = AmplifierSession(
        {"session": {"orchestrator": "fixture-loop", "context": "fixture-context", **(session_config or {})},
         "hooks": [{"module": "fixture-hooks"}]}, session_id="positive-session")
    session.coordinator.loader = ParticipatingFixtureLoader(handlers)
    bridge = install_media_hook_bridge(session.coordinator)
    graph = bridge.begin_participating_graph(
        session, owner="controlled.public.core2",
        participants=("fixture-loop", "fixture-context", "fixture-hooks", "host.late"))
    await session.initialize()
    graph.participant("host.late").hooks.register(
        "tool:post", late or (lambda e, d: HookResult()), name="late")
    receipt = graph.seal()
    return session, bridge, graph, receipt


@pytest.mark.asyncio
@pytest.mark.parametrize("kind,expected", [
    ("retain", {"success": True, "output": {"experimental.media.v1": {"refs": ["staged-fixture"]}}}),
    ("null", None), ("empty", None), ("string", "redacted"), ("list", ["redacted"]),
    ("inplace", {"success": True}), ("delete", None), ("deny", None),
])
@requires_core2
async def test_public_positive_cooperative_graph_effective_table(kind, expected):
    calls = []
    def policy(e, data):
        calls.append(e)
        if kind == "null":
            return HookResult(action="modify", data={"result": None})
        if kind == "empty":
            return HookResult(action="modify", data={})
        if kind == "string":
            return HookResult(action="modify", data={"result": "redacted"})
        if kind == "list":
            return HookResult(action="modify", data={"result": ["redacted"]})
        if kind == "inplace":
            del data["result"]["output"]
        if kind == "delete":
            del data["result"]
        return HookResult(action="deny" if kind == "deny" else "continue")
    session, bridge, graph, receipt = await positive_graph([policy])
    result = await bridge(event())
    assert bridge.validate_receipt(receipt) is True
    assert result["complete"] is True and result["result"] == expected
    assert calls == ["tool:post"]
    assert all(item.original is not None and item.observed is not None
               and item.participant in graph.participants for item in bridge._expected())
    await session.cleanup()


@pytest.mark.asyncio
@requires_core2
async def test_public_positive_outer_redaction_overrides_surviving_inner_result():
    session, bridge, graph, receipt = await positive_graph(
        [lambda e, d: HookResult(action="modify", data={"result": {"inner": "retained"}})],
        late=lambda e, d: HookResult(action="modify", data={"result": None}))
    result = await bridge(event())
    assert result["complete"] is True and result["result"] is None
    assert bridge.validate_receipt(receipt)
    await session.cleanup()


@pytest.mark.asyncio
@requires_core2
async def test_public_positive_callback_error_is_incomplete_not_original_media():
    def fail(e, d):
        raise RuntimeError("actual participating callback failure")
    session, bridge, graph, receipt = await positive_graph([fail])
    result = await bridge(event())
    assert not result["complete"] and result["result"] is None
    assert bridge.validate_receipt(receipt)  # graph valid, individual chain failed
    await session.cleanup()


@pytest.mark.asyncio
@requires_core2
async def test_graph_late_nonpost_lifecycle_allowed_but_post_mutation_latches_invalid():
    session, bridge, graph, receipt = await positive_graph()
    remove = graph.participant("host.late").hooks.register(
        "orchestrator:steering_injected", lambda e, d: HookResult(), name="badge")
    assert (await bridge(event()))["complete"]
    remove()
    assert bridge.validate_receipt(receipt)
    late = graph.participant("host.late").hooks.register(
        "tool:post", lambda e, d: HookResult(), name="late-extra")
    late()
    assert not bridge.validate_receipt(receipt)
    assert not (await bridge(event()))["complete"]
    with pytest.raises(ValueError, match="cannot seal"):
        graph.seal()
    await session.cleanup()


@pytest.mark.asyncio
@requires_core2
async def test_graph_foreign_receipts_and_config_mutation_cannot_qualify_or_reseal():
    import copy
    session, bridge, graph, receipt = await positive_graph()
    assert not bridge.validate_receipt(object())
    assert not bridge.validate_receipt(copy.copy(receipt))
    session.config["extra"] = {"changed": True}
    assert not bridge.validate_receipt(receipt)
    del session.config["extra"]
    assert not bridge.validate_receipt(receipt)
    assert not (await bridge(event()))["complete"]
    await session.cleanup()


@pytest.mark.asyncio
@requires_core2
async def test_public_positive_nested_enrolled_route_and_revoked_identity():
    session, bridge, graph, receipt = await positive_graph()
    lease, hooks, attributed, observation = enroll(bridge, session.coordinator)
    with lease:
        result = await bridge(event(call_id="inner"), hooks=hooks, coordinator=attributed)
    assert result["complete"] and observation["post_result"] == event()["result"]
    assert bridge.validate_receipt(receipt)
    with pytest.raises(ValueError):
        await bridge(event(call_id="inner"), hooks=hooks, coordinator=attributed)
    await session.cleanup()


@pytest.mark.asyncio
@requires_core2
async def test_public_positive_midchain_graph_invalidation_keeps_effects_once():
    captured = {}
    calls = []
    async def change(e, data):
        calls.append("callback")
        remove = captured["graph"].participant("host.late").hooks.register(
            "tool:post", lambda e, d: HookResult(), name="transient-participant")
        remove()
        await asyncio.sleep(0)
        return HookResult()
    session, bridge, graph, receipt = await positive_graph([change])
    captured["graph"] = graph
    result = await bridge(event())
    assert calls == ["callback"]
    assert not result["complete"] and result["result"] is None
    assert not bridge.validate_receipt(receipt)
    await session.cleanup()


@pytest.mark.asyncio
@requires_core2
async def test_unowned_registration_and_incomplete_construction_refuse_seal():
    session = AmplifierSession(
        {"session": {"orchestrator": "fixture-loop", "context": "fixture-context"}},
        session_id="unowned-session")
    session.coordinator.loader = ParticipatingFixtureLoader()
    bridge = install_media_hook_bridge(session.coordinator)
    graph = bridge.begin_participating_graph(
        session, owner="controlled", participants=("fixture-loop", "fixture-context", "missing.host"))
    await session.initialize()
    with pytest.raises(ValueError, match="incomplete"):
        graph.seal()
    assert not (await bridge(event()))["complete"]
    await session.cleanup()


@pytest.mark.asyncio
@requires_core2
async def test_public_positive_null_then_actual_injection_stays_null():
    messages = []
    class Context:
        async def add_message(self, message):
            messages.append(message)
    # Install the real context endpoint during its participating mount.
    class ContextLoader(ParticipatingFixtureLoader):
        async def load(self, module_id, *args, **kwargs):
            mounted = await super().load(module_id, *args, **kwargs)
            if module_id == "fixture-context":
                async def context(current):
                    await current.mount("context", Context())
                return context
            return mounted
    session = AmplifierSession(
        {"session": {"orchestrator": "fixture-loop", "context": "fixture-context"},
         "hooks": [{"module": "fixture-hooks"}]}, session_id="null-injection-session")
    session.coordinator.loader = ContextLoader([
        lambda e, d: HookResult(action="modify", data={"result": None}),
        lambda e, d: HookResult(action="inject_context", context_injection="retained notice")])
    bridge = install_media_hook_bridge(session.coordinator)
    graph = bridge.begin_participating_graph(
        session, owner="test.null-injection",
        participants=("fixture-loop", "fixture-context", "fixture-hooks"))
    await session.initialize()
    graph.seal()
    result = await bridge(event())
    assert result["complete"] and result["result"] is None
    assert result["hook_result"].action == "continue" and len(messages) == 1
    await session.cleanup()


@pytest.mark.asyncio
@pytest.mark.parametrize("bad", ["raw", "loader", "context", "callback_identity"])
@requires_core2
async def test_public_positive_detected_bypass_or_endpoint_mutation_is_permanent(bad):
    session, bridge, graph, receipt = await positive_graph()
    if bad == "raw":
        session.coordinator.hooks.register("tool:post", lambda e, d: HookResult(), name="raw")
    elif bad == "loader":
        session.coordinator.loader = object()
    elif bad == "context":
        await session.coordinator.mount("context", object())
    else:
        bridge._expected()[0].original = lambda e, d: HookResult()
    result = await bridge(event())
    assert not result["complete"] and result["result"] is None
    assert not bridge.validate_receipt(receipt)
    with pytest.raises(ValueError, match="fresh"):
        bridge.begin_participating_graph(
            session, owner="reseal", participants=("fixture-loop", "fixture-context", "fixture-hooks"))
    await session.cleanup()


@pytest.mark.asyncio
@requires_core2
async def test_failed_ready_and_source_plan_drift_never_issue_receipt():
    class FailedReadyLoader(ParticipatingFixtureLoader):
        async def load(self, module_id, *args, **kwargs):
            mount = await super().load(module_id, *args, **kwargs)
            if module_id == "fixture-hooks":
                async def failed(current):
                    raise RuntimeError("actual ready failure")
                mount.__on_session_ready__ = (module_id, failed)
            return mount
    for fail_ready in (False, True):
        session = AmplifierSession(
            {"session": {"orchestrator": "fixture-loop", "context": "fixture-context"},
             "hooks": [{"module": "fixture-hooks"}]}, session_id="failed-construction")
        session.coordinator.loader = FailedReadyLoader() if fail_ready else ParticipatingFixtureLoader()
        bridge = install_media_hook_bridge(session.coordinator)
        graph = bridge.begin_participating_graph(
            session, owner="failed", participants=("fixture-loop", "fixture-context", "fixture-hooks"))
        if not fail_ready:
            session.config["hooks"][0]["source"] = "different-source"
        await session.initialize()
        with pytest.raises(ValueError):
            graph.seal()
        assert bridge.graph_receipt is None and not (await bridge(event()))["complete"]
        await session.cleanup()


@pytest.mark.asyncio
@requires_core2
async def test_positive_graph_parallel_callbacks_are_independent_and_receipt_reused():
    async def policy(e, data):
        await asyncio.sleep(0)
        return HookResult(action="modify", data={"result": data["tool_call_id"]})
    session, bridge, graph, receipt = await positive_graph([policy])
    result = await asyncio.gather(*(bridge(event(call_id=str(index))) for index in range(4)))
    assert [item["result"] for item in result] == ["0", "1", "2", "3"]
    assert all(item["complete"] for item in result) and bridge.validate_receipt(receipt)
    await session.cleanup()


@pytest.mark.asyncio
@requires_core2
async def test_issuer_reconstructs_distinct_fresh_generation_for_settings_change():
    session, bridge, graph, receipt = await positive_graph()
    replacement = AmplifierSession(
        {"session": {"orchestrator": "fixture-loop", "context": "fixture-context"}},
        session_id="reconstructed-session")
    replacement.coordinator.loader = ParticipatingFixtureLoader()
    new_graph = graph.reconstruct(
        replacement, participants=("fixture-loop", "fixture-context", "host.new-policy"))
    new_bridge = replacement.coordinator.get_capability("session.media.tool_post")
    assert not bridge.validate_receipt(receipt)
    assert not (await bridge(event()))["complete"]
    await replacement.initialize()
    new_graph.participant("host.new-policy").hooks.register(
        "tool:post", lambda e, d: HookResult(action="modify", data={"result": "new-policy"}),
        name="new-policy")
    new_receipt = new_graph.seal()
    assert new_receipt is not receipt and not new_bridge.validate_receipt(receipt)
    assert (await new_bridge(event()))["result"] == "new-policy"
    assert bridge.receipt_status(receipt)["generation"] != new_bridge.receipt_status(new_receipt)["generation"]
    with pytest.raises(ValueError, match="distinct fresh"):
        new_graph.reconstruct(replacement, participants=("fixture-loop", "fixture-context"))
    await replacement.cleanup()
    await session.cleanup()


@pytest.mark.asyncio
@requires_core2
async def test_public_positive_cancel_then_next_call_and_route_release():
    started, finished = asyncio.Event(), asyncio.Event()
    async def policy(e, d):
        if d["tool_call_id"] == "cancel":
            started.set()
            try:
                await asyncio.Event().wait()
            finally:
                finished.set()
        return HookResult()
    session, bridge, graph, receipt = await positive_graph([policy])
    task = asyncio.create_task(bridge(event(call_id="cancel")))
    await asyncio.wait_for(started.wait(), 2)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    await asyncio.wait_for(finished.wait(), 2)
    result = await bridge(event(call_id="next"))
    assert result["complete"] and bridge.validate_receipt(receipt)
    await session.cleanup()


@pytest.mark.asyncio
@requires_core2
async def test_callback_error_cannot_be_healed_by_later_outer_reattachment():
    def error(e, d):
        raise RuntimeError("actual image policy callback failure")
    session, bridge, graph, receipt = await positive_graph([error], late=lambda e, d: HookResult(
        action="modify", data={"result": {"success": True, "output": {"experimental.media.v1": {
            "refs": ["must-not-reappear"]}}}}))
    result = await bridge(event())
    assert not result["complete"] and result["result"] is None
    await session.cleanup()


@pytest.mark.asyncio
@pytest.mark.parametrize("phase", ["pending", "late", "undeclared", "underlying"])
@requires_core2
async def test_ready_queue_work_cannot_be_omitted_from_public_qualification(phase):
    if phase == "pending":
        class PendingLoader(ParticipatingFixtureLoader):
            def clear_on_session_ready_queue(self):
                super().clear_on_session_ready_queue()
                # Public underlying loader path leaves unexecuted ready work.
                self.queue.append(("fixture-loop", lambda c: None))
        session = AmplifierSession(
            {"session": {"orchestrator": "fixture-loop", "context": "fixture-context"}},
            session_id="pending-ready")
        session.coordinator.loader = PendingLoader()
        bridge = install_media_hook_bridge(session.coordinator)
        graph = bridge.begin_participating_graph(
            session, owner="pending", participants=("fixture-loop", "fixture-context"))
        await session.initialize()
        with pytest.raises(ValueError):
            graph.seal()
        assert not (await bridge(event()))["complete"]
    else:
        session, bridge, graph, receipt = await positive_graph()
        async def null_redactor(current):
            current.hooks.register("tool:post", lambda e, d: HookResult(
                action="modify", data={"result": None}), name="deferred-null")
        if phase == "undeclared":
            with pytest.raises(ValueError, match="declared"):
                session.coordinator.loader.enqueue_on_session_ready("unknown-owner", null_redactor)
        elif phase == "underlying":
            session.coordinator.loader._loader.enqueue_on_session_ready("fixture-hooks", null_redactor)
        else:
            session.coordinator.loader.enqueue_on_session_ready("fixture-hooks", null_redactor)
        assert not bridge.validate_receipt(receipt)
        assert not (await bridge(event()))["complete"]
    await session.cleanup()


@pytest.mark.asyncio
@requires_core2
async def test_unowned_preseal_mount_and_preexisting_inventory_never_qualify():
    for preexisting in (False, True):
        session = AmplifierSession(
            {"session": {"orchestrator": "fixture-loop", "context": "fixture-context"}},
            session_id="unowned-mount")
        session.coordinator.loader = ParticipatingFixtureLoader()
        if preexisting:
            await session.coordinator.mount("tools", object(), name="preexisting")
        bridge = install_media_hook_bridge(session.coordinator)
        if preexisting:
            with pytest.raises(ValueError, match="unmounted"):
                bridge.begin_participating_graph(
                    session, owner="bad", participants=("fixture-loop", "fixture-context"))
        else:
            graph = bridge.begin_participating_graph(
                session, owner="bad", participants=("fixture-loop", "fixture-context"))
            await session.initialize()
            await bridge.coordinator.mount("tools", object(), name="unowned")
            with pytest.raises(ValueError):
                graph.seal()
            assert not (await bridge(event()))["complete"]
        await session.cleanup()


@pytest.mark.asyncio
@requires_core2
async def test_participant_loader_swap_restore_invalidates_before_forwarding():
    session, bridge, graph, receipt = await positive_graph()
    participant = graph.participant("host.late")
    loader = participant.loader
    participant.loader = object()
    participant.loader = loader
    assert not bridge.validate_receipt(receipt)
    assert not (await bridge(event()))["complete"]
    await session.cleanup()


@pytest.mark.asyncio
@requires_core2
async def test_passive_ready_queue_inspection_reuses_original_callback_record():
    session = AmplifierSession(
        {"session": {"orchestrator": "fixture-loop", "context": "fixture-context"}},
        session_id="ready-inspection")
    loader = ParticipatingFixtureLoader()
    called = []
    async def ready(current):
        called.append(current)
    loader.enqueue_on_session_ready("host.ready", ready)
    session.coordinator.loader = loader
    bridge = install_media_hook_bridge(session.coordinator)
    graph = bridge.begin_participating_graph(
        session, owner="ready-inspection",
        participants=("fixture-loop", "fixture-context", "host.ready"))
    first = session.coordinator.loader.get_on_session_ready_queue()
    second = session.coordinator.loader.get_on_session_ready_queue()
    assert first[0][1] is second[0][1]
    await session.initialize()
    graph.seal()
    assert len(called) == 1 and (await bridge(event()))["complete"]
    await session.cleanup()


@pytest.mark.asyncio
@requires_core2
async def test_issuer_same_session_mode_transition_issues_new_generation():
    session, bridge, graph, old_receipt = await positive_graph()
    old_policy = graph.participant("host.late").hooks.register
    # Execute the actual participating hook registration/settings operation,
    # not a Boolean stand-in for the host trigger.
    with graph.transition() as successor:
        assert not bridge.validate_receipt(old_receipt)
        session.config["mode"] = "redacting-mode"
        successor.participant("host.late").hooks.register(
            "tool:post", lambda e, d: HookResult(action="modify", data={"result": None}),
            priority=10, name="mode-redactor")
        with pytest.raises(ValueError, match="reconstruction"):
            await bridge(event())
    receipt = bridge.graph_receipt
    result = await bridge(event())
    assert receipt is not old_receipt
    assert result["complete"] and result["result"] is None
    # A cached participating registration edge remains source-owned, but an
    # unscoped late change invalidates the current generation as usual.
    old_policy("tool:post", lambda e, d: HookResult(), name="late-cached")
    assert not bridge.validate_receipt(receipt)
    await session.cleanup()


@pytest.mark.asyncio
@requires_core2
async def test_same_session_transition_rejects_active_boundary_route_and_request():
    started, release = asyncio.Event(), asyncio.Event()
    async def callback(e, d):
        started.set()
        await release.wait()
        return HookResult()
    session, bridge, graph, receipt = await positive_graph([callback])
    operation = asyncio.create_task(bridge(event()))
    await started.wait()
    with pytest.raises(ValueError, match="idle"):
        with graph.transition():
            pass
    release.set()
    assert (await operation)["complete"]
    lease, hooks, attributed, _ = enroll(bridge, session.coordinator)
    with lease:
        with pytest.raises(ValueError, match="idle"):
            with graph.transition():
                pass
    with bridge.request_scope() as prepared_generation:
        assert callable(prepared_generation) and prepared_generation() is True
        with pytest.raises(ValueError, match="idle"):
            with graph.transition():
                pass
    assert prepared_generation() is False
    with graph.transition() as successor:
        with pytest.raises(ValueError, match="idle"):
            with successor.transition():
                pass
    assert (await bridge(event()))["complete"]
    await session.cleanup()


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["exception", "cancel", "unowned", "pending"])
@requires_core2
async def test_failed_same_session_transition_never_resurrects_old_receipt(failure):
    session, bridge, graph, receipt = await positive_graph()
    if failure in ("exception", "cancel"):
        error = RuntimeError if failure == "exception" else asyncio.CancelledError
        with pytest.raises(error):
            with graph.transition() as successor:
                successor.participant("host.late").hooks.register(
                    "tool:post", lambda e, d: HookResult(), name="started-change")
                raise error()
    else:
        with pytest.raises(ValueError):
            with graph.transition() as successor:
                if failure == "unowned":
                    bridge.coordinator.hooks.register("tool:post", lambda e, d: HookResult(), name="unowned")
                else:
                    async def pending(c):
                        pass
                    session.coordinator.loader.enqueue_on_session_ready("host.late", pending)
    assert not bridge.validate_receipt(receipt)
    assert not (await bridge(event()))["complete"]
    await session.cleanup()


@pytest.mark.asyncio
@requires_core2
async def test_continue_declared_null_propagates_to_later_native_echo():
    seen = []
    def redact(e, d):
        return HookResult(data={"result": None})
    def echo(e, d):
        seen.append(deepcopy(d))
        return HookResult(action="modify", data=d)
    session, bridge, graph, receipt = await positive_graph([redact, echo])
    result = await bridge(event())
    assert result["complete"] and result["result"] is None
    assert seen == [{"result": None}]
    await session.cleanup()


@pytest.mark.asyncio
@requires_core2
async def test_special_action_declared_result_is_incomplete_without_reordering():
    session, bridge, graph, receipt = await positive_graph([
        lambda e, d: HookResult(action="ask_user", data={"result": None}, approval_prompt="redact+ask")])
    class Approval:
        async def request_approval(self, *args):
            return "allow"
    session.coordinator.approval_system = Approval()
    result = await bridge(event())
    assert not result["complete"] and result["result"] is None
    await session.cleanup()


@pytest.mark.asyncio
@requires_core2
async def test_normal_return_without_configured_provider_install_never_seals():
    session = AmplifierSession(
        {"session": {"orchestrator": "fixture-loop", "context": "fixture-context"},
         "providers": [{"module": "fixture-provider", "config": {}}]},
        session_id="missing-provider")
    session.coordinator.loader = ParticipatingFixtureLoader()
    bridge = install_media_hook_bridge(session.coordinator)
    graph = bridge.begin_participating_graph(
        session, owner="failed-provider",
        participants=("fixture-loop", "fixture-context", "fixture-provider"))
    await session.initialize()
    with pytest.raises(ValueError):
        graph.seal()
    assert not (await bridge(event()))["complete"]
    await session.cleanup()


@pytest.mark.asyncio
@requires_core2
async def test_transition_cannot_satisfy_current_config_with_historical_mount():
    session, bridge, graph, receipt = await positive_graph()
    with graph.transition() as changed:
        session.config["session"]["context"] = {"module": "fixture-context", "config": {"version": "B"}}
        mounted = await session.coordinator.loader.load(
            "fixture-context", {"version": "B"}, source_hint=None, coordinator=session.coordinator)
        await mounted(session.coordinator)
    assert (await bridge(event()))["complete"]
    with pytest.raises(ValueError):
        with changed.transition():
            session.config["session"]["context"] = "fixture-context"
            # Deliberately NO actual reload/restoration from B to A.
    assert not (await bridge(event()))["complete"]
    await session.cleanup()


@pytest.mark.asyncio
@requires_core2
async def test_cached_participant_view_survives_valid_same_session_generation():
    session, bridge, graph, receipt = await positive_graph()
    view = graph.participant("fixture-loop")
    assert (await bridge(event(), hooks=view.hooks, coordinator=view))["complete"]
    with graph.transition():
        pass
    assert (await bridge(event(), hooks=view.hooks, coordinator=view))["complete"]
    await session.cleanup()


@pytest.mark.asyncio
@requires_core2
async def test_cached_old_ready_callback_invalidates_current_transition_before_effects():
    session = AmplifierSession(
        {"session": {"orchestrator": "fixture-loop", "context": "fixture-context"}},
        session_id="stale-ready")
    loader = ParticipatingFixtureLoader()
    calls = []
    async def original(current):
        calls.append("ready")
    loader.enqueue_on_session_ready("host.ready", original)
    session.coordinator.loader = loader
    bridge = install_media_hook_bridge(session.coordinator)
    graph = bridge.begin_participating_graph(
        session, owner="stale", participants=("fixture-loop", "fixture-context", "host.ready"))
    stale = session.coordinator.loader.get_on_session_ready_queue()[0][1]
    await session.initialize()
    graph.seal()
    with pytest.raises(ValueError):
        with graph.transition():
            with pytest.raises(ValueError, match="stale"):
                await stale(session.coordinator)
    assert calls == ["ready"] and not (await bridge(event()))["complete"]
    await session.cleanup()


@pytest.mark.asyncio
@requires_core2
async def test_cancelled_waiter_keeps_transition_guard_until_native_approval_settles():
    started, release, settled = asyncio.Event(), asyncio.Event(), asyncio.Event()
    session, bridge, graph, receipt = await positive_graph([
        lambda e, d: HookResult(action="ask_user", approval_prompt="hold actual approval")])
    class Approval:
        async def request_approval(self, *args):
            started.set()
            try:
                await release.wait()
                return "allow"
            finally:
                settled.set()
    session.coordinator.approval_system = Approval()
    task = asyncio.create_task(bridge(event()))
    await asyncio.wait_for(started.wait(), 2)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    with pytest.raises(ValueError, match="idle"):
        with graph.transition():
            pass
    release.set()
    await asyncio.wait_for(settled.wait(), 2)
    for _ in range(10):
        if not bridge._processing:
            break
        await asyncio.sleep(0.01)
    with graph.transition():
        pass
    await session.cleanup()


@pytest.mark.asyncio
@requires_core2
async def test_actual_saved_settings_phase_tracks_load_before_final_late_seal():
    session = AmplifierSession(
        {"session": {"orchestrator": "fixture-loop", "context": "fixture-context"}},
        session_id="saved-settings")
    session.coordinator.loader = ParticipatingFixtureLoader()
    bridge = install_media_hook_bridge(session.coordinator)
    graph = bridge.begin_participating_graph(
        session, owner="saved", participants=("fixture-loop", "fixture-context", "host.settings"))
    await session.initialize()
    with graph.configure():
        session.config["session"]["context"] = {"module": "fixture-context", "config": {"saved": True}}
        mounted = await session.coordinator.loader.load(
            "fixture-context", {"saved": True}, coordinator=session.coordinator)
        await mounted(session.coordinator)
    graph.participant("host.settings").hooks.register(
        "tool:post", lambda e, d: HookResult(), name="late-settings")
    graph.seal()
    assert (await bridge(event()))["complete"]
    await session.cleanup()


@pytest.mark.asyncio
@requires_core2
async def test_public_evidence_metadata_detached_identity_opaque_and_strong():
    session, bridge, graph, receipt = await positive_graph()
    evidence = graph.participants_evidence()
    evidence["loads"][0]["config"]["pretend"] = True
    evidence["registrations"][0]["participant"] = "other"
    assert type(evidence["registrations"][0]["identity"]) is object
    assert bridge.validate_receipt(receipt)
    assert callable(evidence["loads"][0]["mount"])
    assert callable(evidence["registrations"][0]["original"])
    assert (await bridge(event()))["complete"]
    await session.cleanup()


@pytest.mark.asyncio
@requires_core2
async def test_transition_direct_remount_cannot_inherit_other_config_provenance():
    session, bridge, graph, receipt = await positive_graph()
    context_a = session.coordinator.get("context")
    with graph.transition() as changed:
        session.config["session"]["context"] = {"module": "fixture-context", "config": {"version": "B"}}
        mounted = await session.coordinator.loader.load(
            "fixture-context", {"version": "B"}, coordinator=session.coordinator)
        await mounted(session.coordinator)
    assert session.coordinator.get("context") is not context_a
    assert (await bridge(event()))["complete"]
    with pytest.raises(ValueError):
        with changed.transition() as mismatch:
            await mismatch.participant("fixture-context").mount("context", context_a)
    assert not (await bridge(event()))["complete"]
    await session.cleanup()


@pytest.mark.asyncio
@requires_core2
async def test_explicit_host_adapter_mount_retains_exact_prior_object_provenance():
    session, bridge, graph, receipt = await positive_graph()
    previous = session.coordinator.get("context")
    adapter = object()
    with graph.transition() as changed:
        await changed.participant("host.late").mount_adapter(
            "context", adapter, previous=previous)
    assert session.coordinator.get("context") is adapter
    assert (await bridge(event()))["complete"]
    evidence = changed.participants_evidence()["adapters"]
    assert evidence[-1]["previous"] is previous and evidence[-1]["installed"] is adapter
    with pytest.raises(ValueError):
        with changed.transition() as invalid:
            await invalid.participant("host.late").mount_adapter(
                "context", object(), previous=previous)
    assert not (await bridge(event()))["complete"]
    await session.cleanup()


@pytest.mark.asyncio
@requires_core2
async def test_request_scope_yields_literal_validator_for_entire_candidate_lifetime():
    session, bridge, graph, receipt = await positive_graph()
    with bridge.request_scope() as validate:
        assert validate() is True
        assert validate() is True  # count and send reuse one entered lifetime
        with pytest.raises(ValueError, match="idle"):
            with graph.transition():
                pass
        session.config["revoked_candidate"] = True
        assert validate() is False
        del session.config["revoked_candidate"]
        assert validate() is False  # restored config cannot revive receipt
    assert validate() is False
    assert bridge._active_requests == 0
    await session.cleanup()


@requires_core2
@pytest.mark.asyncio
async def test_synchronous_native_processing_refusal_clears_nested_observers_once():
    calls = []
    session, bridge, graph, receipt = await positive_graph([
        lambda e, d: calls.append(e) or HookResult(
            action="inject_context", context_injection="oversize")],
        session_config={"injection_size_limit": 3})
    assert session.coordinator.injection_size_limit == 3
    outer, outer_hooks, outer_coord, outer_observation = enroll(
        bridge, session.coordinator, call="outer", parent="root")
    with outer:
        inner, hooks, attributed, observation = enroll(
            bridge, session.coordinator, parent_hooks=outer_hooks,
            parent_coordinator=outer_coord)
        with inner:
            with pytest.raises(ValueError, match="Context injection exceeds 3 characters"):
                await bridge(event(call_id="inner"), hooks=hooks, coordinator=attributed)
            assert observation == {"post_seen": True, "post_result": None}
            assert outer_observation == {"post_seen": True, "post_result": None}
    assert calls == ["tool:post"]
    assert not bridge._processing and not bridge._routes and bridge._active_boundaries == 0
    assert bridge.validate_receipt(receipt)
    await session.cleanup()


class ProviderFixtureLoader(ParticipatingFixtureLoader):
    """Actual Core initialization owns provider remapping and rollback."""
    def __init__(self, *, automatic=False, decline=False, fail=False, shared=False):
        super().__init__()
        self.automatic, self.decline, self.fail = automatic, decline, fail
        self.providers = []
        self.ready_calls = []
        self.shared = shared

    async def provider_ready(self, current):
        self.ready_calls.append(current)

    async def load(self, module_id, config, **kwargs):
        if module_id != "provider-fixture":
            return await super().load(module_id, config, **kwargs)

        class Provider:
            name = "fixture"

        async def mount(current):
            if self.decline:
                return
            provider = self.providers[0] if self.shared and self.providers else Provider()
            self.providers.append(provider)
            if self.automatic:
                await current.mount("providers", provider)
            else:
                await current.mount("providers", provider, name="fixture")
            if self.fail:
                raise ValueError("provider failed after mount")
        # A cached loader may return the same original callback for two accounts.
        if not hasattr(self, "ready"):
            self.ready = self.provider_ready
        mount.__on_session_ready__ = (module_id, self.ready)
        return mount


@requires_core2
@pytest.mark.asyncio
@pytest.mark.parametrize("automatic,accounts,shared", [
    (True, (None,), False), (False, ("account-A",), False),
    (True, ("account-A",), False),
    (False, ("account-A", "account-B"), False),
    (True, ("account-A", "account-B"), False),
    (True, ("account-A", "account-B"), True),
    (True, (None, "account-B"), False),
])
async def test_configured_provider_occurrences_seal_after_actual_core_remap(automatic, accounts, shared):
    session = AmplifierSession({
        "session": {"orchestrator": "fixture-loop", "context": "fixture-context"},
        "providers": [{"module": "provider-fixture",
                       **({"instance_id": account} if account else {}),
                       "config": {}} for account in accounts]})
    loader = ProviderFixtureLoader(automatic=automatic, shared=shared)
    session.coordinator.loader = loader
    bridge = install_media_hook_bridge(session.coordinator)
    graph = bridge.begin_participating_graph(
        session, owner="provider-occurrences",
        participants=("fixture-loop", "fixture-context", "provider-fixture"))
    failures = []
    bridge.participant("fixture-loop").hooks.register(
        "module:load_failed", lambda e, d: failures.append(d) or HookResult(), name="load-failures")
    await session.initialize()
    assert failures == []
    for account, provider in zip(accounts, loader.providers):
        assert session.coordinator.get("providers", account or "fixture") is provider
    receipt = graph.seal()
    assert bridge.validate_receipt(receipt) and (await bridge(event()))["complete"]
    assert len(loader.providers) == len(loader.ready_calls) == len(accounts)
    with graph.transition():
        pass
    assert (await bridge(event()))["complete"]
    await session.cleanup()


@requires_core2
@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["decline", "raise", "unmount", "config", "direct", "alias"])
async def test_provider_occurrence_missing_or_stale_source_cannot_seal(failure):
    session = AmplifierSession({
        "session": {"orchestrator": "fixture-loop", "context": "fixture-context"},
        "providers": [{"module": "provider-fixture", "instance_id": "account-A"}]})
    loader = ProviderFixtureLoader(automatic=True, decline=failure == "decline", fail=failure == "raise")
    session.coordinator.loader = loader
    bridge = install_media_hook_bridge(session.coordinator)
    graph = bridge.begin_participating_graph(
        session, owner="provider-negatives",
        participants=("fixture-loop", "fixture-context", "provider-fixture"))
    await session.initialize()
    if failure == "unmount":
        await graph.participant("provider-fixture").unmount("providers", name="account-A")
    elif failure == "config":
        session.config["providers"][0]["config"] = {"changed": True}
    elif failure == "direct":
        await graph.participant("provider-fixture").mount(
            "providers", loader.providers[0], name="account-A")
    elif failure == "alias":
        # A raw alias of an already-owned object is not a configured occurrence.
        await session.coordinator.mount("providers", loader.providers[0], name="unexplained")
    with pytest.raises(ValueError):
        graph.seal()
    assert bridge.graph_receipt is None and not (await bridge(event()))["complete"]
    await session.cleanup()


@pytest.mark.asyncio
@pytest.mark.parametrize("replacement", ["retain", "null", "deny", "failure"])
async def test_public_unqualified_boundary_never_exposes_media_on_supported_or_legacy_core(replacement):
    """Core 1.3 runs real native hooks but cannot issue a positive receipt."""
    session = AmplifierSession(
        {"session": {"orchestrator": "fixture-loop", "context": "fixture-context"}})
    bridge = install_media_hook_bridge(session.coordinator)
    if CORE_ABI != "2.0.2":
        with pytest.raises(ValueError, match="qualified Core ABI 2.0.2"):
            bridge.begin_participating_graph(
                session, owner="unsupported", participants=("fixture-loop", "fixture-context"))
    calls = []
    def policy(e, d):
        calls.append(e)
        if replacement == "failure":
            raise RuntimeError("actual native callback failure")
        if replacement == "null":
            return HookResult(action="modify", data={"result": None})
        return HookResult(action="deny" if replacement == "deny" else "continue")
    bridge.hooks.register("tool:post", policy, name="actual-native")
    result = await bridge(event())
    assert result["complete"] is False and result["result"] is None
    assert calls == ["tool:post"] and bridge.graph_receipt is None
    assert bridge.validate_receipt(object()) is False
    assert isinstance(result["hook_result"], HookResult)
    await session.cleanup()


def test_mount_lifecycle_opt_in_does_not_probe_generic_coordinator_or_mock():
    from unittest.mock import MagicMock
    class Unrelated:
        def __getattr__(self, name):
            pytest.fail("must not duck-type arbitrary coordinator")
    assert media_mount_lifecycle(Unrelated()) is None
    assert media_mount_lifecycle(MagicMock()) is None
    assert media_mount_lifecycle(ModuleCoordinator()) is None


async def configured_lifecycle_graph(kind, *, seal=True):
    class LifecycleLoader(ProviderFixtureLoader):
        async def load(self, module_id, config, **kwargs):
            if module_id == "fixture-tool":
                async def mount(current):
                    await current.mount("tools", object(), name="fixture")
                return mount
            return await super().load(module_id, config, **kwargs)
    session = AmplifierSession({
        "session": {"orchestrator": "fixture-loop", "context": "fixture-context"},
        kind: [{"module": "provider-fixture" if kind == "providers" else "fixture-tool"}]})
    session.coordinator.loader = LifecycleLoader(automatic=True)
    bridge = install_media_hook_bridge(session.coordinator)
    graph = bridge.begin_participating_graph(
        session, owner="lifecycle",
        participants=("fixture-loop", "fixture-context",
                      "provider-fixture" if kind == "providers" else "fixture-tool", "host.settings"))
    graph.participant("host.settings")
    await session.initialize()
    receipt = graph.seal() if seal else None
    return session, bridge, graph, receipt


@requires_core2
@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["providers", "tools"])
async def test_configured_suspend_restore_retains_same_actual_object_across_generations(kind):
    session, bridge, graph, old = await configured_lifecycle_graph(kind)
    view = graph.participant("host.settings")
    lifecycle = media_mount_lifecycle(view)
    module = session.coordinator.get(kind, "fixture")
    with pytest.raises(ValueError, match="idle"):
        await lifecycle.suspend(kind, name="fixture")
    with graph.transition() as disabled:
        token = await lifecycle.suspend(kind, name="fixture")
        assert session.coordinator.get(kind, "fixture") is None
    disabled_receipt = bridge.graph_receipt
    assert disabled_receipt is not old and not bridge.validate_receipt(old)
    assert (await bridge(event()))["complete"]  # disabled effective plan is qualified
    assert session.config[kind]  # original configured occurrence remains
    with disabled.transition() as enabled:
        assert await lifecycle.restore(token) is module
    assert session.coordinator.get(kind, "fixture") is module
    assert bridge.graph_receipt is not disabled_receipt
    assert (await bridge(event()))["complete"]
    with enabled.transition():
        with pytest.raises(ValueError, match="consumed"):
            await lifecycle.restore(token)
    await session.cleanup()


@requires_core2
@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["providers", "tools"])
async def test_configured_initial_settings_can_suspend_before_final_seal(kind):
    session, bridge, graph, _ = await configured_lifecycle_graph(kind, seal=False)
    lifecycle = media_mount_lifecycle(graph.participant("host.settings"))
    with graph.configure():
        token = await lifecycle.suspend(kind, name="fixture")
    graph.seal()
    assert (await bridge(event()))["complete"]
    with graph.transition():
        await lifecycle.restore(token)
    assert (await bridge(event()))["complete"]
    await session.cleanup()


@requires_core2
@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["foreign", "owner", "config", "slot", "raw", "request"])
async def test_configured_suspension_never_blesses_unknown_or_changed_provenance(failure):
    session, bridge, graph, old = await configured_lifecycle_graph("tools")
    lifecycle = media_mount_lifecycle(graph.participant("host.settings"))
    if failure == "request":
        with bridge.request_scope():
            with pytest.raises(ValueError, match="idle"):
                await lifecycle.suspend("tools", name="fixture")
        assert bridge.validate_receipt(old)
    elif failure == "raw":
        with pytest.raises(ValueError):
            with graph.transition():
                await session.coordinator.mount("tools", object(), name="raw")
                with pytest.raises(ValueError, match="participating"):
                    await lifecycle.suspend("tools", name="raw")
    else:
        with graph.transition() as disabled:
            token = await lifecycle.suspend("tools", name="fixture")
        if failure in ("foreign", "owner"):
            with disabled.transition():
                other = (media_mount_lifecycle(bridge.participant("fixture-tool"))
                         if failure == "owner" else lifecycle)
                with pytest.raises(ValueError, match="unknown|another"):
                    await other.restore(object() if failure == "foreign" else token)
            assert (await bridge(event()))["complete"]
        else:
            with pytest.raises(ValueError):
                with disabled.transition():
                    if failure == "config":
                        session.config["tools"][0]["config"] = {"changed": True}
                    else:
                        await session.coordinator.mount("tools", object(), name="fixture")
                    with pytest.raises(ValueError, match="changed|empty"):
                        await lifecycle.restore(token)
            assert not bridge.validate_receipt(old) and not (await bridge(event()))["complete"]
    await session.cleanup()


@requires_core2
@pytest.mark.asyncio
@pytest.mark.parametrize("accounts", [(None,), ("account-A",), ("account-A", "account-B")])
async def test_actual_core_module_loader_discovers_and_caches_provider_occurrences(monkeypatch, accounts):
    """No replacement loader: Core discovers local fixture modules and caches mounts."""
    import sys
    from types import ModuleType
    from amplifier_core.loader import ModuleLoader
    mounted, ready, failed = [], [], []

    async def loop_mount(current, config):
        await current.mount("orchestrator", object())
    async def context_mount(current, config):
        await current.mount("context", object())
    class Provider:
        name = "fixture"
    async def provider_mount(current, config):
        provider = Provider()
        mounted.append((provider, deepcopy(config)))
        await current.mount("providers", provider)
    async def provider_ready(current):
        ready.append(current)

    for module_id, mount in (("fixture-loop", loop_mount), ("fixture-context", context_mount),
                             ("provider-fixture", provider_mount)):
        module = ModuleType(f"amplifier_module_{module_id.replace('-', '_')}")
        module.mount = mount
        if module_id == "provider-fixture":
            module.on_session_ready = provider_ready
        monkeypatch.setitem(sys.modules, module.__name__, module)
    session = AmplifierSession({
        "session": {"orchestrator": "fixture-loop", "context": "fixture-context"},
        "providers": [{"module": "provider-fixture", "config": {"account": account},
                       **({"instance_id": account} if account else {})} for account in accounts]})
    bridge = install_media_hook_bridge(session.coordinator)
    assert type(bridge._loader._loader) is ModuleLoader
    graph = bridge.begin_participating_graph(
        session, owner="actual-core-loader",
        participants=("fixture-loop", "fixture-context", "provider-fixture"))
    graph.participant("fixture-loop").hooks.register(
        "module:load_failed", lambda e, d: failed.append(d) or HookResult(), name="failed")
    await session.initialize()
    assert failed == [] and len(mounted) == len(ready) == len(accounts)
    graph.seal()
    for (provider, config), account in zip(mounted, accounts):
        assert config == {"account": account}
        assert session.coordinator.get("providers", account or "fixture") is provider
    assert (await bridge(event()))["complete"]
    await session.cleanup()


@requires_core2
@pytest.mark.asyncio
async def test_named_singleton_mount_uses_actual_core_ignored_name_semantics():
    class NamedLoader(ParticipatingFixtureLoader):
        async def load(self, module_id, config, **kwargs):
            async def mount(current):
                await current.mount("orchestrator" if module_id == "fixture-loop" else "context",
                                    object(), name="ignored-singleton-name")
            return mount
    session = AmplifierSession(
        {"session": {"orchestrator": "fixture-loop", "context": "fixture-context"}})
    session.coordinator.loader = NamedLoader()
    bridge = install_media_hook_bridge(session.coordinator)
    graph = bridge.begin_participating_graph(
        session, owner="named-singleton", participants=("fixture-loop", "fixture-context"))
    await session.initialize()
    receipt = graph.seal()
    assert bridge.validate_receipt(receipt) and (await bridge(event()))["complete"]
    await session.cleanup()


@requires_core2
@pytest.mark.asyncio
@pytest.mark.parametrize("phase", ["configure", "transition"])
async def test_partial_identical_account_addition_preserves_existing_occurrence(phase):
    session = AmplifierSession({
        "session": {"orchestrator": "fixture-loop", "context": "fixture-context"},
        "providers": [{"module": "provider-fixture", "instance_id": "account-A", "config": {}}]})
    loader = ProviderFixtureLoader(automatic=True)
    session.coordinator.loader = loader
    bridge = install_media_hook_bridge(session.coordinator)
    graph = bridge.begin_participating_graph(
        session, owner="partial-config", participants=("fixture-loop", "fixture-context", "provider-fixture"))
    await session.initialize()
    if phase == "transition":
        graph.seal()
    before = session.coordinator.get("providers", "account-A")
    with (graph.configure() if phase == "configure" else graph.transition()) as current:
        session.config["providers"].append(
            {"module": "provider-fixture", "instance_id": "account-B", "config": {}})
        # Real public loader and actual Core's named remap operations.
        mount = await session.coordinator.loader.load("provider-fixture", {}, source_hint=None)
        await mount(session.coordinator)
        second = session.coordinator.get("providers", "fixture")
        await session.coordinator.mount("providers", second, name="account-B")
        await session.coordinator.unmount("providers", name="fixture")
        ready = mount.__on_session_ready__
        session.coordinator.loader.enqueue_on_session_ready(*ready)
        for _, callback in session.coordinator.loader.get_on_session_ready_queue():
            await callback(session.coordinator)
        session.coordinator.loader.clear_on_session_ready_queue()
    if phase == "configure":
        current.seal()
    assert session.coordinator.get("providers", "account-A") is before
    assert session.coordinator.get("providers", "account-B") is second and second is not before
    assert [load["occurrence"] for load in current.participants_evidence()["loads"]][-2:] == [
        ("providers", 0, "account-A"), ("providers", 1, "account-B")]
    assert (await bridge(event()))["complete"]
    await session.cleanup()


@requires_core2
@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["providers", "tools"])
@pytest.mark.parametrize("operation", ["suspend", "restore"])
async def test_cancelled_mount_lifecycle_retains_pending_evidence_and_no_receipt(kind, operation):
    session, bridge, graph, receipt = await configured_lifecycle_graph(kind)
    lifecycle = media_mount_lifecycle(graph.participant("host.settings"))
    module = session.coordinator.get(kind, "fixture")
    token = None
    if operation == "restore":
        with graph.transition() as graph:
            token = await lifecycle.suspend(kind, name="fixture")
    with pytest.raises(asyncio.CancelledError):
        with graph.transition() as interrupted:
            pending = asyncio.create_task(
                lifecycle.suspend(kind, name="fixture") if operation == "suspend"
                else lifecycle.restore(token))
            asyncio.get_running_loop().call_soon(pending.cancel)
            await pending
    for _ in range(20):
        if not bridge._processing:
            break
        await asyncio.sleep(0.01)
    assert not bridge._processing
    actual = session.coordinator.get(kind, "fixture")
    assert actual is (None if operation == "suspend" else module)
    evidence = interrupted.participants_evidence()["suspended"]
    assert len(evidence) == 1 and evidence[0]["module"] is module
    assert evidence[0]["state"] == "interrupted"
    assert bridge.validate_receipt(receipt) is False
    assert not (await bridge(event()))["complete"]
    with pytest.raises(ValueError, match="idle"):
        await lifecycle.restore(token)
    await session.cleanup()


@requires_core2
@pytest.mark.asyncio
@pytest.mark.parametrize("drift", ["config", "source", "instance"])
async def test_suspend_checks_current_configured_occurrence_before_unmount(drift):
    session, bridge, graph, receipt = await configured_lifecycle_graph("providers")
    lifecycle = media_mount_lifecycle(graph.participant("host.settings"))
    actual = session.coordinator.get("providers", "fixture")
    with pytest.raises(ValueError):
        with graph.transition():
            spec = session.config["providers"][0]
            spec[{"config": "config", "source": "source", "instance": "instance_id"}[drift]] = (
                {"changed": True} if drift == "config" else "changed")
            with pytest.raises(ValueError, match="source"):
                await lifecycle.suspend("providers", name="fixture")
            assert session.coordinator.get("providers", "fixture") is actual
            assert bridge._graph.participants_evidence()["suspended"] == ()
    assert not bridge.validate_receipt(receipt)
    await session.cleanup()
