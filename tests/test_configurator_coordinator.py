"""Explicit coordinator routing, including real Core cooperative generations.

The native fixture uses an actual Rust session/registry with local module
fixtures, not a CLI/Unified host or provider/inference qualification.
"""

from importlib.metadata import version
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from amplifier_core import AmplifierSession, HookResult

from amplifier_foundation.bundle import Bundle
from amplifier_foundation.configurator import BundleStateManager, SessionConfigurator
from amplifier_foundation.configurator._inspector import BundleInspector
from amplifier_foundation.session.media_hook_bridge import install_media_hook_bridge


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
