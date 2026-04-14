"""Tests for agent-level provider_preferences defaults in tool-delegate."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from amplifier_module_tool_delegate import DelegateTool


# =============================================================================
# Helpers
# =============================================================================


def _make_delegate_tool(
    *,
    spawn_fn=None,
    agents: dict | None = None,
) -> DelegateTool:
    """Create a DelegateTool with mocked coordinator for execute() testing.

    Unlike the helper in test_delegate_surface_status.py (which tests internal
    methods directly), this helper sets up coordinator.config so that the full
    execute() path works — including the agents registry lookup.
    """
    coordinator = MagicMock()
    coordinator.session_id = "parent-session-123"

    # execute() calls self.coordinator.config.get("agents", {}) at line 733
    # to look up agent metadata. Must be a real dict, not a MagicMock.
    coordinator.config = {"agents": agents or {}}

    # Capability lookup
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
    }

    def get_capability(name):
        return capabilities.get(name)

    coordinator.get_capability = get_capability
    coordinator.get = MagicMock(return_value=None)  # hooks = None

    # Parent session mock
    parent_session = MagicMock()
    parent_session.session_id = "parent-session-123"
    parent_session.config = {"session": {"orchestrator": {}}}
    coordinator.session = parent_session

    config: dict = {"features": {}, "settings": {"exclude_tools": []}}
    return DelegateTool(coordinator, config)


# =============================================================================
# Tests: agent-level provider_preferences
# =============================================================================


class TestAgentProviderPreferences:
    """Tests for agent-level default provider_preferences in execute()."""

    @pytest.mark.asyncio
    async def test_agent_defaults_applied_when_caller_omits_prefs(self):
        """Agent's provider_preferences used when caller doesn't specify any."""
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
                "budget-agent": {
                    "description": "A budget-tier agent",
                    "provider_preferences": [
                        {"provider": "anthropic", "model": "claude-haiku-*"},
                        {"provider": "openai", "model": "gpt-5-mini"},
                    ],
                }
            },
        )

        await tool.execute(
            {
                "agent": "budget-agent",
                "instruction": "Do something simple",
                "context_depth": "none",
            }
        )

        spawn_fn.assert_called_once()
        _, kwargs = spawn_fn.call_args
        prefs = kwargs["provider_preferences"]
        assert prefs is not None, "Expected agent defaults, got None"
        assert len(prefs) == 2
        assert prefs[0].provider == "anthropic"
        assert prefs[0].model == "claude-haiku-*"
        assert prefs[1].provider == "openai"
        assert prefs[1].model == "gpt-5-mini"

    @pytest.mark.asyncio
    async def test_caller_prefs_override_agent_defaults(self):
        """Delegation-time provider_preferences fully replace agent defaults."""
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
                "budget-agent": {
                    "description": "A budget-tier agent",
                    "provider_preferences": [
                        {"provider": "anthropic", "model": "claude-haiku-*"},
                    ],
                }
            },
        )

        await tool.execute(
            {
                "agent": "budget-agent",
                "instruction": "Do something",
                "context_depth": "none",
                "provider_preferences": [
                    {"provider": "openai", "model": "gpt-5.2"},
                ],
            }
        )

        spawn_fn.assert_called_once()
        _, kwargs = spawn_fn.call_args
        prefs = kwargs["provider_preferences"]
        assert len(prefs) == 1, "Caller prefs should fully replace agent defaults"
        assert prefs[0].provider == "openai"
        assert prefs[0].model == "gpt-5.2"

    @pytest.mark.asyncio
    async def test_no_prefs_anywhere_inherits_parent(self):
        """No agent or caller prefs -> provider_preferences is None (inherit parent)."""
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
                "plain-agent": {
                    "description": "Agent with no provider preferences",
                }
            },
        )

        await tool.execute(
            {
                "agent": "plain-agent",
                "instruction": "Do something",
                "context_depth": "none",
            }
        )

        spawn_fn.assert_called_once()
        _, kwargs = spawn_fn.call_args
        assert kwargs["provider_preferences"] is None, (
            "Without agent or caller prefs, should be None to inherit parent model"
        )
