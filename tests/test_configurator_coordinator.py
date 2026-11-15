"""Explicit coordinator routing, including real Core cooperative generations.

The native fixture uses an actual Rust session/registry with local module
fixtures, not a CLI/Unified host or provider/inference qualification.
"""

import asyncio
from copy import deepcopy
from importlib.metadata import version
import sys
from types import ModuleType, SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
import pytest_asyncio
from amplifier_core import AmplifierSession, HookResult

from amplifier_foundation.bundle import Bundle
from amplifier_foundation.configurator import BundleStateManager, SessionConfigurator
from amplifier_foundation.configurator._inspector import BundleInspector
from amplifier_foundation.session.media_hook_bridge import install_media_hook_bridge
from amplifier_foundation.session.media_hook_bridge import media_mount_lifecycle


requires_core2 = pytest.mark.skipif(
    version("amplifier-core") != "2.0.2",
    reason="cooperative graph positive contract qualifies exact Core 2.0.2 only",
)


@pytest.fixture(autouse=True)
def isolate_registry(monkeypatch):
    monkeypatch.setattr(BundleInspector, "_load_registry_dict", lambda self: None)


@pytest.fixture
def routed_coordinator():
    coordinator = MagicMock()
    coordinator.config = {
        "tools": [{"module": "tool-fixture"}],
        "agents": {"fixture-agent": {"description": "local"}},
    }
    coordinator.get_capability.return_value = None
    coordinator.hooks.list_handlers.return_value = {"tool:post": ["fixture-policy"]}
    coordinator.hooks.emit = AsyncMock()
    coordinator.mount = AsyncMock()
    coordinator.unmount = AsyncMock()
    mounts = {"tools": {"fixture": object()}, "providers": {"fixture": object()}}
    coordinator.get.side_effect = lambda kind: mounts.get(kind)
    return coordinator


@pytest.mark.parametrize("factory", [SessionConfigurator, BundleStateManager])
@pytest.mark.parametrize("explicit_none", [False, True])
def test_default_coordinator_keeps_original_identity(factory, explicit_none):
    session = SimpleNamespace(coordinator=MagicMock(config={}))
    prepared = SimpleNamespace(bundle=Bundle(name="fixture"))
    kwargs = {"coordinator": None} if explicit_none else {}
    configurator = factory(session, prepared, **kwargs)
    assert configurator._session is session
    assert configurator._coordinator is session.coordinator
    assert configurator._prepared_bundle is prepared


@pytest.mark.parametrize("factory", [SessionConfigurator, BundleStateManager])
def test_explicit_falsey_view_never_reads_raw_session_coordinator(
    factory, routed_coordinator
):
    class Session:
        @property
        def coordinator(self):
            pytest.fail("explicit routing must not consult session.coordinator")

    session = Session()
    routed_coordinator.__bool__.return_value = False
    prepared = SimpleNamespace(bundle=Bundle(name="fixture"))
    configurator = factory(session, prepared, coordinator=routed_coordinator)
    assert configurator._session is session
    assert configurator._coordinator is routed_coordinator
    assert configurator._hook_snapshot == {
        "fixture-policy": {"event": "tool:post"}
    }
    assert configurator._module_to_tools == {"tool-fixture": ["fixture"]}
    with pytest.raises(TypeError):
        factory(session, prepared, routed_coordinator)


@pytest.mark.asyncio
async def test_saved_settings_and_restore_use_only_explicit_view(routed_coordinator):
    raw = MagicMock()
    session = SimpleNamespace(coordinator=raw)
    prepared = SimpleNamespace(bundle=Bundle(name="fixture"))
    configurator = SessionConfigurator(
        session, prepared, coordinator=routed_coordinator
    )
    instances = {
        kind: routed_coordinator.get(kind)["fixture"]
        for kind in ("tools", "providers")
    }
    warnings = await configurator.apply_saved_settings({
        "disabled": {
            "tools": ["fixture"],
            "providers": ["fixture"],
            "agents": ["fixture-agent"],
        },
        "config_overrides": {"session.mode": "saved"},
    })
    assert warnings == []
    assert configurator.config_get("session.mode") == "saved"
    assert configurator.snapshot()["agents"]["enabled"] == []
    assert [item.name for item in configurator.agents_list()] == ["fixture-agent"]
    assert routed_coordinator.unmount.await_args_list == [
        (("tools",), {"name": "fixture"}),
        (("providers",), {"name": "fixture"}),
    ]
    assert routed_coordinator.hooks.emit.await_count == 1
    await configurator.tool_enable_module("tool-fixture")
    await configurator.provider_enable("fixture")
    configurator.agent_enable("fixture-agent")
    assert routed_coordinator.mount.await_args_list == [
        (("tools", instances["tools"]), {"name": "fixture"}),
        (("providers", instances["providers"]), {"name": "fixture"}),
    ]
    assert configurator.snapshot()["agents"]["enabled"] == ["fixture-agent"]
    assert session.coordinator is raw
    assert raw.mock_calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["tool", "provider"])
async def test_explicit_view_failure_recovery_keeps_same_stashed_instance(
    kind, routed_coordinator
):
    raw = MagicMock()
    configurator = SessionConfigurator(
        SimpleNamespace(coordinator=raw),
        SimpleNamespace(bundle=Bundle(name="fixture")),
        coordinator=routed_coordinator,
    )
    category = "tools" if kind == "tool" else "providers"
    instance = routed_coordinator.get(category)["fixture"]
    disable = getattr(configurator, f"{kind}_disable")
    enable = getattr(configurator, f"{kind}_enable")
    routed_coordinator.unmount.side_effect = RuntimeError("unmount failed")
    with pytest.raises(RuntimeError, match="unmount failed"):
        await disable("fixture")
    assert configurator._stash[category]["fixture"] is instance
    routed_coordinator.mount.side_effect = RuntimeError("mount failed")
    with pytest.raises(RuntimeError, match="mount failed"):
        await enable("fixture")
    assert configurator._stash[category]["fixture"] is instance
    routed_coordinator.mount.side_effect = None
    await enable("fixture")
    assert "fixture" not in configurator._stash[category]
    routed_coordinator.mount.assert_awaited_with(category, instance, name="fixture")
    assert raw.mock_calls == []


@pytest.mark.asyncio
async def test_explicit_view_module_restore_failure_remains_retryable(routed_coordinator):
    configurator = SessionConfigurator(
        SimpleNamespace(coordinator=object()),
        SimpleNamespace(bundle=Bundle(name="fixture")),
        coordinator=routed_coordinator,
    )
    instance = routed_coordinator.get("tools")["fixture"]
    assert await configurator.tool_disable_module("tool-fixture") == ["fixture"]
    routed_coordinator.mount.side_effect = RuntimeError("mount failed")
    assert await configurator.tool_enable_module("tool-fixture") == []
    assert configurator._stash["tools"]["fixture"] is instance
    routed_coordinator.mount.side_effect = None
    assert await configurator.tool_enable_module("tool-fixture") == ["fixture"]
    assert "fixture" not in configurator._stash["tools"]
    routed_coordinator.mount.assert_awaited_with("tools", instance, name="fixture")


class ConfiguratorFixtureLoader:
    """Local module mounts; Core initialization and graph tracking are real."""

    async def load(self, module_id, config, *, source_hint=None, coordinator=None):
        async def mount(current):
            kind = {
                "fixture-loop": "orchestrator",
                "fixture-context": "context",
            }[module_id]
            await current.mount(kind, object())
        return mount

    def get_on_session_ready_queue(self):
        return []

    def clear_on_session_ready_queue(self):
        pass


@pytest.mark.asyncio
@pytest.mark.skipif(
    version("amplifier-core") != "2.0.2",
    reason="cooperative graph positive contract qualifies exact Core 2.0.2 only",
)
async def test_native_configure_and_transitions_reuse_exact_configurator_view():
    session = AmplifierSession({
        "session": {"orchestrator": "fixture-loop", "context": "fixture-context"},
        "agents": {"fixture-agent": {"description": "local"}},
    }, session_id="configurator-routing")
    raw = session.coordinator
    raw.loader = ConfiguratorFixtureLoader()
    bridge = install_media_hook_bridge(raw)
    graph = bridge.begin_participating_graph(
        session, owner="configurator-test",
        participants=("fixture-loop", "fixture-context", "host.configurator"),
    )
    view = graph.participant("host.configurator")
    prepared = SimpleNamespace(bundle=Bundle(name="fixture"))
    configurator = SessionConfigurator(session, prepared, coordinator=view)
    calls = []
    view.hooks.register(
        "tool:post", lambda e, d: calls.append(e) or HookResult(),
        name="configurator-policy",
    )
    # These are runtime host contributions, not configured load-plan modules.
    tool, provider = object(), object()
    try:
        await session.initialize()
        await view.mount("tools", tool, name="fixture")
        await view.mount("providers", provider, name="fixture")
        with graph.configure():
            assert await configurator.apply_saved_settings({
                "disabled": {
                    "tools": ["fixture"], "providers": ["fixture"],
                    "agents": ["fixture-agent"],
                },
                "config_overrides": {"session.mode": "saved"},
            }) == []
        assert session.config["session"]["mode"] == "saved"
        assert session.config["agents"] == {}
        assert raw.get("tools") == {}
        assert raw.get("providers") == {}
        receipt = graph.seal()
        assert bridge.validate_receipt(receipt)

        event = {"tool_name": "fixture", "tool_call_id": "call", "result": "safe"}
        assert (await bridge(event, hooks=view.hooks, coordinator=view))["complete"]
        with graph.transition() as enabled:
            await configurator.tool_enable("fixture")
            await configurator.provider_enable("fixture")
            configurator.agent_enable("fixture-agent")
            configurator.config_set("session.mode", "enabled")
        assert not bridge.validate_receipt(receipt)
        assert session.config["session"]["mode"] == "enabled"
        assert "fixture-agent" in session.config["agents"]
        assert raw.get("tools")["fixture"] is tool
        assert raw.get("providers")["fixture"] is provider
        assert (await bridge(event, hooks=view.hooks, coordinator=view))["complete"]
        with enabled.transition():
            await configurator.tool_disable("fixture")
            await configurator.provider_disable("fixture")
            configurator.config_set("session.mode", "disabled-again")
        assert (await bridge(event, hooks=view.hooks, coordinator=view))["complete"]
        assert calls == ["tool:post"] * 3
        assert configurator._session is session
        assert configurator._coordinator is view
        assert session.coordinator is raw
    finally:
        await session.cleanup()


@pytest_asyncio.fixture
async def configured_graph(monkeypatch, request):
    """Real Core ModuleLoader discovers local modules, with configured slots."""
    accounts = request.param
    mounted, failed = {"tools": {}, "providers": {}}, []

    async def loop_mount(current, config):
        await current.mount("orchestrator", object())

    async def context_mount(current, config):
        await current.mount("context", object())

    async def tool_mount(current, config):
        for name in ("fixture_read", "fixture_write"):
            tool = object()
            mounted["tools"][name] = tool
            await current.mount("tools", tool, name=name)

    class Provider:
        name = "fixture"

    async def provider_mount(current, config):
        provider = Provider()
        mounted["providers"][config.get("account") or "fixture"] = provider
        await current.mount("providers", provider)  # exercise actual Core naming

    for module_id, mount in (
        ("fixture-loop", loop_mount), ("fixture-context", context_mount),
        ("tool-fixture", tool_mount), ("provider-fixture", provider_mount),
    ):
        module = ModuleType(f"amplifier_module_{module_id.replace('-', '_')}")
        module.mount = mount
        monkeypatch.setitem(sys.modules, module.__name__, module)

    tools = [{"module": "tool-fixture"}]
    providers = [
        {"module": "provider-fixture", "config": {"account": account},
         **({"instance_id": account} if account else {})}
        for account in accounts
    ]
    session = AmplifierSession({
        "session": {"orchestrator": "fixture-loop", "context": "fixture-context"},
        "tools": tools, "providers": providers,
    }, session_id="configured-configurator")
    raw = session.coordinator
    bridge = install_media_hook_bridge(raw)
    graph = bridge.begin_participating_graph(
        session, owner="configured-configurator",
        participants=("fixture-loop", "fixture-context", "tool-fixture",
                      "provider-fixture", "host.configurator"),
    )
    view = graph.participant("host.configurator")
    view.hooks.register(
        "module:load_failed", lambda e, d: failed.append(d) or HookResult(),
        name="load-failures",
    )
    try:
        await session.initialize()
        assert failed == []
        assert len(mounted["tools"]) == 2
        assert len(mounted["providers"]) == len(accounts)
        prepared = SimpleNamespace(bundle=Bundle(
            name="configured-fixture", tools=tools, providers=providers,
        ))
        configurator = SessionConfigurator(session, prepared, coordinator=view)
        yield SimpleNamespace(
            session=session, raw=raw, bridge=bridge, graph=graph, view=view,
            configurator=configurator, mounted=mounted,
        )
    finally:
        await session.cleanup()


@requires_core2
@pytest.mark.asyncio
@pytest.mark.parametrize(
    "configured_graph", [(None,), ("account-A",), ("account-A", "account-B")],
    indirect=True,
)
@pytest.mark.parametrize("by_module", [False, True])
async def test_actual_configured_saved_settings_restore_and_successors(
    configured_graph, by_module
):
    f = configured_graph
    cfg = f.configurator
    tools = list(f.mounted["tools"])
    providers = list(f.mounted["providers"])
    plan = deepcopy({key: f.session.config[key] for key in ("tools", "providers")})
    calls = []

    def policy(e, data):
        calls.append(e)
        return (
            HookResult(action="modify", data={"result": None})
            if cfg.config_get("policy.redact") else HookResult()
        )

    f.view.hooks.register("tool:post", policy, name="settings-policy")
    settings = {
        "disabled": {"tools": ["tool-fixture"] if by_module else tools,
                     "providers": providers},
        "config_overrides": {"policy.redact": True},
    }
    with f.graph.configure():
        assert await cfg.apply_saved_settings(settings) == []
    receipt = f.graph.seal()
    assert f.bridge.validate_receipt(receipt)
    first_tokens = {
        kind: dict(tokens) for kind, tokens in cfg._state._mount_suspensions.items()
    }
    for kind, instances in f.mounted.items():
        assert f.raw.get(kind) == {}
        assert f.session.config[kind] == plan[kind]  # declarations stay intact
        for name, instance in instances.items():
            assert cfg._stash[kind][name] is instance
            assert first_tokens[kind][name] is not instance
    suspended = f.graph.participants_evidence()["suspended"]
    assert len(suspended) == len(tools) + len(providers)
    assert all(item["occurrence"] is not None for item in suspended)
    event = {"tool_name": "fixture", "tool_call_id": "call", "result": "safe"}
    result = await f.bridge(event, hooks=f.view.hooks, coordinator=f.view)
    assert result["complete"] and result["result"] is None

    with f.graph.transition() as enabled:
        assert enabled.participant("host.configurator") is f.view
        if by_module:
            assert await cfg.tool_enable_module("tool-fixture") == tools
        else:
            for name in tools:
                await cfg.tool_enable(name)
        for name in providers:
            await cfg.provider_enable(name)
        cfg.config_set("policy.redact", False)
        lifecycle = media_mount_lifecycle(f.view)
        for tokens in first_tokens.values():
            for token in tokens.values():
                with pytest.raises(ValueError, match="consumed"):
                    await lifecycle.restore(token)
    assert not f.bridge.validate_receipt(receipt)
    assert f.bridge.validate_receipt(f.bridge.graph_receipt)
    for kind, instances in f.mounted.items():
        assert cfg._stash[kind] == cfg._state._mount_suspensions[kind] == {}
        for name, instance in instances.items():
            assert f.raw.get(kind, name) is instance
    result = await f.bridge(event, hooks=f.view.hooks, coordinator=f.view)
    assert result["complete"] and result["result"] == "safe"

    # A second disable creates fresh tokens in a genuinely new generation.
    with enabled.transition() as disabled:
        assert await cfg.apply_saved_settings(settings) == []
    for kind, tokens in cfg._state._mount_suspensions.items():
        assert all(token is not first_tokens[kind][name] for name, token in tokens.items())
    assert disabled.participant("host.configurator") is f.view
    result = await f.bridge(event, hooks=f.view.hooks, coordinator=f.view)
    assert result["complete"] and result["result"] is None
    assert calls == ["tool:post"] * 3
    assert cfg._session is f.session and cfg._coordinator is f.view
    assert f.session.coordinator is f.raw


@requires_core2
@pytest.mark.asyncio
@pytest.mark.parametrize("configured_graph", [("account-A",)], indirect=True)
@pytest.mark.parametrize("kind", ["tools", "providers"])
async def test_configured_restore_refusal_preserves_stash_and_token(
    configured_graph, kind
):
    f = configured_graph
    name, instance = next(iter(f.mounted[kind].items()))
    disable = f.configurator.tool_disable if kind == "tools" else f.configurator.provider_disable
    enable = f.configurator.tool_enable if kind == "tools" else f.configurator.provider_enable
    with f.graph.configure():
        await disable(name)
    f.graph.seal()
    state = f.configurator._state
    token = state._mount_suspensions[kind][name]
    with pytest.raises(ValueError):
        with f.graph.transition():
            f.session.config[kind][0]["config"] = {"changed": True}
            with pytest.raises(ValueError, match="changed"):
                await enable(name)
            assert state._mount_suspensions[kind][name] is token
            assert state._stash[kind][name] is instance
            assert f.raw.get(kind, name) is None  # no fallback mount
    assert not (await f.bridge({"tool_name": "fixture", "result": "safe"}))["complete"]


@requires_core2
@pytest.mark.asyncio
@pytest.mark.parametrize("configured_graph", [(None,)], indirect=True)
@pytest.mark.parametrize("kind", ["tools", "providers"])
async def test_untracked_failed_suspension_cannot_restore_from_object_stash(
    configured_graph, kind
):
    f = configured_graph
    name, instance = next(iter(f.mounted[kind].items()))
    disable = f.configurator.tool_disable if kind == "tools" else f.configurator.provider_disable
    enable = f.configurator.tool_enable if kind == "tools" else f.configurator.provider_enable
    receipt = f.graph.seal()
    # No idle issuer phase: no effect was admitted, so evidence is not a token.
    with pytest.raises(ValueError, match="idle"):
        await disable(name)
    assert f.configurator._stash[kind][name] is instance
    assert f.configurator._state._mount_suspensions[kind] == {}
    with f.graph.transition():
        with pytest.raises(RuntimeError, match="did not complete"):
            await enable(name)
        with pytest.raises(RuntimeError, match="did not complete"):
            await disable(name)
        warnings = await f.configurator.apply_saved_settings({
            "disabled": {kind: [name]},
        })
        assert len(warnings) == 1 and "Failed to disable" in warnings[0]
    assert not f.bridge.validate_receipt(receipt)
    assert f.raw.get(kind, name) is instance


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["tools", "providers"])
@pytest.mark.parametrize("error_type", [RuntimeError, asyncio.CancelledError])
async def test_lifecycle_restore_failure_or_cancel_never_falls_back_or_consumes_token(
    monkeypatch, routed_coordinator, kind, error_type
):
    """Adapter boundary injection tests owner bookkeeping, not native effects."""
    token = object()
    lifecycle = SimpleNamespace(
        suspend=AsyncMock(return_value=token),
        restore=AsyncMock(side_effect=error_type),
    )
    monkeypatch.setattr(
        "amplifier_foundation.session.media_hook_bridge.media_mount_lifecycle",
        lambda coordinator: lifecycle,
    )
    cfg = SessionConfigurator(
        SimpleNamespace(coordinator=object()),
        SimpleNamespace(bundle=Bundle(name="fixture")),
        coordinator=routed_coordinator,
    )
    instance = routed_coordinator.get(kind)["fixture"]
    disable = cfg.tool_disable if kind == "tools" else cfg.provider_disable
    enable = cfg.tool_enable if kind == "tools" else cfg.provider_enable
    await disable("fixture")
    with pytest.raises(error_type):
        await enable("fixture")
    assert cfg._stash[kind]["fixture"] is instance
    assert cfg._state._mount_suspensions[kind]["fixture"] is token
    lifecycle.restore.assert_awaited_once_with(token)
    routed_coordinator.mount.assert_not_awaited()
    routed_coordinator.unmount.assert_not_awaited()


@requires_core2
@pytest.mark.asyncio
@pytest.mark.parametrize("configured_graph", [("account-A", "account-B")], indirect=True)
async def test_configured_account_suspension_preserves_other_account(configured_graph):
    f = configured_graph
    cfg = f.configurator
    first, second = (f.mounted["providers"][name] for name in ("account-A", "account-B"))
    with f.graph.configure():
        await cfg.provider_disable("account-A")
    f.graph.seal()
    assert f.raw.get("providers", "account-A") is None
    assert f.raw.get("providers", "account-B") is second
    with f.graph.transition():
        await cfg.provider_enable("account-A")
    assert f.raw.get("providers", "account-A") is first
    assert f.raw.get("providers", "account-B") is second
    assert f.bridge.validate_receipt(f.bridge.graph_receipt)


@requires_core2
@pytest.mark.asyncio
@pytest.mark.parametrize("configured_graph", [(None,)], indirect=True)
async def test_configured_behavior_toggles_use_issuer_suspensions(configured_graph):
    f = configured_graph
    cfg = f.configurator
    cfg._bundle.origins = {
        "tool:tool-fixture": ["fixture-behavior"],
        "provider:provider-fixture": ["fixture-behavior"],
    }
    with f.graph.configure():
        result = await cfg.behavior_disable("fixture-behavior")
    assert result["warnings"] == []
    assert set(result["disabled"]) == {
        "tool:tool-fixture", "provider:provider-fixture",
    }
    f.graph.seal()
    assert f.raw.get("tools") == f.raw.get("providers") == {}
    with f.graph.transition():
        result = await cfg.behavior_enable("fixture-behavior")
    assert result["warnings"] == []
    for kind, instances in f.mounted.items():
        for name, instance in instances.items():
            assert f.raw.get(kind, name) is instance
    assert f.bridge.validate_receipt(f.bridge.graph_receipt)


@pytest.mark.asyncio
@pytest.mark.parametrize("error_type", [RuntimeError, asyncio.CancelledError])
async def test_lifecycle_module_restore_failure_is_not_silently_successful(
    monkeypatch, routed_coordinator, error_type
):
    token = object()
    lifecycle = SimpleNamespace(
        suspend=AsyncMock(return_value=token),
        restore=AsyncMock(side_effect=error_type),
    )
    monkeypatch.setattr(
        "amplifier_foundation.session.media_hook_bridge.media_mount_lifecycle",
        lambda coordinator: lifecycle,
    )
    cfg = SessionConfigurator(
        SimpleNamespace(coordinator=object()),
        SimpleNamespace(bundle=Bundle(name="fixture")),
        coordinator=routed_coordinator,
    )
    await cfg.tool_disable_module("tool-fixture")
    with pytest.raises(error_type):
        await cfg.tool_enable_module("tool-fixture")
    assert cfg._state._mount_suspensions["tools"]["fixture"] is token
    assert "fixture" in cfg._stash["tools"]
    routed_coordinator.mount.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["tools", "providers"])
@pytest.mark.parametrize("error_type", [RuntimeError, asyncio.CancelledError])
async def test_lifecycle_failed_suspend_retains_evidence_not_authority(
    monkeypatch, routed_coordinator, kind, error_type
):
    lifecycle = SimpleNamespace(
        suspend=AsyncMock(side_effect=error_type), restore=AsyncMock(),
    )
    monkeypatch.setattr(
        "amplifier_foundation.session.media_hook_bridge.media_mount_lifecycle",
        lambda coordinator: lifecycle,
    )
    cfg = SessionConfigurator(
        SimpleNamespace(coordinator=object()),
        SimpleNamespace(bundle=Bundle(name="fixture")),
        coordinator=routed_coordinator,
    )
    instance = routed_coordinator.get(kind)["fixture"]
    disable = cfg.tool_disable if kind == "tools" else cfg.provider_disable
    enable = cfg.tool_enable if kind == "tools" else cfg.provider_enable
    with pytest.raises(error_type):
        await disable("fixture")
    assert cfg._stash[kind]["fixture"] is instance
    assert cfg._state._mount_suspensions[kind] == {}
    with pytest.raises(RuntimeError, match="did not complete"):
        await enable("fixture")
    lifecycle.restore.assert_not_awaited()
    routed_coordinator.mount.assert_not_awaited()
    routed_coordinator.unmount.assert_not_awaited()


@requires_core2
@pytest.mark.asyncio
@pytest.mark.parametrize("configured_graph", [("account-A",)], indirect=True)
@pytest.mark.parametrize("kind", ["tools", "providers"])
@pytest.mark.parametrize("operation", ["suspend", "restore"])
async def test_actual_cancelled_configurator_retains_evidence_without_replay(
    configured_graph, kind, operation
):
    f = configured_graph
    cfg = f.configurator
    name, instance = next(iter(f.mounted[kind].items()))
    disable = cfg.tool_disable if kind == "tools" else cfg.provider_disable
    enable = cfg.tool_enable if kind == "tools" else cfg.provider_enable
    graph = f.graph
    prior_token = None
    if operation == "restore":
        with graph.configure():
            await disable(name)
        prior_token = cfg._state._mount_suspensions[kind][name]
    old_receipt = graph.seal()
    with pytest.raises(asyncio.CancelledError):
        with graph.transition() as interrupted:
            task = asyncio.create_task(disable(name) if operation == "suspend" else enable(name))
            asyncio.get_running_loop().call_soon(task.cancel)
            await task
    # Settlement observation only: never replay an interrupted native effect.
    for _ in range(20):
        if f.raw.get(kind, name) is (None if operation == "suspend" else instance):
            break
        await asyncio.sleep(0.01)
    assert f.raw.get(kind, name) is (None if operation == "suspend" else instance)
    assert cfg._stash[kind][name] is instance
    tokens = cfg._state._mount_suspensions[kind]
    if operation == "suspend":
        assert name not in tokens
    else:
        assert tokens[name] is prior_token
    evidence = interrupted.participants_evidence()["suspended"]
    assert len(evidence) == 1
    assert evidence[0]["module"] is instance and evidence[0]["state"] == "interrupted"
    assert not f.bridge.validate_receipt(old_receipt)
    assert not (await f.bridge({"tool_name": "fixture", "result": "safe"}))["complete"]
    # Retained tokens/evidence cannot make later requests report success or
    # dispatch a replay, even when interrupted restore actually remounted it.
    for action in (disable, enable):
        with pytest.raises(RuntimeError, match="did not complete"):
            await action(name)
    if kind == "tools":
        with pytest.raises(RuntimeError, match="did not complete"):
            await cfg.tool_disable_module("tool-fixture")
        with pytest.raises(RuntimeError, match="did not complete"):
            await cfg.tool_enable_module("tool-fixture")
    warnings = await cfg.apply_saved_settings({"disabled": {kind: [name]}})
    assert len(warnings) == 1 and "Failed to disable" in warnings[0]
    assert f.raw.get(kind, name) is (None if operation == "suspend" else instance)
