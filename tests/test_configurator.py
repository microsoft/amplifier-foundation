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
