"""Tests for SessionConfigurator core constructor and stash."""

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from amplifier_foundation.bundle import Bundle
from amplifier_foundation.configurator import SessionConfigurator


@pytest.fixture
def mock_bundle() -> Bundle:
    """Bundle with context, tools, hooks, providers, agents, and _provenance."""
    bundle = Bundle(
        name="test-bundle",
        context={"readme": Path("/tmp/readme.md")},
        tools=[{"module": "tool-bash"}],
        hooks=[{"module": "hooks-logging"}],
        providers=[{"module": "provider-anthropic"}],
        agents={"my-agent": {"description": "Test agent"}},
    )
    bundle._provenance = {"context:readme": "test-behavior"}  # type: ignore[misc]
    return bundle


@pytest.fixture
def mock_coordinator(mock_bundle: Bundle) -> MagicMock:
    """MagicMock coordinator with config dict containing agents, async mount/unmount,
    and hooks registry with _handlers."""
    coordinator = MagicMock()
    coordinator.config = {"agents": {"my-agent": {}}}

    # Set up hooks registry with _handlers
    handler_func = MagicMock()
    coordinator.hooks._handlers = {
        "on_before_tool": {
            "event": "before_tool",
            "handler": handler_func,
            "priority": 10,
        },
        "on_after_tool": {
            "event": "after_tool",
            "handler": handler_func,
            "priority": 5,
        },
    }

    # Async mount/unmount
    coordinator.mount = MagicMock()
    coordinator.unmount = MagicMock()

    return coordinator


@pytest.fixture
def mock_session(mock_coordinator: MagicMock) -> MagicMock:
    """MagicMock session wrapping coordinator."""
    session = MagicMock()
    session.coordinator = mock_coordinator
    return session


@pytest.fixture
def mock_prepared_bundle(mock_bundle: Bundle) -> MagicMock:
    """MagicMock prepared bundle wrapping bundle."""
    prepared = MagicMock()
    prepared.bundle = mock_bundle
    return prepared


@pytest.fixture
def configurator(
    mock_session: MagicMock, mock_prepared_bundle: MagicMock
) -> SessionConfigurator:
    """Configured SessionConfigurator instance."""
    return SessionConfigurator(
        session=mock_session, prepared_bundle=mock_prepared_bundle
    )


class TestContextToggle:
    """Tests for context_disable and context_enable methods."""

    def test_disable_removes_from_bundle_context(
        self, configurator: SessionConfigurator, mock_bundle: Bundle
    ) -> None:
        """Disabling context removes it from bundle.context and stashes it."""
        assert "readme" in mock_bundle.context
        original_path = mock_bundle.context["readme"]

        configurator.context_disable("readme")

        assert "readme" not in mock_bundle.context
        assert configurator._stash["context"]["readme"] == original_path

    def test_enable_restores_from_stash(
        self, configurator: SessionConfigurator, mock_bundle: Bundle
    ) -> None:
        """Enabling a disabled context restores it from stash to bundle.context."""
        original_path = mock_bundle.context["readme"]
        configurator.context_disable("readme")

        assert "readme" not in mock_bundle.context

        configurator.context_enable("readme")

        assert "readme" in mock_bundle.context
        assert mock_bundle.context["readme"] == original_path
        assert "readme" not in configurator._stash["context"]

    def test_disable_unknown_raises_value_error(
        self, configurator: SessionConfigurator
    ) -> None:
        """Disabling an unknown context name raises ValueError with 'not found' message."""
        with pytest.raises(ValueError, match="not found"):
            configurator.context_disable("nonexistent")

    def test_disable_is_idempotent(
        self, configurator: SessionConfigurator, mock_bundle: Bundle
    ) -> None:
        """Disabling an already-disabled context is a no-op (idempotent)."""
        configurator.context_disable("readme")
        # Second call should not raise or change state
        configurator.context_disable("readme")
        assert "readme" not in mock_bundle.context

    def test_enable_already_enabled_is_noop(
        self, configurator: SessionConfigurator, mock_bundle: Bundle
    ) -> None:
        """Enabling an already-enabled context is a no-op."""
        original_path = mock_bundle.context["readme"]
        # Should not raise — readme is already in bundle.context
        configurator.context_enable("readme")
        assert "readme" in mock_bundle.context
        assert mock_bundle.context["readme"] == original_path


class TestSessionConfiguratorConstructor:
    """Tests for SessionConfigurator constructor."""

    def test_stash_initialized_with_five_empty_categories(
        self, configurator: SessionConfigurator
    ) -> None:
        """Stash is initialized with 5 empty category dicts."""
        assert hasattr(configurator, "_stash")
        assert set(configurator._stash.keys()) == {
            "context",
            "tools",
            "hooks",
            "providers",
            "agents",
        }
        for category in ["context", "tools", "hooks", "providers", "agents"]:
            assert configurator._stash[category] == {}

    def test_hook_snapshot_captures_handler_info(
        self, configurator: SessionConfigurator, mock_coordinator: MagicMock
    ) -> None:
        """Hook snapshot captures handler info from coordinator."""
        assert hasattr(configurator, "_hook_snapshot")
        # Should have copied both handlers
        assert "on_before_tool" in configurator._hook_snapshot
        assert "on_after_tool" in configurator._hook_snapshot
        # Verify the entries match what was in _handlers
        assert configurator._hook_snapshot["on_before_tool"]["event"] == "before_tool"
        assert configurator._hook_snapshot["on_before_tool"]["priority"] == 10
        assert configurator._hook_snapshot["on_after_tool"]["event"] == "after_tool"
        assert configurator._hook_snapshot["on_after_tool"]["priority"] == 5

    def test_stored_references(
        self,
        configurator: SessionConfigurator,
        mock_session: MagicMock,
        mock_coordinator: MagicMock,
        mock_bundle: Bundle,
        mock_prepared_bundle: MagicMock,
    ) -> None:
        """Session, coordinator, and bundle references are stored correctly."""
        assert configurator._session is mock_session
        assert configurator._coordinator is mock_coordinator
        assert configurator._bundle is mock_bundle
        assert configurator._prepared_bundle is mock_prepared_bundle


class TestAgentToggle:
    """Tests for agent_disable and agent_enable methods."""

    def test_disable_removes_from_config(
        self, configurator: SessionConfigurator, mock_coordinator: MagicMock
    ) -> None:
        """Disabling agent removes it from coordinator.config['agents'] and stashes it."""
        assert "my-agent" in mock_coordinator.config["agents"]
        original_config = mock_coordinator.config["agents"]["my-agent"]

        configurator.agent_disable("my-agent")

        assert "my-agent" not in mock_coordinator.config["agents"]
        assert configurator._stash["agents"]["my-agent"] == original_config

    def test_enable_restores_from_stash(
        self, configurator: SessionConfigurator, mock_coordinator: MagicMock
    ) -> None:
        """Enabling a disabled agent restores it from stash to coordinator.config['agents']."""
        original_config = mock_coordinator.config["agents"]["my-agent"]
        configurator.agent_disable("my-agent")

        assert "my-agent" not in mock_coordinator.config["agents"]

        configurator.agent_enable("my-agent")

        assert "my-agent" in mock_coordinator.config["agents"]
        assert mock_coordinator.config["agents"]["my-agent"] == original_config
        assert "my-agent" not in configurator._stash["agents"]

    def test_disable_unknown_raises_value_error(
        self, configurator: SessionConfigurator
    ) -> None:
        """Disabling an unknown agent raises ValueError with 'not found' message."""
        with pytest.raises(ValueError, match="not found"):
            configurator.agent_disable("nonexistent-agent")


# ---------------------------------------------------------------------------
# Fixtures for async tool toggle tests
# ---------------------------------------------------------------------------


@pytest.fixture
def async_coordinator(mock_bundle: Bundle) -> MagicMock:
    """MagicMock coordinator with AsyncMock for mount/unmount (required for tool tests)."""
    coordinator = MagicMock()
    coordinator.config = {"agents": {"my-agent": {}}}

    handler_func = MagicMock()
    coordinator.hooks._handlers = {
        "on_before_tool": {
            "event": "before_tool",
            "handler": handler_func,
            "priority": 10,
        },
        "on_after_tool": {
            "event": "after_tool",
            "handler": handler_func,
            "priority": 5,
        },
    }

    # Async mount/unmount — must be AsyncMock so they can be awaited.
    tool_instance = MagicMock(name="tool-instance")
    coordinator.mount = AsyncMock()
    coordinator.unmount = AsyncMock(return_value=tool_instance)

    return coordinator


@pytest.fixture
def async_session(async_coordinator: MagicMock) -> MagicMock:
    """MagicMock session wrapping async_coordinator."""
    session = MagicMock()
    session.coordinator = async_coordinator
    return session


@pytest.fixture
def async_configurator(
    async_session: MagicMock, mock_prepared_bundle: MagicMock
) -> SessionConfigurator:
    """SessionConfigurator instance with async-capable coordinator."""
    return SessionConfigurator(
        session=async_session, prepared_bundle=mock_prepared_bundle
    )


class TestToolToggle:
    """Tests for async tool_disable and tool_enable methods."""

    @pytest.mark.asyncio
    async def test_disable_calls_unmount(
        self, async_configurator: SessionConfigurator, async_coordinator: MagicMock
    ) -> None:
        """Disabling a tool calls coordinator.unmount('tools', name)."""
        await async_configurator.tool_disable("tool-bash")

        async_coordinator.unmount.assert_called_once_with("tools", "tool-bash")

    @pytest.mark.asyncio
    async def test_disable_stashes_instance(
        self, async_configurator: SessionConfigurator, async_coordinator: MagicMock
    ) -> None:
        """Disabling a tool stashes the instance returned by unmount."""
        expected_instance = async_coordinator.unmount.return_value

        await async_configurator.tool_disable("tool-bash")

        assert async_configurator._stash["tools"]["tool-bash"] is expected_instance

    @pytest.mark.asyncio
    async def test_enable_remounts_from_stash(
        self, async_configurator: SessionConfigurator, async_coordinator: MagicMock
    ) -> None:
        """Enabling a disabled tool calls coordinator.mount with the stashed instance and clears stash."""
        await async_configurator.tool_disable("tool-bash")
        stashed_instance = async_configurator._stash["tools"]["tool-bash"]

        await async_configurator.tool_enable("tool-bash")

        async_coordinator.mount.assert_called_once_with(
            "tools", "tool-bash", stashed_instance
        )
        assert "tool-bash" not in async_configurator._stash["tools"]

    @pytest.mark.asyncio
    async def test_enable_without_stash_raises_value_error(
        self, async_configurator: SessionConfigurator
    ) -> None:
        """Enabling a tool without prior disable raises ValueError with 'not in stash'."""
        with pytest.raises(ValueError, match="not in stash"):
            await async_configurator.tool_enable("tool-bash")


class TestProviderToggle:
    """Tests for async provider_disable and provider_enable methods."""

    @pytest.mark.asyncio
    async def test_disable_calls_unmount(
        self, async_configurator: SessionConfigurator, async_coordinator: MagicMock
    ) -> None:
        """Disabling a provider calls coordinator.unmount('providers', name)."""
        await async_configurator.provider_disable("provider-anthropic")

        async_coordinator.unmount.assert_called_once_with(
            "providers", "provider-anthropic"
        )

    @pytest.mark.asyncio
    async def test_enable_remounts_from_stash(
        self, async_configurator: SessionConfigurator, async_coordinator: MagicMock
    ) -> None:
        """Enabling a disabled provider calls coordinator.mount with the stashed instance and clears stash."""
        await async_configurator.provider_disable("provider-anthropic")
        stashed_instance = async_configurator._stash["providers"]["provider-anthropic"]

        await async_configurator.provider_enable("provider-anthropic")

        async_coordinator.mount.assert_called_once_with(
            "providers", "provider-anthropic", stashed_instance
        )
        assert "provider-anthropic" not in async_configurator._stash["providers"]

    @pytest.mark.asyncio
    async def test_enable_without_stash_raises_value_error(
        self, async_configurator: SessionConfigurator
    ) -> None:
        """Enabling a provider without prior disable raises ValueError with 'not in stash'."""
        with pytest.raises(ValueError, match="not in stash"):
            await async_configurator.provider_enable("provider-anthropic")


class TestConfigGetSet:
    """Tests for config_get and config_set methods."""

    def test_config_get_reads_nested_value(
        self, configurator: SessionConfigurator, mock_coordinator: MagicMock
    ) -> None:
        """config_get returns the nested value from coordinator.config using dot-path."""
        mock_coordinator.config["settings"] = {"model": {"name": "claude-3"}}

        result = configurator.config_get("settings.model.name")

        assert result == "claude-3"

    def test_config_get_returns_none_for_missing_path(
        self, configurator: SessionConfigurator
    ) -> None:
        """config_get returns None when the path does not exist."""
        result = configurator.config_get("nonexistent.nested.key")

        assert result is None

    def test_config_set_mutates_coordinator_config(
        self, configurator: SessionConfigurator, mock_coordinator: MagicMock
    ) -> None:
        """config_set mutates the actual coordinator.config dict at the given path."""
        configurator.config_set("settings.timeout", 30)

        assert mock_coordinator.config["settings"]["timeout"] == 30

    def test_config_set_tracks_override_in_config_overrides(
        self, configurator: SessionConfigurator
    ) -> None:
        """config_set records the override path and value in _config_overrides."""
        configurator.config_set("settings.timeout", 30)

        assert configurator._config_overrides["settings.timeout"] == 30


class TestHookToggle:
    """Tests for hook_disable and hook_enable methods."""

    def test_disable_calls_unregister(
        self, configurator: SessionConfigurator, mock_coordinator: MagicMock
    ) -> None:
        """Disabling a hook calls coordinator.hooks.unregister(name) and stashes marker."""
        configurator.hook_disable("on_before_tool")

        mock_coordinator.hooks.unregister.assert_called_once_with("on_before_tool")
        assert configurator._stash["hooks"]["on_before_tool"] is True

    def test_enable_reregisters_from_snapshot(
        self, configurator: SessionConfigurator, mock_coordinator: MagicMock
    ) -> None:
        """Enabling a disabled hook calls coordinator.hooks.register with event, handler, priority, and name from snapshot."""
        configurator.hook_disable("on_before_tool")
        mock_coordinator.hooks.register.reset_mock()

        configurator.hook_enable("on_before_tool")

        snapshot_info = configurator._hook_snapshot["on_before_tool"]
        mock_coordinator.hooks.register.assert_called_once_with(
            snapshot_info["event"],
            snapshot_info["handler"],
            priority=snapshot_info["priority"],
            name="on_before_tool",
        )
        assert "on_before_tool" not in configurator._stash["hooks"]

    def test_disable_unknown_raises_value_error(
        self, configurator: SessionConfigurator
    ) -> None:
        """Disabling a hook not in snapshot raises ValueError with 'not found in snapshot'."""
        with pytest.raises(ValueError, match="not found in snapshot"):
            configurator.hook_disable("nonexistent_hook")

    def test_enable_without_stash_raises_value_error(
        self, configurator: SessionConfigurator
    ) -> None:
        """Enabling a hook without prior disable raises ValueError with 'not in stash'."""
        with pytest.raises(ValueError, match="not in stash"):
            configurator.hook_enable("on_before_tool")


class TestSnapshotAndDiff:
    """Tests for snapshot() and diff_from_original() methods."""

    def test_snapshot_captures_initial_state(
        self,
        configurator: SessionConfigurator,
        mock_bundle: Bundle,
        mock_coordinator: MagicMock,
    ) -> None:
        """snapshot() returns enabled/disabled lists for all 5 categories."""
        snap = configurator.snapshot()

        assert set(snap.keys()) == {"context", "tools", "hooks", "providers", "agents"}

        # context: "readme" is enabled, none disabled
        assert "readme" in snap["context"]["enabled"]
        assert snap["context"]["disabled"] == []

        # agents: "my-agent" is enabled, none disabled
        assert "my-agent" in snap["agents"]["enabled"]
        assert snap["agents"]["disabled"] == []

        # tools: "tool-bash" is enabled, none disabled
        assert "tool-bash" in snap["tools"]["enabled"]
        assert snap["tools"]["disabled"] == []

        # providers: "provider-anthropic" is enabled, none disabled
        assert "provider-anthropic" in snap["providers"]["enabled"]
        assert snap["providers"]["disabled"] == []

        # hooks: "on_before_tool" and "on_after_tool" are enabled, none disabled
        assert "on_before_tool" in snap["hooks"]["enabled"]
        assert "on_after_tool" in snap["hooks"]["enabled"]
        assert snap["hooks"]["disabled"] == []

    def test_snapshot_reflects_disables(
        self,
        configurator: SessionConfigurator,
    ) -> None:
        """After disabling items, snapshot shows them as disabled."""
        configurator.context_disable("readme")
        configurator.agent_disable("my-agent")
        configurator.hook_disable("on_before_tool")

        snap = configurator.snapshot()

        assert "readme" in snap["context"]["disabled"]
        assert "readme" not in snap["context"]["enabled"]

        assert "my-agent" in snap["agents"]["disabled"]
        assert "my-agent" not in snap["agents"]["enabled"]

        assert "on_before_tool" in snap["hooks"]["disabled"]
        assert "on_before_tool" not in snap["hooks"]["enabled"]

    def test_diff_identifies_changes(
        self,
        configurator: SessionConfigurator,
    ) -> None:
        """diff_from_original() returns changes compared to the original snapshot."""
        # Original snapshot is captured in __init__; now disable some items
        configurator.context_disable("readme")
        configurator.agent_disable("my-agent")
        configurator.hook_disable("on_before_tool")

        diff = configurator.diff_from_original()

        disabled_by_name = {d["name"]: d for d in diff if d["action"] == "disabled"}
        assert "readme" in disabled_by_name
        assert disabled_by_name["readme"]["category"] == "context"
        assert "my-agent" in disabled_by_name
        assert disabled_by_name["my-agent"]["category"] == "agents"
        assert "on_before_tool" in disabled_by_name
        assert disabled_by_name["on_before_tool"]["category"] == "hooks"

    def test_diff_empty_when_no_changes(
        self,
        configurator: SessionConfigurator,
    ) -> None:
        """diff_from_original() returns empty list when nothing has changed."""
        diff = configurator.diff_from_original()
        assert diff == []

    def test_diff_without_snapshot_returns_empty(
        self,
        configurator: SessionConfigurator,
    ) -> None:
        """diff_from_original() returns empty list when _original_snapshot is None."""
        configurator._original_snapshot = None  # type: ignore[assignment]
        diff = configurator.diff_from_original()
        assert diff == []


# ---------------------------------------------------------------------------
# Fixtures for behavior toggle tests
# ---------------------------------------------------------------------------


@pytest.fixture
def behavior_bundle() -> Bundle:
    """Bundle with multiple provenance entries for a single behavior."""
    bundle = Bundle(
        name="behavior-bundle",
        context={"readme": Path("/tmp/readme.md")},
        tools=[{"module": "tool-bash"}],
        hooks=[{"module": "hooks-logging"}],
        providers=[{"module": "provider-anthropic"}],
        agents={"my-agent": {"description": "Test agent"}},
    )
    bundle._provenance = {  # type: ignore[misc]
        "context:readme": "my-behavior",
        "tools:tool-bash": "my-behavior",
        "hooks:on_before_tool": "my-behavior",
        "agents:my-agent": "my-behavior",
    }
    return bundle


@pytest.fixture
def behavior_coordinator(behavior_bundle: Bundle) -> MagicMock:
    """Async coordinator for behavior tests with all required mocks."""
    coordinator = MagicMock()
    coordinator.config = {"agents": {"my-agent": {}}}

    handler_func = MagicMock()
    coordinator.hooks._handlers = {
        "on_before_tool": {
            "event": "before_tool",
            "handler": handler_func,
            "priority": 10,
        },
    }

    tool_instance = MagicMock(name="tool-instance")
    coordinator.mount = AsyncMock()
    coordinator.unmount = AsyncMock(return_value=tool_instance)

    return coordinator


@pytest.fixture
def behavior_session(behavior_coordinator: MagicMock) -> MagicMock:
    """MagicMock session wrapping behavior_coordinator."""
    session = MagicMock()
    session.coordinator = behavior_coordinator
    return session


@pytest.fixture
def behavior_prepared_bundle(behavior_bundle: Bundle) -> MagicMock:
    """MagicMock prepared bundle wrapping behavior_bundle."""
    prepared = MagicMock()
    prepared.bundle = behavior_bundle
    return prepared


@pytest.fixture
def behavior_configurator(
    behavior_session: MagicMock, behavior_prepared_bundle: MagicMock
) -> SessionConfigurator:
    """SessionConfigurator instance for behavior toggle tests."""
    return SessionConfigurator(
        session=behavior_session, prepared_bundle=behavior_prepared_bundle
    )


class TestBehaviorToggle:
    """Tests for behavior_disable and behavior_enable methods."""

    @pytest.mark.asyncio
    async def test_behavior_disable_disables_all_contributions(
        self,
        behavior_configurator: SessionConfigurator,
        behavior_bundle: Bundle,
        behavior_coordinator: MagicMock,
    ) -> None:
        """behavior_disable disables all items contributed by the named behavior."""
        result = await behavior_configurator.behavior_disable("my-behavior")

        # All provenance keys for the behavior should be in the disabled list
        assert set(result["disabled"]) == {
            "context:readme",
            "tools:tool-bash",
            "hooks:on_before_tool",
            "agents:my-agent",
        }
        assert result["warnings"] == []

        # Context item removed from bundle
        assert "readme" not in behavior_bundle.context

        # Tool unmounted
        behavior_coordinator.unmount.assert_any_call("tools", "tool-bash")

        # Hook unregistered
        behavior_coordinator.hooks.unregister.assert_any_call("on_before_tool")

        # Agent removed from coordinator.config
        assert "my-agent" not in behavior_coordinator.config["agents"]

    @pytest.mark.asyncio
    async def test_behavior_enable_restores_all(
        self,
        behavior_configurator: SessionConfigurator,
        behavior_bundle: Bundle,
        behavior_coordinator: MagicMock,
    ) -> None:
        """behavior_enable restores all contributions after behavior_disable."""
        await behavior_configurator.behavior_disable("my-behavior")

        result = await behavior_configurator.behavior_enable("my-behavior")

        # All provenance keys should be in the enabled list
        assert set(result["enabled"]) == {
            "context:readme",
            "tools:tool-bash",
            "hooks:on_before_tool",
            "agents:my-agent",
        }

        # Context item restored to bundle
        assert "readme" in behavior_bundle.context

        # Tool stash cleared (was remounted)
        assert "tool-bash" not in behavior_configurator._stash["tools"]

        # Hook stash cleared (was re-registered)
        assert "on_before_tool" not in behavior_configurator._stash["hooks"]

        # Agent restored to coordinator.config
        assert "my-agent" in behavior_coordinator.config["agents"]

    @pytest.mark.asyncio
    async def test_behavior_disable_unknown_raises_value_error(
        self,
        behavior_configurator: SessionConfigurator,
    ) -> None:
        """behavior_disable raises ValueError with 'not found in provenance' for unknown name."""
        with pytest.raises(ValueError, match="not found in provenance"):
            await behavior_configurator.behavior_disable("unknown-behavior")

    @pytest.mark.asyncio
    async def test_behavior_disable_partial_failure_continues_with_warnings(
        self,
        behavior_configurator: SessionConfigurator,
        behavior_bundle: Bundle,
    ) -> None:
        """Partial failure during behavior_disable continues and collects warnings."""
        # Add a provenance entry for a context key that does NOT exist in the bundle
        behavior_bundle._provenance["context:nonexistent"] = "my-behavior"  # type: ignore[index]

        result = await behavior_configurator.behavior_disable("my-behavior")

        # The failing item produces a warning
        assert len(result["warnings"]) > 0

        # The successful items are still in the disabled list
        assert "context:readme" in result["disabled"]
        assert "agents:my-agent" in result["disabled"]

        # The failed item is NOT in disabled
        assert "context:nonexistent" not in result["disabled"]

    @pytest.mark.asyncio
    async def test_behavior_disable_tracks_behavior_name(
        self,
        behavior_configurator: SessionConfigurator,
    ) -> None:
        """behavior_disable adds the behavior name to _disabled_behaviors set."""
        assert "my-behavior" not in behavior_configurator._disabled_behaviors

        await behavior_configurator.behavior_disable("my-behavior")

        assert "my-behavior" in behavior_configurator._disabled_behaviors
