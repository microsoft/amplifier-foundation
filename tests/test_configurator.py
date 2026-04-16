"""Tests for SessionConfigurator core constructor and stash."""

from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from amplifier_foundation.bundle import Bundle
from amplifier_foundation.configurator import SessionConfigurator
from amplifier_foundation.configurator import (
    _build_normalized_prov_lookup,
    _lookup_prov_behavior,
    _normalize_module_name,
)


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
    bundle._provenance = {"context:readme": ["test-behavior"]}  # type: ignore[misc]
    return bundle


@pytest.fixture
def mock_coordinator(mock_bundle: Bundle) -> MagicMock:
    """MagicMock coordinator with config dict containing agents, async mount/unmount,
    and hooks registry using the public list_handlers() API (no private _handlers)."""
    coordinator = MagicMock()
    coordinator.config = {"agents": {"my-agent": {}}}

    # Return None for hook_metadata capability so _capture_hooks() falls through
    # to the list_handlers() fallback path.
    coordinator.get_capability.return_value = None

    # Public list_handlers() returns {event: [name, ...]} — no callables, no priorities.
    coordinator.hooks.list_handlers.return_value = {
        "before_tool": ["on_before_tool"],
        "after_tool": ["on_after_tool"],
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

    def test_original_snapshot_initialized_before_take_snapshot(
        self, mock_session: MagicMock, mock_prepared_bundle: MagicMock
    ) -> None:
        """_original_snapshot is None before take_snapshot() runs.

        If take_snapshot() raises during __init__, diff_from_original()'s
        'if self._original_snapshot is None' guard must work without AttributeError.
        """
        from unittest.mock import patch

        captured_before: list = []

        def failing_take_snapshot(self: Any) -> None:
            captured_before.append(getattr(self, "_original_snapshot", "MISSING"))
            raise RuntimeError("simulated failure in take_snapshot")

        with patch.object(SessionConfigurator, "take_snapshot", failing_take_snapshot):
            with pytest.raises(RuntimeError, match="simulated failure"):
                SessionConfigurator(
                    session=mock_session, prepared_bundle=mock_prepared_bundle
                )

        assert len(captured_before) == 1, "take_snapshot should have been called once"
        assert captured_before[0] is None, (
            "_original_snapshot must be initialized to None before take_snapshot() runs; "
            "currently it is missing (AttributeError would occur in diff_from_original)"
        )

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
        """Hook snapshot captures event bindings from the public list_handlers() API.

        The public API returns {event: [names]} — no handler callables or priorities.
        Both hook names must be present in the snapshot; each entry has an 'event'
        key but no 'handler' key.
        """
        assert hasattr(configurator, "_hook_snapshot")
        # Both hook names present
        assert "on_before_tool" in configurator._hook_snapshot
        assert "on_after_tool" in configurator._hook_snapshot
        # Event is captured
        assert configurator._hook_snapshot["on_before_tool"]["event"] == "before_tool"
        assert configurator._hook_snapshot["on_after_tool"]["event"] == "after_tool"
        # Handler callables are NOT exposed by the public API
        assert "handler" not in configurator._hook_snapshot["on_before_tool"]
        assert "handler" not in configurator._hook_snapshot["on_after_tool"]

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

    # Return None for hook_metadata capability so _capture_hooks() falls through
    # to the list_handlers() fallback path.
    coordinator.get_capability.return_value = None

    # Public list_handlers() returns {event: [name, ...]} — no callables, no priorities.
    coordinator.hooks.list_handlers.return_value = {
        "before_tool": ["on_before_tool"],
        "after_tool": ["on_after_tool"],
    }

    # Async mount/unmount — must be AsyncMock so they can be awaited.
    # unmount() always returns None in the real Rust binding (module is deleted,
    # return value is discarded).  get() is synchronous and returns the mounted dict.
    tool_instance = MagicMock(name="tool-instance")
    provider_instance = MagicMock(name="provider-instance")
    coordinator.mount = AsyncMock()
    coordinator.unmount = AsyncMock(return_value=None)

    # get() returns the live mounted-module dict for a given mount-point.
    _mounts: dict[str, dict] = {
        "tools": {"tool-bash": tool_instance},
        "providers": {"provider-anthropic": provider_instance},
    }
    coordinator.get = MagicMock(side_effect=lambda mp: _mounts.get(mp))

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
        """Disabling a tool calls coordinator.unmount('tools', name=name)."""
        await async_configurator.tool_disable("tool-bash")

        async_coordinator.unmount.assert_called_once_with("tools", name="tool-bash")

    @pytest.mark.asyncio
    async def test_disable_stashes_instance(
        self, async_configurator: SessionConfigurator, async_coordinator: MagicMock
    ) -> None:
        """Disabling a tool stashes the instance retrieved from coordinator.get(), not unmount().

        The real Rust binding's unmount() always returns None.  The instance must be
        fetched via coordinator.get("tools") before unmounting.
        """
        await async_configurator.tool_disable("tool-bash")

        # Instance comes from coordinator.get("tools"), not from unmount() return value.
        expected_instance = async_coordinator.get("tools")["tool-bash"]
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
            "tools", stashed_instance, name="tool-bash"
        )
        assert "tool-bash" not in async_configurator._stash["tools"]

    @pytest.mark.asyncio
    async def test_enable_without_stash_raises_value_error(
        self, async_configurator: SessionConfigurator
    ) -> None:
        """Enabling a tool without prior disable raises ValueError with 'not in stash'."""
        with pytest.raises(ValueError, match="not in stash"):
            await async_configurator.tool_enable("tool-bash")

    @pytest.mark.asyncio
    async def test_disable_stashes_instance_even_when_unmount_raises(
        self, async_configurator: SessionConfigurator, async_coordinator: MagicMock
    ) -> None:
        """tool_disable stashes the instance even when coordinator.unmount() raises.

        If unmount raises, the instance must still be in the stash so state is consistent
        (the tool is considered disabled and can be re-enabled later).
        """
        async_coordinator.unmount.side_effect = RuntimeError("unmount failed")

        with pytest.raises(RuntimeError, match="unmount failed"):
            await async_configurator.tool_disable("tool-bash")

        # Instance must be stashed despite the unmount failure
        assert "tool-bash" in async_configurator._stash["tools"], (
            "Instance must be stashed in try/finally even when unmount raises"
        )

    @pytest.mark.asyncio
    async def test_enable_restashes_instance_when_mount_raises(
        self, async_configurator: SessionConfigurator, async_coordinator: MagicMock
    ) -> None:
        """tool_enable re-stashes the instance if coordinator.mount() raises.

        If mount raises, the instance must be put back in the stash so it's not lost.
        """
        # First disable successfully
        async_coordinator.unmount.side_effect = None  # unmount succeeds
        await async_configurator.tool_disable("tool-bash")
        assert "tool-bash" in async_configurator._stash["tools"]

        # Now make mount fail
        async_coordinator.mount.side_effect = RuntimeError("mount failed")

        with pytest.raises(RuntimeError, match="mount failed"):
            await async_configurator.tool_enable("tool-bash")

        # Instance must be back in stash after mount failure
        assert "tool-bash" in async_configurator._stash["tools"], (
            "Instance must be re-stashed in except block when mount raises"
        )


class TestProviderToggle:
    """Tests for async provider_disable and provider_enable methods."""

    @pytest.mark.asyncio
    async def test_disable_calls_unmount(
        self, async_configurator: SessionConfigurator, async_coordinator: MagicMock
    ) -> None:
        """Disabling a provider calls coordinator.unmount('providers', name=name)."""
        await async_configurator.provider_disable("provider-anthropic")

        async_coordinator.unmount.assert_called_once_with(
            "providers", name="provider-anthropic"
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
            "providers", stashed_instance, name="provider-anthropic"
        )
        assert "provider-anthropic" not in async_configurator._stash["providers"]

    @pytest.mark.asyncio
    async def test_enable_without_stash_raises_value_error(
        self, async_configurator: SessionConfigurator
    ) -> None:
        """Enabling a provider without prior disable raises ValueError with 'not in stash'."""
        with pytest.raises(ValueError, match="not in stash"):
            await async_configurator.provider_enable("provider-anthropic")

    @pytest.mark.asyncio
    async def test_disable_stashes_instance_even_when_unmount_raises(
        self, async_configurator: SessionConfigurator, async_coordinator: MagicMock
    ) -> None:
        """provider_disable stashes the instance even when coordinator.unmount() raises.

        If unmount raises, the instance must still be in the stash so state is consistent.
        """
        async_coordinator.unmount.side_effect = RuntimeError("unmount failed")

        with pytest.raises(RuntimeError, match="unmount failed"):
            await async_configurator.provider_disable("provider-anthropic")

        # Instance must be stashed despite the unmount failure
        assert "provider-anthropic" in async_configurator._stash["providers"], (
            "Instance must be stashed in try/finally even when unmount raises"
        )

    @pytest.mark.asyncio
    async def test_enable_restashes_instance_when_mount_raises(
        self, async_configurator: SessionConfigurator, async_coordinator: MagicMock
    ) -> None:
        """provider_enable re-stashes the instance if coordinator.mount() raises.

        If mount raises, the instance must be put back in the stash so it's not lost.
        """
        # First disable successfully
        async_coordinator.unmount.side_effect = None
        await async_configurator.provider_disable("provider-anthropic")
        assert "provider-anthropic" in async_configurator._stash["providers"]

        # Now make mount fail
        async_coordinator.mount.side_effect = RuntimeError("mount failed")

        with pytest.raises(RuntimeError, match="mount failed"):
            await async_configurator.provider_enable("provider-anthropic")

        # Instance must be back in stash after mount failure
        assert "provider-anthropic" in async_configurator._stash["providers"], (
            "Instance must be re-stashed in except block when mount raises"
        )


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
    """Tests for hook_disable and hook_enable methods.

    Hook toggle is not supported — a core suspend/resume API is needed.
    Both methods must log a warning and return silently (not raise).
    """

    def test_disable_returns_none_without_error(
        self, configurator: SessionConfigurator
    ) -> None:
        """hook_disable logs a warning and returns None — does NOT raise."""
        result = configurator.hook_disable("on_before_tool")
        assert result is None

    def test_enable_returns_none_without_error(
        self, configurator: SessionConfigurator
    ) -> None:
        """hook_enable logs a warning and returns None — does NOT raise."""
        result = configurator.hook_enable("on_before_tool")
        assert result is None

    def test_disable_any_name_returns_none(
        self, configurator: SessionConfigurator
    ) -> None:
        """hook_disable returns None for any hook name (including nonexistent ones)."""
        result = configurator.hook_disable("nonexistent_hook")
        assert result is None

    def test_enable_any_name_returns_none(
        self, configurator: SessionConfigurator
    ) -> None:
        """hook_enable returns None for any hook name (including nonexistent ones)."""
        result = configurator.hook_enable("nonexistent_hook")
        assert result is None

    def test_disable_logs_warning(
        self, configurator: SessionConfigurator, caplog: pytest.LogCaptureFixture
    ) -> None:
        """hook_disable emits a warning log mentioning the hook name."""
        import logging

        with caplog.at_level(logging.WARNING):
            configurator.hook_disable("on_before_tool")

        assert any(
            "on_before_tool" in record.message
            for record in caplog.records
            if record.levelno == logging.WARNING
        ), "Expected a WARNING log mentioning the hook name 'on_before_tool'"

    def test_enable_logs_warning(
        self, configurator: SessionConfigurator, caplog: pytest.LogCaptureFixture
    ) -> None:
        """hook_enable emits a warning log mentioning the hook name."""
        import logging

        with caplog.at_level(logging.WARNING):
            configurator.hook_enable("on_before_tool")

        assert any(
            "on_before_tool" in record.message
            for record in caplog.records
            if record.levelno == logging.WARNING
        ), "Expected a WARNING log mentioning the hook name 'on_before_tool'"


class TestHookCaptureGracefulFallback:
    """Tests for graceful degradation when hook handler callables are not available.

    In production the coordinator uses RustHookRegistry, whose public list_handlers()
    API returns {event: [names]} — no callables, no priorities.  These tests verify
    that hook_disable() still works in this scenario and that hook_enable() raises a
    clear RuntimeError rather than crashing with AttributeError or silently no-oping.
    """

    def test_snapshot_contains_event_not_handler(
        self, configurator: SessionConfigurator
    ) -> None:
        """Snapshot built from list_handlers() has event binding but no handler callable."""
        entry = configurator._hook_snapshot.get("on_before_tool", {})
        assert entry.get("event") == "before_tool", (
            "Event should be captured from list_handlers()"
        )
        assert "handler" not in entry, (
            "Handler callable must NOT be present — public API does not expose it"
        )

    def test_disable_returns_none_with_partial_metadata(
        self, configurator: SessionConfigurator
    ) -> None:
        """hook_disable returns None regardless of snapshot content — logs a warning."""
        result = configurator.hook_disable("on_before_tool")
        assert result is None

    def test_enable_returns_none(self, configurator: SessionConfigurator) -> None:
        """hook_enable returns None regardless of stash or snapshot state — logs a warning."""
        result = configurator.hook_enable("on_before_tool")
        assert result is None

    def test_empty_snapshot_when_coordinator_has_no_introspection(self) -> None:
        """Configurator initialises without error when coordinator has no hook introspection API.

        Guards against any coordinator implementation that lacks list_handlers() — the
        configurator should degrade to an empty snapshot rather than raising at __init__.
        """
        coordinator = MagicMock()
        coordinator.get_capability.return_value = None
        coordinator.config = {}
        # spec=["unregister"] means hasattr(coordinator.hooks, "list_handlers") is False
        coordinator.hooks = MagicMock(spec=["unregister"])

        bundle_mock = MagicMock()
        bundle_mock.context = {}
        bundle_mock.tools = []
        bundle_mock.providers = []

        prepared = MagicMock()
        prepared.bundle = bundle_mock

        session = MagicMock()
        session.coordinator = coordinator

        cfg = SessionConfigurator(session=session, prepared_bundle=prepared)

        # Should initialise cleanly with an empty hook snapshot.
        assert cfg._hook_snapshot == {}


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
        """After disabling items, snapshot shows them as disabled.

        Hooks are read-only and cannot be disabled, so only context and agents
        are tested here. Hooks always appear as enabled in the snapshot.
        """
        configurator.context_disable("readme")
        configurator.agent_disable("my-agent")

        snap = configurator.snapshot()

        assert "readme" in snap["context"]["disabled"]
        assert "readme" not in snap["context"]["enabled"]

        assert "my-agent" in snap["agents"]["disabled"]
        assert "my-agent" not in snap["agents"]["enabled"]

        # Hooks are always enabled (read-only) — stash is always empty
        assert snap["hooks"]["disabled"] == []
        assert "on_before_tool" in snap["hooks"]["enabled"]

    def test_diff_identifies_changes(
        self,
        configurator: SessionConfigurator,
    ) -> None:
        """diff_from_original() returns changes compared to the original snapshot.

        Hooks are read-only and cannot be disabled, so only context and agent
        changes appear in the diff.
        """
        # Original snapshot is captured in __init__; now disable some items
        configurator.context_disable("readme")
        configurator.agent_disable("my-agent")

        diff = configurator.diff_from_original()

        disabled_by_name = {d["name"]: d for d in diff if d["action"] == "disabled"}
        assert "readme" in disabled_by_name
        assert disabled_by_name["readme"]["category"] == "context"
        assert "my-agent" in disabled_by_name
        assert disabled_by_name["my-agent"]["category"] == "agents"
        # Hooks are always read-only — they never appear as "disabled" in the diff
        assert "on_before_tool" not in disabled_by_name

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
        "context:readme": ["my-behavior"],
        "tool:tool-bash": ["my-behavior"],
        "hook:on_before_tool": ["my-behavior"],
        "agent:my-agent": ["my-behavior"],
    }
    return bundle


@pytest.fixture
def behavior_coordinator(behavior_bundle: Bundle) -> MagicMock:
    """Async coordinator for behavior tests with all required mocks."""
    coordinator = MagicMock()
    coordinator.config = {"agents": {"my-agent": {}}}

    # Return None for hook_metadata capability so _capture_hooks() falls through
    # to the list_handlers() fallback path.
    coordinator.get_capability.return_value = None

    # Public list_handlers() returns {event: [name, ...]} — no callables, no priorities.
    coordinator.hooks.list_handlers.return_value = {
        "before_tool": ["on_before_tool"],
    }

    tool_instance = MagicMock(name="tool-instance")
    coordinator.mount = AsyncMock()
    coordinator.unmount = AsyncMock(return_value=None)

    # get() returns the live mounted-module dict for a given mount-point.
    _mounts: dict[str, dict] = {
        "tools": {"tool-bash": tool_instance},
    }
    coordinator.get = MagicMock(side_effect=lambda mp: _mounts.get(mp))

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
        """behavior_disable disables context and agent contributions; skips hooks and tools.

        Hooks are silently skipped (read-only).  Tools are skipped with a
        warning — shared ownership cannot be determined from provenance alone,
        so unmounting is not safe.  Only context and agent items appear in the
        disabled list.
        """
        result = await behavior_configurator.behavior_disable("my-behavior")

        # Context, agent, and sole-owner tool contributions all disabled.
        # Hooks are silently skipped (read-only).
        assert set(result["disabled"]) == {
            "context:readme",
            "agent:my-agent",
            "tool:tool-bash",
        }

        # No warnings — sole ownership resolved cleanly.
        assert result["warnings"] == []

        # Context item removed from bundle
        assert "readme" not in behavior_bundle.context

        # Tool IS unmounted — sole active claimant, safe to disable
        behavior_coordinator.unmount.assert_called()

        # Hook NOT unregistered — hooks are read-only
        behavior_coordinator.hooks.unregister.assert_not_called()

        # Agent removed from coordinator.config
        assert "my-agent" not in behavior_coordinator.config["agents"]

    @pytest.mark.asyncio
    async def test_behavior_enable_restores_all(
        self,
        behavior_configurator: SessionConfigurator,
        behavior_bundle: Bundle,
        behavior_coordinator: MagicMock,
    ) -> None:
        """behavior_enable restores context, agent, and sole-owner tool contributions.

        Hooks are silently skipped during both disable and enable — they are read-only
        in this version.  Tools that were stashed by behavior_disable (sole owner) are
        re-enabled and re-mounted here.
        """
        await behavior_configurator.behavior_disable("my-behavior")

        result = await behavior_configurator.behavior_enable("my-behavior")

        # Context, agent, and tool contributions are all restored.
        # Hooks are silently skipped (read-only).
        assert set(result["enabled"]) == {
            "context:readme",
            "agent:my-agent",
            "tool:tool-bash",
        }
        assert result["warnings"] == []

        # Context item restored to bundle
        assert "readme" in behavior_bundle.context

        # Tool stash is empty — tool was re-mounted from stash
        assert "tool-bash" not in behavior_configurator._stash["tools"]

        # Hook stash is empty — hooks are never toggled
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
        behavior_bundle._provenance["context:nonexistent"] = ["my-behavior"]  # type: ignore[index]

        result = await behavior_configurator.behavior_disable("my-behavior")

        # The failing item produces a warning
        assert len(result["warnings"]) > 0

        # The successful items are still in the disabled list
        assert "context:readme" in result["disabled"]
        assert "agent:my-agent" in result["disabled"]

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


class TestBehaviorToggleToolResolution:
    """Tests for Bug 1 (module ID resolution) and Bug 2 (shared tool skip) fixes
    in behavior_disable and behavior_enable.
    """

    def _make_behavior_cfg(
        self,
        provenance: dict,
        mounted_tools: dict,
    ) -> SessionConfigurator:
        """Helper: build a SessionConfigurator with a single-behavior bundle.

        Args:
            provenance: The bundle._provenance dict.
            mounted_tools: What coordinator.get('tools') returns.
        """
        coordinator = MagicMock()
        coordinator.get_capability.return_value = None
        coordinator.hooks.list_handlers.return_value = {}
        coordinator.mount = AsyncMock()
        coordinator.unmount = AsyncMock(return_value=None)
        coordinator.config = {"agents": {}}

        def _get(mp: str) -> dict:
            if mp == "tools":
                return mounted_tools
            return {}

        coordinator.get = MagicMock(side_effect=_get)

        bundle = Bundle(
            name="behavior-bundle",
            context={},
            tools=[],
            hooks=[],
            providers=[],
            agents={},
        )
        bundle._provenance = provenance  # type: ignore[misc]

        prepared = MagicMock()
        prepared.bundle = bundle

        session = MagicMock()
        session.coordinator = coordinator

        return SessionConfigurator(session=session, prepared_bundle=prepared)

    @pytest.mark.asyncio
    async def test_behavior_disable_sole_owner_resolves_module_id_to_mounted_name(
        self,
    ) -> None:
        """behavior_disable resolves module ID to mounted name when disabling sole-owner tool.

        When provenance stores 'tool:tool-bash' but the coordinator mounts the
        tool as 'bash', behavior_disable must call tool_disable('bash') (the
        correct mounted name), not 'tool-bash' (the raw module ID).

        The multi-claimant provenance check shows 'my-behavior' is the sole
        active claimant, so the tool IS disabled — no warning, no skip.
        """
        tool_instance = MagicMock(name="bash-instance")
        cfg = self._make_behavior_cfg(
            provenance={"tool:tool-bash": ["my-behavior"]},
            # Tool is mounted as the SHORT name 'bash', not the module ID 'tool-bash'.
            mounted_tools={"bash": tool_instance},
        )

        result = await cfg.behavior_disable("my-behavior")

        # Tool IS in disabled — sole active claimant, resolved to 'bash'.
        assert "tool:tool-bash" in result["disabled"]
        assert result["warnings"] == []

        # unmount WAS called — tool was disabled.
        cfg._coordinator.unmount.assert_called_once()

    @pytest.mark.asyncio
    async def test_behavior_disable_warns_when_tool_name_unresolvable(
        self,
    ) -> None:
        """behavior_disable emits a warning when tool mounted name cannot be resolved.

        When provenance attributes 'tool:tool-skills' to 'superpowers-behavior'
        (sole claimant) but the tool is mounted as 'load_skill' (semantically
        unrelated name that no resolution strategy can derive from 'tool-skills'),
        behavior_disable cannot determine the mounted name and emits a warning
        instead of silently skipping.

        This covers the case where strategy 5 (Python module path introspection)
        is also unavailable because no tool specs are configured in coordinator.config.
        """
        tool_instance = MagicMock(name="load-skill-instance")
        cfg = self._make_behavior_cfg(
            provenance={
                "tool:tool-skills": ["superpowers-behavior"],
            },
            # Mounted as 'load_skill' — semantically unrelated to 'tool-skills'.
            mounted_tools={"load_skill": tool_instance},
        )

        result = await cfg.behavior_disable("superpowers-behavior")

        # Tool must NOT be unmounted — name couldn't be resolved.
        cfg._coordinator.unmount.assert_not_called()

        # Tool must NOT appear in disabled (nothing was actually disabled).
        assert "tool:tool-skills" not in result["disabled"]

        # A warning must be present — user needs to know the tool was skipped.
        assert len(result["warnings"]) >= 1
        warning = result["warnings"][0]
        # Warning mentions the module ID so user can investigate.
        assert "tool-skills" in warning, (
            f"Expected 'tool-skills' in warning, got: {warning!r}"
        )

    @pytest.mark.asyncio
    async def test_behavior_disable_skips_tool_with_other_active_claimant(
        self,
    ) -> None:
        """behavior_disable skips a tool that is also claimed by another active behavior.

        When 'tool:tool-mode' is claimed by both 'superpowers-behavior' and
        'behavior-modes', disabling 'superpowers-behavior' must NOT unmount
        the tool — 'behavior-modes' is still active and still needs it.
        No warning is emitted (silent skip — not an error).
        """
        tool_instance = MagicMock(name="mode-instance")
        cfg = self._make_behavior_cfg(
            provenance={
                "tool:tool-mode": ["superpowers-behavior", "behavior-modes"],
            },
            mounted_tools={"mode": tool_instance},
        )

        result = await cfg.behavior_disable("superpowers-behavior")

        # Tool must NOT be unmounted — still claimed by behavior-modes.
        cfg._coordinator.unmount.assert_not_called()

        # Tool must NOT appear in disabled.
        assert "tool:tool-mode" not in result["disabled"]

        # No warning — this is expected behavior, not an error.
        assert result["warnings"] == []

    @pytest.mark.asyncio
    async def test_behavior_disable_still_removes_context_and_agents(
        self,
    ) -> None:
        """behavior_disable disables context and agents even when tools are skipped.

        Verifies that the tool-skip logic does not interfere with the normal
        context and agent disable paths.
        """
        bundle = Bundle(
            name="mixed-bundle",
            context={"readme": Path("/tmp/readme.md")},
            tools=[],
            hooks=[],
            providers=[],
            agents={"my-agent": {"description": "Test"}},
        )
        bundle._provenance = {  # type: ignore[misc]
            "context:readme": ["my-behavior"],
            "tool:tool-bash": ["my-behavior"],
            "agent:my-agent": ["my-behavior"],
        }

        coordinator = MagicMock()
        coordinator.get_capability.return_value = None
        coordinator.hooks.list_handlers.return_value = {}
        coordinator.mount = AsyncMock()
        coordinator.unmount = AsyncMock(return_value=None)
        coordinator.config = {"agents": {"my-agent": {}}}

        tool_instance = MagicMock()
        coordinator.get = MagicMock(
            side_effect=lambda mp: {"tool-bash": tool_instance} if mp == "tools" else {}
        )

        prepared = MagicMock()
        prepared.bundle = bundle
        session = MagicMock()
        session.coordinator = coordinator

        cfg = SessionConfigurator(session=session, prepared_bundle=prepared)

        result = await cfg.behavior_disable("my-behavior")

        # Context and agent are disabled (stashed).
        assert "context:readme" in result["disabled"]
        assert "agent:my-agent" in result["disabled"]
        assert "readme" not in bundle.context
        assert "my-agent" not in coordinator.config["agents"]

        # Tool IS disabled — sole active claimant, unmount was called.
        assert "tool:tool-bash" in result["disabled"]
        coordinator.unmount.assert_called()
        # No tool warnings — ownership was determinable.
        assert not any("tool-bash" in w for w in result["warnings"])

    @pytest.mark.asyncio
    async def test_behavior_disable_skips_shared_context(self) -> None:
        """behavior_disable skips a context item claimed by another active behavior.

        When 'context:modes:context/modes-instructions.md' is claimed by both
        'superpowers-methodology-behavior' and 'behavior-modes', disabling
        'superpowers-methodology-behavior' must NOT stash the context item —
        'behavior-modes' is still active and still needs it.
        No warning is emitted (silent skip — not an error).
        """
        bundle = Bundle(
            name="test-bundle",
            context={"modes:context/modes-instructions.md": Path("/tmp/modes.md")},
            tools=[],
            hooks=[],
            providers=[],
            agents={},
        )
        bundle._provenance = {  # type: ignore[misc]
            "context:modes:context/modes-instructions.md": [
                "superpowers-methodology-behavior",
                "behavior-modes",
            ],
        }

        coordinator = MagicMock()
        coordinator.get_capability.return_value = None
        coordinator.hooks.list_handlers.return_value = {}
        coordinator.mount = AsyncMock()
        coordinator.unmount = AsyncMock(return_value=None)
        coordinator.config = {"agents": {}}
        coordinator.get = MagicMock(return_value={})

        prepared = MagicMock()
        prepared.bundle = bundle
        session = MagicMock()
        session.coordinator = coordinator

        cfg = SessionConfigurator(session=session, prepared_bundle=prepared)
        result = await cfg.behavior_disable("superpowers-methodology-behavior")

        # Context must NOT be stashed — still claimed by behavior-modes.
        assert "modes:context/modes-instructions.md" in bundle.context
        assert "modes:context/modes-instructions.md" not in cfg._stash["context"]

        # Prov key must NOT appear in disabled.
        assert "context:modes:context/modes-instructions.md" not in result["disabled"]

        # No warning — silent skip is correct behavior.
        assert result["warnings"] == []

    @pytest.mark.asyncio
    async def test_behavior_disable_removes_unshared_context(self) -> None:
        """behavior_disable removes a context item when the behavior is its sole claimant.

        When a context item is claimed only by the behavior being disabled,
        it IS moved to the stash.
        """
        bundle = Bundle(
            name="test-bundle",
            context={"superpowers:context/philosophy.md": Path("/tmp/philosophy.md")},
            tools=[],
            hooks=[],
            providers=[],
            agents={},
        )
        bundle._provenance = {  # type: ignore[misc]
            "context:superpowers:context/philosophy.md": [
                "superpowers-methodology-behavior",
            ],
        }

        coordinator = MagicMock()
        coordinator.get_capability.return_value = None
        coordinator.hooks.list_handlers.return_value = {}
        coordinator.mount = AsyncMock()
        coordinator.unmount = AsyncMock(return_value=None)
        coordinator.config = {"agents": {}}
        coordinator.get = MagicMock(return_value={})

        prepared = MagicMock()
        prepared.bundle = bundle
        session = MagicMock()
        session.coordinator = coordinator

        cfg = SessionConfigurator(session=session, prepared_bundle=prepared)
        result = await cfg.behavior_disable("superpowers-methodology-behavior")

        # Context IS stashed — sole active claimant.
        assert "superpowers:context/philosophy.md" not in bundle.context
        assert "superpowers:context/philosophy.md" in cfg._stash["context"]

        # Prov key appears in disabled.
        assert "context:superpowers:context/philosophy.md" in result["disabled"]
        assert result["warnings"] == []

    @pytest.mark.asyncio
    async def test_behavior_disable_skips_shared_agents(self) -> None:
        """behavior_disable skips an agent claimed by another active behavior.

        When an agent is claimed by both the behavior being disabled and another
        active behavior, the agent is NOT stashed — the other behavior still needs it.
        """
        bundle = Bundle(
            name="test-bundle",
            context={},
            tools=[],
            hooks=[],
            providers=[],
            agents={"shared-agent": {"description": "Shared by two behaviors"}},
        )
        bundle._provenance = {  # type: ignore[misc]
            "agent:shared-agent": ["behavior-a", "behavior-b"],
        }

        coordinator = MagicMock()
        coordinator.get_capability.return_value = None
        coordinator.hooks.list_handlers.return_value = {}
        coordinator.mount = AsyncMock()
        coordinator.unmount = AsyncMock(return_value=None)
        coordinator.config = {"agents": {"shared-agent": {"description": "Shared"}}}
        coordinator.get = MagicMock(return_value={})

        prepared = MagicMock()
        prepared.bundle = bundle
        session = MagicMock()
        session.coordinator = coordinator

        cfg = SessionConfigurator(session=session, prepared_bundle=prepared)
        result = await cfg.behavior_disable("behavior-a")

        # Agent must NOT be stashed — behavior-b still active.
        assert "shared-agent" in coordinator.config["agents"]
        assert "shared-agent" not in cfg._stash["agents"]

        # Prov key must NOT appear in disabled.
        assert "agent:shared-agent" not in result["disabled"]

        # No warning — silent skip is correct.
        assert result["warnings"] == []

    @pytest.mark.asyncio
    async def test_behavior_enable_restores_context_that_was_stashed(self) -> None:
        """behavior_enable restores a context item that was actually stashed.

        When the behavior is the sole claimant and was disabled (stashing the
        context), re-enabling it moves the item back into bundle.context.
        """
        bundle = Bundle(
            name="test-bundle",
            context={},
            tools=[],
            hooks=[],
            providers=[],
            agents={},
        )
        bundle._provenance = {  # type: ignore[misc]
            "context:superpowers:context/philosophy.md": [
                "superpowers-methodology-behavior",
            ],
        }

        coordinator = MagicMock()
        coordinator.get_capability.return_value = None
        coordinator.hooks.list_handlers.return_value = {}
        coordinator.mount = AsyncMock()
        coordinator.unmount = AsyncMock(return_value=None)
        coordinator.config = {
            "agents": {},
            "tools": {},
        }
        coordinator.get = MagicMock(return_value={})

        prepared = MagicMock()
        prepared.bundle = bundle
        session = MagicMock()
        session.coordinator = coordinator

        cfg = SessionConfigurator(session=session, prepared_bundle=prepared)

        # Manually stash the context (simulating a prior behavior_disable).
        cfg._stash["context"]["superpowers:context/philosophy.md"] = Path(
            "/tmp/philosophy.md"
        )
        cfg._disabled_behaviors.add("superpowers-methodology-behavior")

        result = await cfg.behavior_enable("superpowers-methodology-behavior")

        # Context is back in the bundle.
        assert "superpowers:context/philosophy.md" in bundle.context

        # Prov key appears in enabled.
        assert "context:superpowers:context/philosophy.md" in result["enabled"]
        assert result["warnings"] == []

    @pytest.mark.asyncio
    async def test_behavior_enable_noop_for_context_kept_alive_by_other_behavior(
        self,
    ) -> None:
        """behavior_enable does nothing for context that was never stashed.

        When a context item was kept alive by another behavior during
        behavior_disable (shared ownership), it was never stashed.
        behavior_enable must not error — context_enable's idempotent guard
        handles this gracefully.
        """
        bundle = Bundle(
            name="test-bundle",
            # Context is already active (was never stashed).
            context={
                "modes:context/modes-instructions.md": Path("/tmp/modes.md"),
            },
            tools=[],
            hooks=[],
            providers=[],
            agents={},
        )
        bundle._provenance = {  # type: ignore[misc]
            "context:modes:context/modes-instructions.md": [
                "superpowers-methodology-behavior",
                "behavior-modes",
            ],
        }

        coordinator = MagicMock()
        coordinator.get_capability.return_value = None
        coordinator.hooks.list_handlers.return_value = {}
        coordinator.mount = AsyncMock()
        coordinator.unmount = AsyncMock(return_value=None)
        coordinator.config = {"agents": {}, "tools": {}}
        coordinator.get = MagicMock(return_value={})

        prepared = MagicMock()
        prepared.bundle = bundle
        session = MagicMock()
        session.coordinator = coordinator

        cfg = SessionConfigurator(session=session, prepared_bundle=prepared)
        # Simulate superpowers was disabled (but context was kept alive by modes).
        cfg._disabled_behaviors.add("superpowers-methodology-behavior")

        # Must not raise — idempotent path handles already-active context.
        result = await cfg.behavior_enable("superpowers-methodology-behavior")

        # Context remains active in bundle.
        assert "modes:context/modes-instructions.md" in bundle.context
        assert "modes:context/modes-instructions.md" not in cfg._stash["context"]

        # No warnings — this is expected behavior.
        assert result["warnings"] == []


class TestSaveAndApply:
    """Tests for save() and apply_saved_settings() methods."""

    def test_save_writes_settings_yaml(
        self,
        configurator: SessionConfigurator,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """save() writes a valid YAML file with 'configurator' section containing
        disabled items and config_overrides."""
        import yaml

        # Disable a context item and set a config override
        configurator.context_disable("readme")
        configurator.config_set("model.name", "claude-3")

        # Use project scope so we can control where the file is written
        monkeypatch.chdir(tmp_path)

        result = configurator.save(scope="project")

        # Check return value is the path string
        expected_path = tmp_path / ".amplifier" / "settings.yaml"
        assert expected_path.exists()

        # Read the written YAML
        with expected_path.open() as f:
            data = yaml.safe_load(f)

        assert "configurator" in data
        conf = data["configurator"]
        assert "disabled" in conf
        assert "config_overrides" in conf

        # disabled.context should contain the stashed "readme"
        assert conf["disabled"]["context"] == ["readme"]

        # disabled.behaviors should be empty (no behaviors disabled)
        assert conf["disabled"]["behaviors"] == []

        # config_overrides should include our override
        assert conf["config_overrides"] == {"model.name": "claude-3"}

        # result should be a string path
        assert isinstance(result, str)

    def test_save_invalid_scope_raises(self, configurator: SessionConfigurator) -> None:
        """save() raises ValueError with 'Invalid scope' for unknown scope."""
        with pytest.raises(ValueError, match="Invalid scope"):
            configurator.save(scope="invalid")

    @pytest.mark.asyncio
    async def test_apply_saved_settings_works(
        self,
        async_configurator: SessionConfigurator,
        async_coordinator: MagicMock,
    ) -> None:
        """apply_saved_settings disables specified items and applies config overrides.

        Hooks listed in the saved settings are silently skipped — hooks are read-only
        and cannot be disabled via apply_saved_settings.
        """
        settings = {
            "disabled": {
                "behaviors": [],
                "context": ["readme"],
                "tools": [],
                "hooks": ["on_before_tool"],
                "providers": [],
                "agents": [],
            },
            "config_overrides": {"model.name": "claude-3"},
        }

        warnings = await async_configurator.apply_saved_settings(settings)

        # Context "readme" should be disabled (removed from bundle.context)
        assert "readme" not in async_configurator._bundle.context

        # Hook "on_before_tool" is silently skipped — hooks are read-only
        assert "on_before_tool" not in async_configurator._stash["hooks"]

        # Config override should be applied
        assert async_configurator._config_overrides["model.name"] == "claude-3"

        # Should return a list of warnings (empty in this case)
        assert isinstance(warnings, list)
        assert warnings == []

    @pytest.mark.asyncio
    async def test_apply_with_stale_refs_doesnt_raise(
        self,
        async_configurator: SessionConfigurator,
    ) -> None:
        """apply_saved_settings silently skips stale references (items no longer present)."""
        settings = {
            "disabled": {
                "behaviors": ["nonexistent-behavior"],
                "context": ["nonexistent-context"],
                "tools": ["nonexistent-tool"],
                "hooks": ["nonexistent-hook"],
                "providers": ["nonexistent-provider"],
                "agents": ["nonexistent-agent"],
            },
            "config_overrides": {},
        }

        # Should not raise despite all references being stale
        warnings = await async_configurator.apply_saved_settings(settings)

        # Returns a list (stale refs are silently skipped)
        assert isinstance(warnings, list)


class TestListMethods:
    """Tests for *_list() methods that power the /config dashboard."""

    def test_context_list_returns_enabled_and_disabled(
        self, configurator: SessionConfigurator, mock_bundle: Bundle
    ) -> None:
        """context_list returns all context entries with correct enabled/disabled status."""
        # Initially "readme" is enabled
        items = configurator.context_list()
        assert len(items) == 1
        readme = items[0]
        assert readme["name"] == "readme"
        assert readme["enabled"] is True
        assert readme["path"] == str(mock_bundle.context["readme"])

        # After disabling, it appears as disabled
        configurator.context_disable("readme")
        items = configurator.context_list()
        assert len(items) == 1
        assert items[0]["name"] == "readme"
        assert items[0]["enabled"] is False

    def test_context_list_carries_behavior_and_source(
        self, configurator: SessionConfigurator
    ) -> None:
        """context_list includes behavior provenance in both 'behaviors' and 'source' keys."""
        items = configurator.context_list()
        # mock_bundle has _provenance = {"context:readme": ["test-behavior"]}
        assert items[0]["behaviors"] == ["test-behavior"]
        assert items[0]["source"] == ["test-behavior"]

    def test_tools_list_returns_enabled_and_disabled(
        self,
        async_configurator: SessionConfigurator,
        async_coordinator: MagicMock,
    ) -> None:
        """tools_list returns mounted tools as enabled and stashed tools as disabled."""
        # Initially tool-bash is enabled (present in coordinator.get("tools"))
        items = async_configurator.tools_list()
        enabled_names = {i["name"] for i in items if i["enabled"]}
        assert "tool-bash" in enabled_names

        # Simulate disabling: remove from mounted dict and add to stash
        mounted = async_coordinator.get("tools")
        instance = mounted.pop("tool-bash")
        async_configurator._stash["tools"]["tool-bash"] = instance

        items = async_configurator.tools_list()
        enabled = [i for i in items if i["enabled"]]
        disabled = [i for i in items if not i["enabled"]]
        assert len(enabled) == 0
        assert len(disabled) == 1
        assert disabled[0]["name"] == "tool-bash"

    def test_hooks_list_returns_all_as_enabled(
        self, configurator: SessionConfigurator
    ) -> None:
        """hooks_list returns all hooks with enabled=True (hooks are read-only)."""
        items = configurator.hooks_list()
        assert len(items) == 2  # on_before_tool + on_after_tool from fixture

        names = {i["name"] for i in items}
        assert "on_before_tool" in names
        assert "on_after_tool" in names

        # All hooks are always enabled
        for item in items:
            assert item["enabled"] is True
            assert "event" in item
            assert "priority" in item

    def test_hooks_list_event_is_correct(
        self, configurator: SessionConfigurator
    ) -> None:
        """hooks_list items carry the correct event binding from the snapshot."""
        by_name = {i["name"]: i for i in configurator.hooks_list()}
        assert by_name["on_before_tool"]["event"] == "before_tool"
        assert by_name["on_after_tool"]["event"] == "after_tool"

    def test_providers_list_returns_enabled_and_disabled(
        self,
        async_configurator: SessionConfigurator,
        async_coordinator: MagicMock,
    ) -> None:
        """providers_list returns mounted providers as enabled and stashed ones as disabled."""
        items = async_configurator.providers_list()
        enabled_names = {i["name"] for i in items if i["enabled"]}
        assert "provider-anthropic" in enabled_names

        # Simulate disabling
        mounted = async_coordinator.get("providers")
        instance = mounted.pop("provider-anthropic")
        async_configurator._stash["providers"]["provider-anthropic"] = instance

        items = async_configurator.providers_list()
        disabled = [i for i in items if not i["enabled"]]
        assert len(disabled) == 1
        assert disabled[0]["name"] == "provider-anthropic"

    def test_agents_list_returns_enabled_and_disabled(
        self,
        configurator: SessionConfigurator,
        mock_coordinator: MagicMock,
    ) -> None:
        """agents_list returns live agents as enabled and stashed agents as disabled."""
        items = configurator.agents_list()
        enabled_names = {i["name"] for i in items if i["enabled"]}
        assert "my-agent" in enabled_names

        # Disable the agent
        configurator.agent_disable("my-agent")

        items = configurator.agents_list()
        assert len(items) == 1
        assert items[0]["name"] == "my-agent"
        assert items[0]["enabled"] is False

    def test_agents_list_config_included(
        self,
        configurator: SessionConfigurator,
        mock_coordinator: MagicMock,
    ) -> None:
        """agents_list items include the agent config dict."""
        items = configurator.agents_list()
        assert isinstance(items[0]["config"], dict)

    def test_behaviors_list_groups_by_provenance(
        self, behavior_configurator: SessionConfigurator
    ) -> None:
        """behaviors_list groups all provenance entries by behavior name with item name lists."""
        items = behavior_configurator.behaviors_list()
        assert len(items) == 1
        beh = items[0]
        assert beh["name"] == "my-behavior"
        assert beh["enabled"] is True

        # behavior_bundle has context:readme, tool:tool-bash, hook:on_before_tool, agent:my-agent
        contributions = beh["contributions"]
        assert len(contributions["context"]) == 1
        assert len(contributions["tools"]) == 1
        assert len(contributions["hooks"]) == 1
        assert len(contributions["agents"]) == 1

    def test_behaviors_list_disabled_behavior(
        self, behavior_configurator: SessionConfigurator
    ) -> None:
        """behaviors_list shows enabled=False for behaviors in _disabled_behaviors."""
        behavior_configurator._disabled_behaviors.add("my-behavior")

        items = behavior_configurator.behaviors_list()
        assert items[0]["enabled"] is False

    def test_behaviors_list_sorted_by_name(
        self,
        mock_session: MagicMock,
        mock_prepared_bundle: MagicMock,
        mock_bundle: Bundle,
    ) -> None:
        """behaviors_list results are sorted alphabetically by name."""
        mock_bundle._provenance = {  # type: ignore[misc]
            "context:readme": ["zebra"],
            "tool:tool-bash": ["alpha"],
        }
        cfg = SessionConfigurator(
            session=mock_session, prepared_bundle=mock_prepared_bundle
        )
        items = cfg.behaviors_list()
        names = [i["name"] for i in items]
        assert names == sorted(names)

    def test_behaviors_list_empty_when_no_provenance(
        self, configurator: SessionConfigurator, mock_bundle: Bundle
    ) -> None:
        """behaviors_list returns empty list when bundle has no provenance."""
        mock_bundle._provenance = {}  # type: ignore[misc]
        items = configurator.behaviors_list()
        assert items == []

    def test_tools_list_resolves_provenance_by_full_module_id(
        self,
        async_configurator: SessionConfigurator,
        async_coordinator: MagicMock,
        mock_bundle: Bundle,
    ) -> None:
        """tools_list() resolves provenance when coordinator mounts tools under short names.

        The real coordinator mounts tools under short names (e.g. "bash") but provenance
        stores module IDs (e.g. "tool:tool-bash").  tools_list() must try both forms so
        that the behavior source is resolved correctly for all mounted tools.
        """
        # Coordinator mounts under the short name "bash" (no "tool-" prefix).
        async_coordinator.get = MagicMock(
            side_effect=lambda mp: {"bash": MagicMock()} if mp == "tools" else {}
        )
        # Provenance uses the full module ID "tool:tool-bash".
        mock_bundle._provenance = {"tool:tool-bash": ["my-behavior"]}  # type: ignore[misc]

        items = async_configurator.tools_list()

        bash_item = next((i for i in items if i["name"] == "bash"), None)
        assert bash_item is not None, "Expected 'bash' in tools_list() result"
        assert bash_item["behaviors"] == ["my-behavior"], (
            "tools_list() must resolve provenance via 'tool:tool-{name}' fallback"
        )
        assert bash_item["source"] == ["my-behavior"]

    def test_providers_list_fallback_to_mount_plan_when_get_returns_empty(
        self,
        mock_session: MagicMock,
        mock_bundle: Bundle,
        mock_prepared_bundle: MagicMock,
    ) -> None:
        """providers_list() falls back to mount plan when coordinator.get('providers') is empty.

        Some coordinator implementations (e.g. the Rust binding) do not expose providers
        via coordinator.get('providers').  In that case providers_list() derives the list
        from coordinator.config['providers'] mount-plan specs so the dashboard still shows
        the correct provider names, config, and behavior attribution.
        """
        coordinator = MagicMock()
        coordinator.get_capability.return_value = None
        coordinator.hooks.list_handlers.return_value = {}
        # coordinator.get("providers") returns empty — simulates the Rust coordinator
        coordinator.get = MagicMock(return_value={})
        coordinator.config = {
            "providers": [
                {
                    "module": "provider-anthropic",
                    "source": "amplifier://provider-anthropic",
                    "config": {"model": "claude-3", "api_key": "sk-test"},
                }
            ],
            "agents": {},
        }

        # Bundle provenance uses full module ID "provider:provider-anthropic"
        mock_bundle._provenance = {  # type: ignore[misc]
            "provider:provider-anthropic": ["foundation"]
        }

        session = MagicMock()
        session.coordinator = coordinator
        mock_prepared_bundle.bundle = mock_bundle

        cfg = SessionConfigurator(session=session, prepared_bundle=mock_prepared_bundle)
        items = cfg.providers_list()

        # Should produce one entry derived from the mount plan
        assert len(items) == 1
        item = items[0]
        # Name should be the short form (prefix stripped)
        assert item["name"] == "anthropic"
        assert item["enabled"] is True
        # Config should be populated from the mount plan spec
        assert item["config"].get("model") == "claude-3"
        # Provenance should resolve via mount plan module ID fallback
        assert item["behaviors"] == ["foundation"]
        assert item["source"] == ["foundation"]

    def test_list_methods_return_empty_on_fresh_empty_bundle(self) -> None:
        """All list methods return empty lists when bundle has no resources."""
        coordinator = MagicMock()
        coordinator.config = {}
        coordinator.get_capability.return_value = None
        coordinator.hooks.list_handlers.return_value = {}
        coordinator.get = MagicMock(return_value={})

        bundle_mock = MagicMock()
        bundle_mock.context = {}
        bundle_mock.tools = []
        bundle_mock.providers = []
        bundle_mock._provenance = {}

        prepared = MagicMock()
        prepared.bundle = bundle_mock

        session = MagicMock()
        session.coordinator = coordinator

        cfg = SessionConfigurator(session=session, prepared_bundle=prepared)

        assert cfg.context_list() == []
        assert cfg.tools_list() == []
        assert cfg.hooks_list() == []
        assert cfg.providers_list() == []
        assert cfg.agents_list() == []
        assert cfg.behaviors_list() == []


class TestBehaviorsListItemNames:
    """Tests that behaviors_list() contributions values are lists of provenance key strings."""

    def test_behaviors_list_contributions_are_name_lists(
        self, behavior_configurator: SessionConfigurator
    ) -> None:
        """behaviors_list() contributions values are lists of provenance key strings, not int counts.

        behavior_bundle provenance:
            context:readme -> my-behavior
            tool:tool-bash -> my-behavior
            hook:on_before_tool -> my-behavior
            agent:my-agent -> my-behavior

        After the fix, contributions['context'] should be ['context:readme'],
        not the integer 1.
        """
        items = behavior_configurator.behaviors_list()
        assert len(items) == 1
        beh = items[0]
        contributions = beh["contributions"]

        # Values must be lists of provenance key strings, not int counts.
        assert isinstance(contributions["context"], list), (
            f"Expected list for 'context', got {type(contributions['context'])}"
        )
        assert isinstance(contributions["tools"], list), (
            f"Expected list for 'tools', got {type(contributions['tools'])}"
        )
        assert isinstance(contributions["hooks"], list), (
            f"Expected list for 'hooks', got {type(contributions['hooks'])}"
        )
        assert isinstance(contributions["agents"], list), (
            f"Expected list for 'agents', got {type(contributions['agents'])}"
        )

        # Provenance keys appear in the lists.
        assert "context:readme" in contributions["context"]
        assert "agent:my-agent" in contributions["agents"]
        assert "tool:tool-bash" in contributions["tools"]
        assert "hook:on_before_tool" in contributions["hooks"]


class TestProvenanceLookupHelpers:
    """Tests for module-level provenance lookup helper functions."""

    def test_normalize_module_name_lowercase(self) -> None:
        """_normalize_module_name lowercases the input."""
        assert _normalize_module_name("LSP") == "lsp"
        assert _normalize_module_name("BASH") == "bash"

    def test_normalize_module_name_hyphens_to_underscores(self) -> None:
        """_normalize_module_name converts hyphens to underscores."""
        assert _normalize_module_name("python-check") == "python_check"
        assert _normalize_module_name("apply-patch") == "apply_patch"

    def test_normalize_module_name_combined(self) -> None:
        """_normalize_module_name handles both hyphen conversion and lowercasing."""
        assert _normalize_module_name("Python-Check") == "python_check"

    def test_build_normalized_prov_lookup_strips_category_prefix(self) -> None:
        """_build_normalized_prov_lookup maps normalized short names to behaviors."""
        prov = {
            "tool:tool-python-check": ["behavior-python"],
            "tool:tool-bash": ["behavior-bash"],
            "tool:tool-lsp": ["behavior-lsp"],
        }
        result = _build_normalized_prov_lookup("tool", prov)
        assert result["python_check"] == ["behavior-python"]
        assert result["bash"] == ["behavior-bash"]
        assert result["lsp"] == ["behavior-lsp"]

    def test_build_normalized_prov_lookup_ignores_other_categories(self) -> None:
        """_build_normalized_prov_lookup only includes entries for the given category."""
        prov = {
            "tool:tool-bash": ["behavior-bash"],
            "hook:hooks-logging": ["behavior-logging"],
            "context:readme": ["behavior-docs"],
        }
        result = _build_normalized_prov_lookup("tool", prov)
        assert "bash" in result
        assert "logging" not in result  # hook entry excluded

    def test_build_normalized_prov_lookup_no_redundant_prefix(self) -> None:
        """_build_normalized_prov_lookup handles module IDs without the category prefix."""
        prov = {"hook:custom-hook": ["behavior-custom"]}
        result = _build_normalized_prov_lookup("hook", prov)
        # "custom-hook" does not start with "hook-" so the full normalized id is used
        assert result.get("custom_hook") == ["behavior-custom"]

    def test_lookup_prov_behavior_strategy1_exact_match(self) -> None:
        """Strategy 1: exact key '{category}:{name}'."""
        prov = {"tool:bash": ["behavior-bash"]}
        norm_map = _build_normalized_prov_lookup("tool", prov)
        assert _lookup_prov_behavior("bash", "tool", prov, norm_map) == [
            "behavior-bash"
        ]

    def test_lookup_prov_behavior_strategy2_module_prefixed(self) -> None:
        """Strategy 2: '{category}:{category}-{name}' (module ID with category prefix)."""
        prov = {"tool:tool-bash": ["behavior-bash"]}
        norm_map = _build_normalized_prov_lookup("tool", prov)
        assert _lookup_prov_behavior("bash", "tool", prov, norm_map) == [
            "behavior-bash"
        ]

    def test_lookup_prov_behavior_strategy3_normalized_case(self) -> None:
        """Strategy 3: normalized exact match handles case differences (LSP→lsp)."""
        prov = {"tool:tool-lsp": ["behavior-lsp"]}
        norm_map = _build_normalized_prov_lookup("tool", prov)
        assert _lookup_prov_behavior("LSP", "tool", prov, norm_map) == ["behavior-lsp"]

    def test_lookup_prov_behavior_strategy3_normalized_hyphens(self) -> None:
        """Strategy 3: normalized exact match handles hyphen/underscore difference."""
        prov = {"tool:tool-python-check": ["behavior-python"]}
        norm_map = _build_normalized_prov_lookup("tool", prov)
        assert _lookup_prov_behavior("python_check", "tool", prov, norm_map) == [
            "behavior-python"
        ]

    def test_lookup_prov_behavior_strategy3_apply_patch(self) -> None:
        """Strategy 3: 'apply_patch' matches 'tool:tool-apply-patch'."""
        prov = {"tool:tool-apply-patch": ["behavior-patch"]}
        norm_map = _build_normalized_prov_lookup("tool", prov)
        assert _lookup_prov_behavior("apply_patch", "tool", prov, norm_map) == [
            "behavior-patch"
        ]

    def test_lookup_prov_behavior_strategy4_prefix_containment(self) -> None:
        """Strategy 4: module short name is a word-boundary prefix of mounted name."""
        prov = {"tool:tool-web": ["behavior-web"]}
        norm_map = _build_normalized_prov_lookup("tool", prov)
        # "web" is a prefix of "web_search" (underscore boundary)
        assert _lookup_prov_behavior("web_search", "tool", prov, norm_map) == [
            "behavior-web"
        ]
        assert _lookup_prov_behavior("web_fetch", "tool", prov, norm_map) == [
            "behavior-web"
        ]

    def test_lookup_prov_behavior_strategy4_requires_underscore_boundary(self) -> None:
        """Strategy 4 requires word-boundary (underscore) — 'web' does not match 'webfoo'."""
        prov = {"tool:tool-web": ["behavior-web"]}
        norm_map = _build_normalized_prov_lookup("tool", prov)
        # "webfoo" does NOT start with "web_" so strategy 4 must not fire
        assert _lookup_prov_behavior("webfoo", "tool", prov, norm_map) is None

    def test_lookup_prov_behavior_semantic_mismatch_returns_none(self) -> None:
        """Semantically unrelated names (load_skill from tool-skills) return None."""
        prov = {"tool:tool-skills": ["behavior-skills"]}
        norm_map = _build_normalized_prov_lookup("tool", prov)
        # 'skills' is not a prefix of 'load_skill', and exact/normalized match fails
        assert _lookup_prov_behavior("load_skill", "tool", prov, norm_map) is None

    def test_lookup_prov_behavior_filesystem_mismatch_returns_none(self) -> None:
        """Tools from multi-tool modules (filesystem→read_file) return None."""
        prov = {"tool:tool-filesystem": ["behavior-fs"]}
        norm_map = _build_normalized_prov_lookup("tool", prov)
        assert _lookup_prov_behavior("read_file", "tool", prov, norm_map) is None
        assert _lookup_prov_behavior("write_file", "tool", prov, norm_map) is None
        assert _lookup_prov_behavior("edit_file", "tool", prov, norm_map) is None

    def test_lookup_prov_behavior_search_mismatch_returns_none(self) -> None:
        """Tools from multi-tool module (search→grep/glob) return None."""
        prov = {"tool:tool-search": ["behavior-search"]}
        norm_map = _build_normalized_prov_lookup("tool", prov)
        assert _lookup_prov_behavior("grep", "tool", prov, norm_map) is None
        assert _lookup_prov_behavior("glob", "tool", prov, norm_map) is None

    def test_lookup_prov_behavior_no_match_returns_none(self) -> None:
        """Returns None when no strategy matches."""
        prov = {"tool:tool-bash": ["behavior-bash"]}
        norm_map = _build_normalized_prov_lookup("tool", prov)
        assert (
            _lookup_prov_behavior("totally_unrelated", "tool", prov, norm_map) is None
        )


class TestNormalizedProvenanceLookupInListMethods:
    """Integration tests for normalized provenance matching in tools_list() and hooks_list()."""

    def _make_cfg(
        self,
        provenance: dict,
        mounted_tools: dict,
        tool_specs: list | None = None,
    ) -> SessionConfigurator:
        """Helper: create a SessionConfigurator with given provenance and mounted tools."""
        coordinator = MagicMock()
        coordinator.get_capability.return_value = None
        coordinator.hooks.list_handlers.return_value = {}
        coordinator.get = MagicMock(
            side_effect=lambda mp: mounted_tools if mp == "tools" else {}
        )
        coordinator.config = {
            "agents": {},
            "tools": tool_specs or [],
        }

        bundle_mock = MagicMock()
        bundle_mock.context = {}
        bundle_mock.tools = tool_specs or []
        bundle_mock.providers = []
        bundle_mock._provenance = provenance

        prepared = MagicMock()
        prepared.bundle = bundle_mock

        session = MagicMock()
        session.coordinator = coordinator

        return SessionConfigurator(session=session, prepared_bundle=prepared)

    def test_tools_list_resolves_lsp_by_normalization(self) -> None:
        """'LSP' resolves to 'tool:tool-lsp' via case normalization (strategy 3)."""
        cfg = self._make_cfg(
            provenance={"tool:tool-lsp": ["behavior-lsp"]},
            mounted_tools={"LSP": MagicMock()},
        )
        items = cfg.tools_list()
        assert len(items) == 1
        assert items[0]["name"] == "LSP"
        assert items[0]["behaviors"] == ["behavior-lsp"]
        assert items[0]["source"] == ["behavior-lsp"]

    def test_tools_list_resolves_python_check_by_normalization(self) -> None:
        """'python_check' resolves to 'tool:tool-python-check' via normalization (strategy 3)."""
        cfg = self._make_cfg(
            provenance={"tool:tool-python-check": ["behavior-pycheck"]},
            mounted_tools={"python_check": MagicMock()},
        )
        items = cfg.tools_list()
        item = next(i for i in items if i["name"] == "python_check")
        assert item["behaviors"] == ["behavior-pycheck"]

    def test_tools_list_resolves_apply_patch_by_normalization(self) -> None:
        """'apply_patch' resolves to 'tool:tool-apply-patch' via normalization (strategy 3)."""
        cfg = self._make_cfg(
            provenance={"tool:tool-apply-patch": ["behavior-patch"]},
            mounted_tools={"apply_patch": MagicMock()},
        )
        items = cfg.tools_list()
        item = next(i for i in items if i["name"] == "apply_patch")
        assert item["behaviors"] == ["behavior-patch"]

    def test_tools_list_resolves_web_tools_by_prefix_match(self) -> None:
        """'web_search' and 'web_fetch' resolve to 'tool:tool-web' via prefix match (strategy 4)."""
        cfg = self._make_cfg(
            provenance={"tool:tool-web": ["behavior-web"]},
            mounted_tools={"web_search": MagicMock(), "web_fetch": MagicMock()},
        )
        items = cfg.tools_list()
        by_name = {i["name"]: i for i in items}
        assert by_name["web_search"]["behaviors"] == ["behavior-web"]
        assert by_name["web_fetch"]["behaviors"] == ["behavior-web"]

    def test_tools_list_returns_none_for_semantic_mismatch(self) -> None:
        """'load_skill' returns behavior=None for 'tool:tool-skills' (no relationship)."""
        cfg = self._make_cfg(
            provenance={"tool:tool-skills": ["behavior-skills"]},
            mounted_tools={"load_skill": MagicMock()},
        )
        items = cfg.tools_list()
        item = next(i for i in items if i["name"] == "load_skill")
        assert item["behaviors"] is None

    def test_tools_list_returns_none_for_filesystem_mismatch(self) -> None:
        """read_file/write_file/edit_file return behavior=None for 'tool:tool-filesystem'."""
        cfg = self._make_cfg(
            provenance={"tool:tool-filesystem": ["behavior-fs"]},
            mounted_tools={
                "read_file": MagicMock(),
                "write_file": MagicMock(),
                "edit_file": MagicMock(),
            },
        )
        items = cfg.tools_list()
        by_name = {i["name"]: i for i in items}
        assert by_name["read_file"]["behaviors"] is None
        assert by_name["write_file"]["behaviors"] is None
        assert by_name["edit_file"]["behaviors"] is None

    def test_tools_list_config_lookup_by_normalized_name(self) -> None:
        """Config for 'tool-python-check' is found when tool is mounted as 'python_check'."""
        cfg = self._make_cfg(
            provenance={},
            mounted_tools={"python_check": MagicMock()},
            tool_specs=[{"module": "tool-python-check", "config": {"timeout": 30}}],
        )
        items = cfg.tools_list()
        item = next(i for i in items if i["name"] == "python_check")
        assert item["config"].get("timeout") == 30

    def test_hooks_list_resolves_by_normalization(self) -> None:
        """Hook names with case/hyphen differences are resolved via normalization."""
        coordinator = MagicMock()
        coordinator.get_capability.return_value = None
        # Hook registered as "python-check", module ID is "hooks-python-check"
        coordinator.hooks.list_handlers.return_value = {
            "tool:post": ["python-check"],
        }
        coordinator.get = MagicMock(return_value={})
        coordinator.config = {"agents": {}}

        bundle_mock = MagicMock()
        bundle_mock.context = {}
        bundle_mock.tools = []
        bundle_mock.providers = []
        bundle_mock._provenance = {"hook:hooks-python-check": ["behavior-pycheck"]}

        prepared = MagicMock()
        prepared.bundle = bundle_mock
        session = MagicMock()
        session.coordinator = coordinator

        cfg = SessionConfigurator(session=session, prepared_bundle=prepared)
        items = cfg.hooks_list()

        hook = next((i for i in items if i["name"] == "python-check"), None)
        assert hook is not None, "'python-check' hook must appear in hooks_list()"
        assert hook["behaviors"] == ["behavior-pycheck"]

    def test_providers_list_empty_when_app_level_injected(self) -> None:
        """providers_list() returns empty when all providers are app-level injected.

        Providers auto-configured by the app-cli from environment variables are
        not tracked by the configurator.  coordinator.get('providers') returns {}
        and coordinator.config has no provider specs.  The result must be empty.
        """
        coordinator = MagicMock()
        coordinator.get_capability.return_value = None
        coordinator.hooks.list_handlers.return_value = {}
        # Both paths return nothing — simulates the real app session
        coordinator.get = MagicMock(return_value={})
        coordinator.config = {"agents": {}}  # no "providers" key

        bundle_mock = MagicMock()
        bundle_mock.context = {}
        bundle_mock.tools = []
        bundle_mock.providers = []
        bundle_mock._provenance = {}

        prepared = MagicMock()
        prepared.bundle = bundle_mock
        session = MagicMock()
        session.coordinator = coordinator

        cfg = SessionConfigurator(session=session, prepared_bundle=prepared)
        items = cfg.providers_list()
        # App-level providers are not visible here — this is the expected, correct result
        assert items == []


class TestMultiClaimantProvenance:
    """Tests for multi-claimant provenance (dict[str, list[str]]) in configurator."""

    def test_context_list_returns_behavior_list(
        self,
        mock_session: MagicMock,
        mock_prepared_bundle: MagicMock,
        mock_bundle: Bundle,
    ) -> None:
        """context_list() returns behavior as list[str] with multi-claimant provenance."""
        mock_bundle._provenance = {  # type: ignore[misc]
            "context:readme": ["behavior-a", "behavior-b"],
        }
        cfg = SessionConfigurator(
            session=mock_session, prepared_bundle=mock_prepared_bundle
        )
        items = cfg.context_list()
        assert len(items) == 1
        readme = items[0]
        assert readme["behaviors"] == ["behavior-a", "behavior-b"]
        assert readme["source"] == ["behavior-a", "behavior-b"]

    def test_agents_list_returns_behavior_list(
        self,
        mock_session: MagicMock,
        mock_prepared_bundle: MagicMock,
        mock_bundle: Bundle,
    ) -> None:
        """agents_list() returns behavior as list[str] with multi-claimant provenance."""
        mock_bundle._provenance = {  # type: ignore[misc]
            "agent:my-agent": ["behavior-a", "behavior-b"],
        }
        cfg = SessionConfigurator(
            session=mock_session, prepared_bundle=mock_prepared_bundle
        )
        items = cfg.agents_list()
        agent_item = next((i for i in items if i["name"] == "my-agent"), None)
        assert agent_item is not None, "Expected 'my-agent' in agents_list()"
        assert agent_item["behaviors"] == ["behavior-a", "behavior-b"]
        assert agent_item["source"] == ["behavior-a", "behavior-b"]

    def test_behaviors_list_counts_multi_claimant_items(
        self,
        mock_session: MagicMock,
        mock_prepared_bundle: MagicMock,
        mock_bundle: Bundle,
    ) -> None:
        """behaviors_list() correctly counts contributions across multi-claimant items.

        When tool:tool-bash claims ["behavior-a", "behavior-b"] and
        context:readme claims ["behavior-a"], behaviors_list should show:
        - behavior-a: tools=1, context=1
        - behavior-b: tools=1, context=0
        """
        mock_bundle._provenance = {  # type: ignore[misc]
            "tool:tool-bash": ["behavior-a", "behavior-b"],
            "context:readme": ["behavior-a"],
        }
        cfg = SessionConfigurator(
            session=mock_session, prepared_bundle=mock_prepared_bundle
        )
        items = cfg.behaviors_list()
        by_name = {i["name"]: i for i in items}

        assert "behavior-a" in by_name
        assert "behavior-b" in by_name

        assert len(by_name["behavior-a"]["contributions"]["tools"]) == 1
        assert len(by_name["behavior-a"]["contributions"]["context"]) == 1
        assert len(by_name["behavior-b"]["contributions"]["tools"]) == 1
        assert len(by_name["behavior-b"]["contributions"]["context"]) == 0


class TestTopLevelImport:
    """Verify SessionConfigurator is importable from amplifier_foundation top-level."""

    def test_session_configurator_importable_from_top_level(self) -> None:
        """SessionConfigurator must be importable directly from amplifier_foundation."""
        from amplifier_foundation import SessionConfigurator as SC  # noqa: F401

        assert SC is not None

    def test_session_configurator_in_dunder_all(self) -> None:
        """SessionConfigurator must appear in amplifier_foundation.__all__."""
        import amplifier_foundation

        assert "SessionConfigurator" in amplifier_foundation.__all__, (
            "'SessionConfigurator' is missing from amplifier_foundation.__all__"
        )

    def test_top_level_and_submodule_are_same_class(self) -> None:
        """Top-level import and submodule import must resolve to the same class."""
        from amplifier_foundation import SessionConfigurator as TopLevel
        from amplifier_foundation.configurator import SessionConfigurator as SubModule

        assert TopLevel is SubModule


class TestModuleToToolMapping:
    """Tests for _module_to_tools and _tool_to_module mappings in SessionConfigurator."""

    def _make_cfg(
        self,
        tool_specs: list,
        mounted_tools: dict,
    ) -> "SessionConfigurator":
        """Helper: create a SessionConfigurator with given tool specs and mounted tools."""
        coordinator = MagicMock()
        coordinator.get_capability.return_value = None
        coordinator.hooks.list_handlers.return_value = {}
        coordinator.get = MagicMock(
            side_effect=lambda mp: mounted_tools if mp == "tools" else {}
        )
        coordinator.config = {
            "agents": {},
            "tools": tool_specs,
        }

        bundle_mock = MagicMock()
        bundle_mock.context = {}
        bundle_mock.tools = tool_specs
        bundle_mock.providers = []
        bundle_mock._provenance = {}

        prepared = MagicMock()
        prepared.bundle = bundle_mock

        session = MagicMock()
        session.coordinator = coordinator

        return SessionConfigurator(session=session, prepared_bundle=prepared)

    def test_module_to_tools_built_at_init(self) -> None:
        """_module_to_tools and _tool_to_module are populated at init from tool specs.

        Module 'tool-web' should match 'web_search' and 'web_fetch' via prefix containment.
        """
        cfg = self._make_cfg(
            tool_specs=[{"module": "tool-web"}],
            mounted_tools={"web_search": MagicMock(), "web_fetch": MagicMock()},
        )

        # _module_to_tools maps module_id -> list of tool names
        assert hasattr(cfg, "_module_to_tools")
        assert hasattr(cfg, "_tool_to_module")
        assert "tool-web" in cfg._module_to_tools
        assert "web_search" in cfg._module_to_tools["tool-web"]
        assert "web_fetch" in cfg._module_to_tools["tool-web"]

        # _tool_to_module maps tool name -> module_id
        assert cfg._tool_to_module["web_search"] == "tool-web"
        assert cfg._tool_to_module["web_fetch"] == "tool-web"

    def test_tools_list_includes_module_field(self) -> None:
        """tools_list() items include a 'module_id' field with the matched module ID.

        Tool 'bash' matched from spec 'tool-bash' via prefix-strip strategy.
        """
        cfg = self._make_cfg(
            tool_specs=[{"module": "tool-bash"}],
            mounted_tools={"bash": MagicMock()},
        )

        items = cfg.tools_list()
        assert len(items) == 1
        item = items[0]
        assert "module_id" in item, "tools_list() items must have a 'module_id' field"
        assert item["module_id"] == "tool-bash"

    def test_tools_list_module_unknown_when_no_spec(self) -> None:
        """tools_list() 'module_id' field is 'unknown' when no spec matches the tool.

        Tool 'load_skill' from spec 'tool-skills' is a semantic mismatch (no match);
        tool 'orphan_tool' with no spec at all should both return 'unknown'.
        """
        cfg = self._make_cfg(
            tool_specs=[],  # No specs — no match possible
            mounted_tools={"orphan_tool": MagicMock()},
        )

        items = cfg.tools_list()
        assert len(items) == 1
        item = items[0]
        assert "module_id" in item, "tools_list() items must have a 'module_id' field"
        assert item["module_id"] == "unknown"


class TestModuleLevelToolDisable:
    """Tests for tool_disable_module async method."""

    def _make_cfg_with_filesystem(self) -> SessionConfigurator:
        """Helper: configurator with tool-filesystem -> [read_file, write_file].

        tool-filesystem -> read_file/write_file is a semantic rename that cannot
        be auto-detected by _build_module_to_tools, so _module_to_tools is
        populated manually (simulating out-of-band metadata).
        """
        read_file_instance = MagicMock(name="read_file-instance")
        write_file_instance = MagicMock(name="write_file-instance")

        coordinator = MagicMock()
        coordinator.get_capability.return_value = None
        coordinator.hooks.list_handlers.return_value = {}
        coordinator.mount = AsyncMock()
        coordinator.unmount = AsyncMock(return_value=None)
        coordinator.config = {
            "agents": {},
            "tools": [{"module": "tool-filesystem"}],
        }

        _mounts: dict = {
            "tools": {
                "read_file": read_file_instance,
                "write_file": write_file_instance,
            }
        }
        coordinator.get = MagicMock(side_effect=lambda mp: _mounts.get(mp))

        bundle_mock = MagicMock()
        bundle_mock.context = {}
        bundle_mock.tools = [{"module": "tool-filesystem"}]
        bundle_mock.providers = []
        bundle_mock._provenance = {}

        prepared = MagicMock()
        prepared.bundle = bundle_mock

        session = MagicMock()
        session.coordinator = coordinator

        cfg = SessionConfigurator(session=session, prepared_bundle=prepared)

        # Manually populate the module-to-tool mapping since semantic renames
        # (tool-filesystem -> read_file/write_file) cannot be auto-detected.
        cfg._module_to_tools["tool-filesystem"] = ["read_file", "write_file"]
        cfg._tool_to_module["read_file"] = "tool-filesystem"
        cfg._tool_to_module["write_file"] = "tool-filesystem"

        return cfg

    @pytest.mark.asyncio
    async def test_module_disable_unmounts_all_tools(self) -> None:
        """tool_disable_module stashes all tools belonging to the module.

        Calling tool_disable_module('tool-filesystem') must stash both
        read_file and write_file and return them in the result list.
        """
        cfg = self._make_cfg_with_filesystem()

        disabled = await cfg.tool_disable_module("tool-filesystem")

        # Both tools must be stashed
        assert "read_file" in cfg._stash["tools"]
        assert "write_file" in cfg._stash["tools"]

        # Return value must list both disabled tool names
        assert sorted(disabled) == ["read_file", "write_file"]

    @pytest.mark.asyncio
    async def test_module_disable_unknown_module_raises(self) -> None:
        """tool_disable_module raises ValueError with 'not found' for unknown module IDs."""
        cfg = self._make_cfg_with_filesystem()

        with pytest.raises(ValueError, match="not found"):
            await cfg.tool_disable_module("tool-unknown")

    @pytest.mark.asyncio
    async def test_tool_disable_accepts_module_id(self) -> None:
        """tool_disable() with a module ID stashes all tools registered under that module.

        Passing 'tool-filesystem' (a module ID, not a mounted tool name) to
        tool_disable() must unmount and stash both read_file and write_file.
        """
        cfg = self._make_cfg_with_filesystem()

        # Use the public tool_disable interface with a module ID.
        await cfg.tool_disable("tool-filesystem")

        # Both tools must be stashed.
        assert "read_file" in cfg._stash["tools"]
        assert "write_file" in cfg._stash["tools"]

    @pytest.mark.asyncio
    async def test_tool_enable_accepts_module_id(self) -> None:
        """tool_enable() with a module ID re-enables all stashed tools for that module.

        After stashing read_file and write_file, passing 'tool-filesystem' to
        tool_enable() must remount both tools and clear them from the stash.
        """
        cfg = self._make_cfg_with_filesystem()

        # Disable first so there's something in the stash.
        await cfg.tool_disable("tool-filesystem")
        assert "read_file" in cfg._stash["tools"]
        assert "write_file" in cfg._stash["tools"]

        # Re-enable by module ID.
        await cfg.tool_enable("tool-filesystem")

        # Both tools must be cleared from the stash.
        assert "read_file" not in cfg._stash["tools"]
        assert "write_file" not in cfg._stash["tools"]

        # mount() must have been called for both tools.
        coordinator = cfg._coordinator
        mount_calls = [call.args[0] for call in coordinator.mount.call_args_list]
        assert "tools" in mount_calls, (
            "coordinator.mount('tools', ...) must have been called"
        )

    @pytest.mark.asyncio
    async def test_tool_disable_module_id_error_shows_both_tools_and_modules(
        self,
    ) -> None:
        """tool_disable() error for unknown name lists both tool names and module IDs.

        When the name is neither a mounted tool name nor a known module ID, the
        ValueError message must include both the available tool names and the
        available module IDs so the user knows what options they have.
        """
        cfg = self._make_cfg_with_filesystem()

        with pytest.raises(ValueError) as exc_info:
            await cfg.tool_disable("completely-unknown")

        message = str(exc_info.value)
        # Must mention available tools.
        assert (
            "read_file" in message
            or "write_file" in message
            or "Available tools" in message
        )
        # Must mention available modules.
        assert "tool-filesystem" in message or "Available modules" in message


class TestSourceUriInListMethods:
    """Tests that tools_list() and providers_list() include source_uri from mount plan specs."""

    def test_tools_list_includes_source_uri(self) -> None:
        """tools_list() items include source_uri from the spec's 'source' field.

        When coordinator.config['tools'] contains a spec with
        'source': 'git+https://example.com/tool-bash@main',
        tools_list() must return items with source_uri containing 'example.com'.
        """
        coordinator = MagicMock()
        coordinator.get_capability.return_value = None
        coordinator.hooks.list_handlers.return_value = {}
        coordinator.config = {
            "agents": {},
            "tools": [
                {
                    "module": "tool-bash",
                    "source": "git+https://example.com/tool-bash@main",
                }
            ],
        }
        # Tool mounted as short name "bash"
        coordinator.get = MagicMock(
            side_effect=lambda mp: {"bash": MagicMock()} if mp == "tools" else {}
        )

        bundle_mock = MagicMock()
        bundle_mock.context = {}
        bundle_mock.tools = coordinator.config["tools"]
        bundle_mock.providers = []
        bundle_mock._provenance = {}

        prepared = MagicMock()
        prepared.bundle = bundle_mock

        session = MagicMock()
        session.coordinator = coordinator

        cfg = SessionConfigurator(session=session, prepared_bundle=prepared)
        items = cfg.tools_list()

        assert len(items) == 1
        item = items[0]
        assert "source_uri" in item, (
            "tools_list() items must include 'source_uri' field"
        )
        assert item["source_uri"] is not None
        assert "example.com" in item["source_uri"]

    def test_providers_list_includes_source_uri(self) -> None:
        """providers_list() items include source_uri from the spec's 'source' field.

        When coordinator.config['providers'] contains a spec with a 'source' field,
        providers_list() must return items with non-None source_uri.
        """
        coordinator = MagicMock()
        coordinator.get_capability.return_value = None
        coordinator.hooks.list_handlers.return_value = {}
        coordinator.config = {
            "agents": {},
            "providers": [
                {
                    "module": "provider-anthropic",
                    "source": "git+https://example.com/provider-anthropic@main",
                }
            ],
        }
        # Provider mounted as short name "anthropic"
        coordinator.get = MagicMock(
            side_effect=lambda mp: (
                {"anthropic": MagicMock()} if mp == "providers" else {}
            )
        )

        bundle_mock = MagicMock()
        bundle_mock.context = {}
        bundle_mock.tools = []
        bundle_mock.providers = coordinator.config["providers"]
        bundle_mock._provenance = {}

        prepared = MagicMock()
        prepared.bundle = bundle_mock

        session = MagicMock()
        session.coordinator = coordinator

        cfg = SessionConfigurator(session=session, prepared_bundle=prepared)
        items = cfg.providers_list()

        assert len(items) == 1
        item = items[0]
        assert "source_uri" in item, (
            "providers_list() items must include 'source_uri' field"
        )
        assert item["source_uri"] is not None
        assert "example.com" in item["source_uri"]


class TestGetBehaviorRootNamespace:
    """Tests for _get_behavior_root_namespace — namespace detection for behaviors."""

    def _make_configurator_with_sbp(
        self, sbp: dict, provenance: dict | None = None
    ) -> SessionConfigurator:
        """Create a minimal SessionConfigurator with a controlled source_base_paths."""
        coordinator = MagicMock()
        coordinator.config = {"agents": {}}
        coordinator.get_capability.return_value = None
        coordinator.hooks.list_handlers.return_value = {}
        coordinator.get = MagicMock(return_value={})

        bundle_mock = MagicMock()
        bundle_mock.context = {}
        bundle_mock.tools = []
        bundle_mock.providers = []
        bundle_mock._provenance = provenance or {}
        bundle_mock.source_base_paths = sbp

        prepared = MagicMock()
        prepared.bundle = bundle_mock

        session = MagicMock()
        session.coordinator = coordinator

        return SessionConfigurator(session=session, prepared_bundle=prepared)

    def test_returns_sibling_namespace_when_present(self) -> None:
        """Returns the shortest non-behavior sibling namespace (existing behaviour)."""
        sbp = {
            "skills-behavior": "/path/to/skills",
            "skills": "/path/to/skills",
        }
        cfg = self._make_configurator_with_sbp(sbp)
        result = cfg._get_behavior_root_namespace("skills-behavior")
        assert result == "skills"

    def test_returns_behavior_name_when_no_sibling_exists(self) -> None:
        """When no non-behavior sibling exists, returns behavior_name itself.

        This handles behaviors like 'shadow', 'foundation', 'python-dev', and
        'routing-matrix' which ARE the root namespace — their agents are named
        '{behavior_name}:{agent-name}' or '{behavior_name}-{agent-name}'.
        """
        sbp = {
            "shadow": "/path/to/shadow",
            "shadow-behavior": "/path/to/shadow",
        }
        cfg = self._make_configurator_with_sbp(sbp)
        result = cfg._get_behavior_root_namespace("shadow")
        assert result == "shadow", (
            "_get_behavior_root_namespace should return 'shadow' when it has no "
            "non-behavior sibling at the same path (shadow IS its own namespace)"
        )

    def test_returns_behavior_name_for_foundation_style(self) -> None:
        """foundation behavior with no sibling namespace returns 'foundation'."""
        sbp = {
            "foundation": "/path/to/foundation",
            "behavior-foundation": "/path/to/foundation",
        }
        cfg = self._make_configurator_with_sbp(sbp)
        result = cfg._get_behavior_root_namespace("foundation")
        assert result == "foundation"

    def test_returns_behavior_name_for_python_dev_style(self) -> None:
        """python-dev behavior with no sibling namespace returns 'python-dev'."""
        sbp = {
            "python-dev": "/path/to/python-dev",
        }
        cfg = self._make_configurator_with_sbp(sbp)
        result = cfg._get_behavior_root_namespace("python-dev")
        assert result == "python-dev"

    def test_returns_none_when_behavior_not_in_sbp(self) -> None:
        """Returns None when the behavior name is not in source_base_paths."""
        sbp = {
            "other-behavior": "/path/to/other",
        }
        cfg = self._make_configurator_with_sbp(sbp)
        result = cfg._get_behavior_root_namespace("unknown-behavior")
        assert result is None

    def test_returns_none_when_sbp_empty(self) -> None:
        """Returns None when source_base_paths is empty (bundle has no path info)."""
        cfg = self._make_configurator_with_sbp({})
        result = cfg._get_behavior_root_namespace("foundation")
        assert result is None

    def test_prefers_shortest_sibling_over_behavior_name(self) -> None:
        """Shortest non-behavior sibling wins over behavior_name fallback."""
        # superpowers-methodology-behavior has "superpowers" as sibling → that wins
        sbp = {
            "superpowers-methodology-behavior": "/path/to/superpowers",
            "superpowers": "/path/to/superpowers",
            "superpowers-extra": "/path/to/superpowers",
        }
        cfg = self._make_configurator_with_sbp(sbp)
        result = cfg._get_behavior_root_namespace("superpowers-methodology-behavior")
        assert result == "superpowers", (
            "Should return the shortest non-behavior sibling 'superpowers', "
            "not the behavior name 'superpowers-methodology-behavior'"
        )

    def test_behaviors_list_sets_self_root_namespace(self) -> None:
        """behaviors_list() uses self-as-namespace root_namespace for behaviors like 'shadow'."""
        from pathlib import Path

        coordinator = MagicMock()
        coordinator.config = {"agents": {}}
        coordinator.get_capability.return_value = None
        coordinator.hooks.list_handlers.return_value = {}
        coordinator.get = MagicMock(return_value={})

        bundle_mock = MagicMock()
        bundle_mock.context = {}
        bundle_mock.tools = []
        bundle_mock.providers = []
        bundle_mock._provenance = {
            "agent:shadow-operator": ["shadow"],
            "agent:shadow-smoke-test": ["shadow"],
        }
        # shadow is its own namespace: only "shadow" in sbp at this path
        bundle_mock.source_base_paths = {
            "shadow": Path("/path/to/shadow"),
        }

        prepared = MagicMock()
        prepared.bundle = bundle_mock

        session = MagicMock()
        session.coordinator = coordinator

        cfg = SessionConfigurator(session=session, prepared_bundle=prepared)
        items = cfg.behaviors_list()

        shadow_item = next((i for i in items if i["name"] == "shadow"), None)
        assert shadow_item is not None, "shadow should appear in behaviors_list()"
        assert shadow_item["root_namespace"] == "shadow", (
            "shadow behavior's root_namespace should be 'shadow' (self-as-namespace fallback)"
        )


class TestConfigurationChangedEvent:
    """Tests for the generic configuration:changed event emitted after mutations.

    The event is emitted by _emit_change_event() after behavior_disable(),
    behavior_enable(), and apply_saved_settings().  It is fire-and-forget:
    exceptions from hooks.emit() are swallowed and must not surface to callers.
    """

    def _make_cfg_with_emit_mock(
        self,
        provenance: dict | None = None,
    ) -> tuple[SessionConfigurator, AsyncMock]:
        """Build a SessionConfigurator whose coordinator.hooks.emit is an AsyncMock.

        Returns (configurator, emit_mock) so tests can inspect calls.
        The bundle has context:readme, tool:tool-bash, and agent:my-agent all
        attributed to 'my-behavior' by default.
        """
        from pathlib import Path

        tool_instance = MagicMock(name="tool-instance")
        emit_mock = AsyncMock()

        coordinator = MagicMock()
        coordinator.get_capability.return_value = None
        coordinator.hooks.list_handlers.return_value = {
            "before_tool": ["on_before_tool"]
        }
        coordinator.hooks.emit = emit_mock
        coordinator.mount = AsyncMock()
        coordinator.unmount = AsyncMock(return_value=None)
        coordinator.config = {"agents": {"my-agent": {}}}
        coordinator.get = MagicMock(
            side_effect=lambda mp: {"tool-bash": tool_instance} if mp == "tools" else {}
        )

        bundle = Bundle(
            name="test-bundle",
            context={"readme": Path("/tmp/readme.md")},
            tools=[{"module": "tool-bash"}],
            hooks=[],
            providers=[],
            agents={"my-agent": {"description": "Test"}},
        )
        bundle._provenance = provenance or {  # type: ignore[misc]
            "context:readme": ["my-behavior"],
            "tool:tool-bash": ["my-behavior"],
            "agent:my-agent": ["my-behavior"],
        }

        prepared = MagicMock()
        prepared.bundle = bundle

        session = MagicMock()
        session.coordinator = coordinator

        return SessionConfigurator(session=session, prepared_bundle=prepared), emit_mock

    @pytest.mark.asyncio
    async def test_behavior_disable_emits_configuration_changed(self) -> None:
        """behavior_disable emits 'configuration:changed' with action='behavior_disable'."""
        cfg, emit_mock = self._make_cfg_with_emit_mock()

        await cfg.behavior_disable("my-behavior")

        emit_mock.assert_called_once()
        event_name, payload = emit_mock.call_args[0]
        assert event_name == "configuration:changed"
        assert payload["action"] == "behavior_disable"
        assert payload["target"] == "my-behavior"
        assert isinstance(payload["changes"], list)

    @pytest.mark.asyncio
    async def test_behavior_enable_emits_configuration_changed(self) -> None:
        """behavior_enable emits 'configuration:changed' with action='behavior_enable'."""
        cfg, emit_mock = self._make_cfg_with_emit_mock()

        await cfg.behavior_disable("my-behavior")
        emit_mock.reset_mock()

        await cfg.behavior_enable("my-behavior")

        emit_mock.assert_called_once()
        event_name, payload = emit_mock.call_args[0]
        assert event_name == "configuration:changed"
        assert payload["action"] == "behavior_enable"
        assert payload["target"] == "my-behavior"
        assert isinstance(payload["changes"], list)

    @pytest.mark.asyncio
    async def test_emit_change_event_error_is_swallowed(self) -> None:
        """Exceptions from hooks.emit() are swallowed — the mutation still succeeds.

        A broken or missing hook registry must never cause behavior_disable()
        to raise.  The event emission is best-effort (fire-and-forget).
        """
        cfg, emit_mock = self._make_cfg_with_emit_mock()
        emit_mock.side_effect = RuntimeError("hook registry unavailable")

        # Should not raise despite emit() failing
        result = await cfg.behavior_disable("my-behavior")

        # Mutation succeeded regardless
        assert "context:readme" in result["disabled"]
        assert "my-behavior" in cfg._disabled_behaviors

    @pytest.mark.asyncio
    async def test_apply_saved_settings_emits_configuration_changed(self) -> None:
        """apply_saved_settings emits 'configuration:changed' with action='settings_applied'."""
        cfg, emit_mock = self._make_cfg_with_emit_mock()

        settings: dict = {
            "disabled": {
                "behaviors": [],
                "context": [],
                "tools": [],
                "hooks": [],
                "providers": [],
                "agents": [],
            },
            "config_overrides": {},
        }

        await cfg.apply_saved_settings(settings)

        emit_mock.assert_called_once()
        event_name, payload = emit_mock.call_args[0]
        assert event_name == "configuration:changed"
        assert payload["action"] == "settings_applied"
        assert payload["target"] == "saved"
        assert payload["changes"] == []
