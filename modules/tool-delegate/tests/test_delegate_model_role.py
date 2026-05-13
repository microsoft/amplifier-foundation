"""Tests for model_role parameter in tool-delegate."""

from __future__ import annotations

import logging
from unittest.mock import AsyncMock, MagicMock

import pytest

from amplifier_foundation.spawn_utils import ProviderPreference
from amplifier_module_tool_delegate import DelegateTool


# =============================================================================
# Helpers
# =============================================================================


def _make_delegate_tool(
    *,
    spawn_fn=None,
    agents: dict | None = None,
    model_role_resolver=None,
) -> DelegateTool:
    """Create a DelegateTool with mocked coordinator for model_role testing.

    ``model_role_resolver`` is the duck-typed capability the consumer code
    looks up via ``coordinator.get_capability("model_role_resolver")``.
    Pass ``None`` (default) to simulate "no routing bundle installed" —
    consumer should log a warning and pass ``provider_preferences=None`` to
    spawn_fn. Pass a mock with ``async def resolve(role)`` to simulate an
    active routing strategy.
    """
    coordinator = MagicMock()
    coordinator.session_id = "parent-session-123"

    coordinator.config = {"agents": agents or {}}

    # session_state remains a real dict for any non-routing test concerns.
    coordinator.session_state = {}

    capabilities: dict = {
        "session.spawn": spawn_fn
        or AsyncMock(
            return_value={
                "output": "done",
                "session_id": "child-001",
                "status": "success",
                "turn_count": 1,
                "metadata": {},
            }
        ),
        "session.resume": AsyncMock(return_value={}),
        "agents.list": lambda: agents or {},
        "agents.get": lambda name: (agents or {}).get(name),
        "self_delegation_depth": 0,
        # Capability under test — None means "no routing bundle installed".
        "model_role_resolver": model_role_resolver,
    }

    def get_capability(name):
        return capabilities.get(name)

    coordinator.get_capability = get_capability
    coordinator.get = MagicMock(return_value=None)  # hooks = None

    parent_session = MagicMock()
    parent_session.session_id = "parent-session-123"
    parent_session.config = {"session": {"orchestrator": {}}}
    coordinator.session = parent_session

    config: dict = {"features": {}, "settings": {"exclude_tools": []}}
    return DelegateTool(coordinator, config)


def _make_resolver(
    *,
    return_value: list | None = None,
    name: str = "test-matrix",
):
    """Build a duck-typed model_role_resolver mock.

    The new capability contract is:
        async def resolve(model_role: str | list[str]) -> list[ProviderPreference]
    Tests pass the mock via ``_make_delegate_tool(model_role_resolver=resolver)``.
    """
    resolver = MagicMock()
    resolver.name = name
    resolver.resolve = AsyncMock(return_value=return_value if return_value is not None else [])
    return resolver


# =============================================================================
# Tests: model_role parameter
# =============================================================================


class TestDelegateModelRole:
    """Tests for model_role parameter in delegate tool execute()."""

    @pytest.mark.asyncio
    async def test_model_role_resolves_against_matrix(self):
        """model_role resolves against routing matrix and produces provider_preferences."""
        spawn_fn = AsyncMock(
            return_value={
                "output": "done",
                "session_id": "child-001",
                "status": "success",
                "turn_count": 1,
                "metadata": {},
            }
        )

        # Mock the resolver to return a resolved preference
        resolver = _make_resolver(
            return_value=[
                ProviderPreference(provider="anthropic", model="claude-haiku-3.5", config={}),
            ]
        )
        tool = _make_delegate_tool(
            spawn_fn=spawn_fn,
            agents={
                "test-agent": {
                    "description": "A test agent",
                }
            },
            model_role_resolver=resolver,
        )
        # Wire up providers on coordinator.get("providers")
        tool.coordinator.get = MagicMock(
            side_effect=lambda key: (
                {"provider-anthropic": MagicMock()} if key == "providers" else None
            )
        )

        await tool.execute(
            {
                "agent": "test-agent",
                "instruction": "Do something fast",
                "context_depth": "none",
                "model_role": "fast",
            }
        )

        spawn_fn.assert_called_once()
        _, kwargs = spawn_fn.call_args
        prefs = kwargs["provider_preferences"]
        assert prefs is not None, (
            "Expected resolved provider_preferences from model_role"
        )
        assert len(prefs) >= 1
        assert prefs[0].provider == "anthropic"
        assert prefs[0].model == "claude-haiku-3.5"

        # Verify resolver was called with correct args
        resolver.resolve.assert_called_once()
        call_args = resolver.resolve.call_args
        assert call_args[0][0] == "fast"  # raw_model_role
    @pytest.mark.asyncio
    async def test_provider_preferences_overrides_model_role(self):
        """Explicit provider_preferences wins over model_role when both provided."""
        spawn_fn = AsyncMock(
            return_value={
                "output": "done",
                "session_id": "child-001",
                "status": "success",
                "turn_count": 1,
                "metadata": {},
            }
        )

        # Even with resolver available, it should NOT be called
        resolver = _make_resolver(return_value=[])
        tool = _make_delegate_tool(
            spawn_fn=spawn_fn,
            agents={
                "test-agent": {"description": "A test agent"},
            },
            model_role_resolver=resolver,
        )

        await tool.execute(
            {
                "agent": "test-agent",
                "instruction": "Do something",
                "context_depth": "none",
                "model_role": "fast",
                "provider_preferences": [
                    {"provider": "openai", "model": "gpt-5.2"},
                ],
            }
        )

        spawn_fn.assert_called_once()
        _, kwargs = spawn_fn.call_args
        prefs = kwargs["provider_preferences"]
        assert len(prefs) == 1, "Explicit provider_preferences should win"
        assert prefs[0].provider == "openai"
        assert prefs[0].model == "gpt-5.2"

        # Resolver should NOT have been called
        resolver.resolve.assert_not_called()
    @pytest.mark.asyncio
    async def test_model_role_resolution_includes_config(self):
        """Config from resolved model_role is preserved in ProviderPreference."""
        spawn_fn = AsyncMock(
            return_value={
                "output": "done",
                "session_id": "child-001",
                "status": "success",
                "turn_count": 1,
                "metadata": {},
            }
        )

        # Resolver returns a result with non-empty config
        resolver = _make_resolver(
            return_value=[
                ProviderPreference(provider="anthropic",
                    model="claude-sonnet-4-6",
                    config={"reasoning_effort": "high"}),
            ]
        )
        tool = _make_delegate_tool(
            spawn_fn=spawn_fn,
            agents={
                "coding-agent": {"description": "A coding agent"},
            },
            model_role_resolver=resolver,
        )
        tool.coordinator.get = MagicMock(
            side_effect=lambda key: (
                {"provider-anthropic": MagicMock()} if key == "providers" else None
            )
        )

        await tool.execute(
            {
                "agent": "coding-agent",
                "instruction": "Write a function",
                "context_depth": "none",
                "model_role": "coding",
            }
        )

        spawn_fn.assert_called_once()
        _, kwargs = spawn_fn.call_args
        prefs = kwargs["provider_preferences"]
        assert prefs is not None, (
            "Expected resolved provider_preferences from model_role"
        )
        assert len(prefs) == 1
        assert prefs[0].provider == "anthropic"
        assert prefs[0].model == "claude-sonnet-4-6"
        assert prefs[0].config == {"reasoning_effort": "high"}, (
            f"Expected config={{'reasoning_effort': 'high'}}, got {prefs[0].config!r}"
        )
    @pytest.mark.asyncio
    async def test_model_role_without_matrix_falls_through(self):
        """model_role with no routing matrix falls through gracefully."""
        spawn_fn = AsyncMock(
            return_value={
                "output": "done",
                "session_id": "child-001",
                "status": "success",
                "turn_count": 1,
                "metadata": {},
            }
        )

        tool = _make_delegate_tool(
            spawn_fn=spawn_fn,
            agents={
                "test-agent": {"description": "A test agent"},
            },
            model_role_resolver=None,  # Simulate "no routing bundle installed"
        )

        result = await tool.execute(
            {
                "agent": "test-agent",
                "instruction": "Do something",
                "context_depth": "none",
                "model_role": "fast",
            }
        )

        # Should succeed (not error out) — just falls through
        assert result.success is True
        spawn_fn.assert_called_once()
        _, kwargs = spawn_fn.call_args
        # No matrix means no resolution — provider_preferences stays None
        assert kwargs["provider_preferences"] is None


# =============================================================================
# Tests: Fix 1 — observability warnings for model_role failures
# =============================================================================


class TestModelRoleObservabilityWarnings:
    """Tests that silent model_role failures now emit WARNING-level log messages."""

    @pytest.mark.asyncio
    async def test_model_role_empty_resolution_logs_warning(self, caplog):
        """resolve_model_role returning [] logs a WARNING with diagnostic context."""
        spawn_fn = AsyncMock(
            return_value={
                "output": "done",
                "session_id": "child-001",
                "status": "success",
                "turn_count": 1,
                "metadata": {},
            }
        )

        # Resolver returns empty list — role exists in matrix but no provider matched
        resolver = _make_resolver(return_value=[])
        tool = _make_delegate_tool(
            spawn_fn=spawn_fn,
            agents={"test-agent": {"description": "A test agent"}},
            model_role_resolver=resolver,
        )
        tool.coordinator.get = MagicMock(
            side_effect=lambda key: (
                {"provider-openai": MagicMock()} if key == "providers" else None
            )
        )

        with caplog.at_level(
            logging.WARNING, logger="amplifier_module_tool_delegate"
        ):
            result = await tool.execute(
                {
                    "agent": "test-agent",
                    "instruction": "Write some code",
                    "context_depth": "none",
                    "model_role": "coding",
                }
            )

        # Should still succeed — fall-through is graceful
        assert result.success is True

        # Resolver was called but returned empty
        resolver.resolve.assert_called_once()

        # spawn received provider_preferences=None (resolution failed)
        spawn_fn.assert_called_once()
        _, kwargs = spawn_fn.call_args
        assert kwargs["provider_preferences"] is None, (
            "Empty resolution should leave provider_preferences as None"
        )

        # Warning was emitted — must mention the role, available roles, and providers
        warning_messages = [
            r.message for r in caplog.records if r.levelno == logging.WARNING
        ]
        assert any("coding" in str(m) for m in warning_messages), (
            f"Expected WARNING mentioning 'coding' role; got: {warning_messages}"
        )
    @pytest.mark.asyncio
    async def test_model_role_without_matrix_logs_warning(self, caplog):
        """model_role with no model_role_resolver capability logs WARNING (not just DEBUG)."""
        spawn_fn = AsyncMock(
            return_value={
                "output": "done",
                "session_id": "child-001",
                "status": "success",
                "turn_count": 1,
                "metadata": {},
            }
        )

        tool = _make_delegate_tool(
            spawn_fn=spawn_fn,
            agents={"test-agent": {"description": "A test agent"}},
            model_role_resolver=None,  # Simulate "no routing bundle installed"
        )

        with caplog.at_level(logging.WARNING, logger="amplifier_module_tool_delegate"):
            result = await tool.execute(
                {
                    "agent": "test-agent",
                    "instruction": "Do something",
                    "context_depth": "none",
                    "model_role": "fast",
                }
            )

        assert result.success is True

        # At least one WARNING mentioning the role
        warning_messages = [
            r.message for r in caplog.records if r.levelno == logging.WARNING
        ]
        assert any("fast" in str(m) for m in warning_messages), (
            f"Expected WARNING mentioning 'fast' role; got: {warning_messages}"
        )

        # No DEBUG-only records for the missing-matrix case (it should be WARNING)
        debug_only = [
            r.message
            for r in caplog.records
            if r.levelno == logging.DEBUG and "fast" in str(r.message)
        ]
        assert not debug_only, (
            f"Expected WARNING (not DEBUG) for missing model_role_resolver; "
            f"found DEBUG: {debug_only}"
        )


# =============================================================================
# Tests: Fix 3 — provider_routing in ToolResult output
# =============================================================================


class TestProviderRoutingInResult:
    """Tests that ToolResult output includes provider_routing when routing was requested."""

    @pytest.mark.asyncio
    async def test_provider_routing_in_result_on_success(self):
        """Successful model_role resolution returns provider_routing in output."""
        spawn_fn = AsyncMock(
            return_value={
                "output": "done",
                "session_id": "child-001",
                "status": "success",
                "turn_count": 1,
                "metadata": {},
            }
        )

        resolver = _make_resolver(
            return_value=[
                ProviderPreference(provider="anthropic", model="claude-sonnet-4-6", config={}),
            ]
        )
        tool = _make_delegate_tool(
            spawn_fn=spawn_fn,
            agents={"test-agent": {"description": "A test agent"}},
            model_role_resolver=resolver,
        )
        tool.coordinator.get = MagicMock(
            side_effect=lambda key: (
                {"provider-anthropic": MagicMock()} if key == "providers" else None
            )
        )

        result = await tool.execute(
            {
                "agent": "test-agent",
                "instruction": "Write code",
                "context_depth": "none",
                "model_role": "coding",
            }
        )

        assert result.success is True
        assert result.output is not None
        assert "provider_routing" in result.output, (
            f"Expected 'provider_routing' key in output; got keys: {list(result.output.keys())}"
        )

        routing = result.output["provider_routing"]
        assert routing["model_role"] == "coding"
        assert routing["resolved"] is not None, (
            "Successful resolution should produce non-null 'resolved'"
        )
        assert len(routing["resolved"]) >= 1
        assert routing["resolved"][0]["provider"] == "anthropic"
    @pytest.mark.asyncio
    async def test_provider_routing_in_result_on_failure(self):
        """Failed model_role resolution returns provider_routing with resolved=None."""
        spawn_fn = AsyncMock(
            return_value={
                "output": "done",
                "session_id": "child-001",
                "status": "success",
                "turn_count": 1,
                "metadata": {},
            }
        )

        # Resolver returns empty — no candidates matched installed providers
        resolver = _make_resolver(return_value=[])
        tool = _make_delegate_tool(
            spawn_fn=spawn_fn,
            agents={"test-agent": {"description": "A test agent"}},
            model_role_resolver=resolver,
        )
        tool.coordinator.get = MagicMock(
            side_effect=lambda key: (
                {"provider-openai": MagicMock()} if key == "providers" else None
            )
        )

        result = await tool.execute(
            {
                "agent": "test-agent",
                "instruction": "Write code",
                "context_depth": "none",
                "model_role": "coding",
            }
        )

        assert result.success is True
        assert result.output is not None
        assert "provider_routing" in result.output, (
            "provider_routing should be present even on resolution failure"
        )

        routing = result.output["provider_routing"]
        assert routing["model_role"] == "coding"
        assert routing["resolved"] is None, (
            "Empty resolution should produce resolved=None"
        )

# =============================================================================
# Tests: Fix 2 — delegate:agent_spawned event includes routing intent
# =============================================================================


class TestAgentSpawnedEventIncludesRouting:
    """Tests that delegate:agent_spawned event payload includes routing fields."""

    @pytest.mark.asyncio
    async def test_agent_spawned_event_includes_routing(self):
        """delegate:agent_spawned event includes model_role and provider_preferences."""
        spawn_fn = AsyncMock(
            return_value={
                "output": "done",
                "session_id": "child-001",
                "status": "success",
                "turn_count": 1,
                "metadata": {},
            }
        )

        resolver = _make_resolver(
            return_value=[
                ProviderPreference(provider="anthropic", model="claude-haiku-3.5", config={}),
            ]
        )
        tool = _make_delegate_tool(
            spawn_fn=spawn_fn,
            agents={"test-agent": {"description": "A test agent"}},
            model_role_resolver=resolver,
        )
        tool.coordinator.get = MagicMock(
            side_effect=lambda key: (
                {"provider-anthropic": MagicMock()} if key == "providers" else None
            )
        )

        # Set up a mock hooks object so events are emitted
        mock_hooks = MagicMock()
        mock_hooks.emit = AsyncMock()
        tool.coordinator.get = MagicMock(
            side_effect=lambda key: (
                mock_hooks
                if key == "hooks"
                else (
                    {"provider-anthropic": MagicMock()}
                    if key == "providers"
                    else None
                )
            )
        )

        await tool.execute(
            {
                "agent": "test-agent",
                "instruction": "Do something fast",
                "context_depth": "none",
                "model_role": "fast",
            }
        )

        # Find the agent_spawned event call
        spawned_calls = [
            call
            for call in mock_hooks.emit.call_args_list
            if call.args[0] == "delegate:agent_spawned"
        ]
        assert len(spawned_calls) == 1, (
            f"Expected exactly one delegate:agent_spawned event; got {len(spawned_calls)}"
        )

        payload = spawned_calls[0].args[1]

        # model_role must be present
        assert "model_role" in payload, (
            f"Expected 'model_role' in event payload; got keys: {list(payload.keys())}"
        )
        assert payload["model_role"] == "fast"

        # provider_preferences must be present — non-null because resolution succeeded
        assert "provider_preferences" in payload, (
            f"Expected 'provider_preferences' in event payload; got keys: {list(payload.keys())}"
        )
        assert payload["provider_preferences"] is not None, (
            "provider_preferences should be non-null after successful resolution"
        )
        assert len(payload["provider_preferences"]) >= 1
        assert payload["provider_preferences"][0]["provider"] == "anthropic"