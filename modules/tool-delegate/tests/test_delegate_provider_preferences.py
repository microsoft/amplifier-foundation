"""Tests for agent-level provider_preferences flowing through DelegateTool.execute()."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from amplifier_module_tool_delegate import DelegateTool


# =============================================================================
# Helpers
# =============================================================================


def _make_delegate_tool(
    *,
    spawn_fn: AsyncMock | None = None,
    agents: dict | None = None,
) -> tuple[DelegateTool, AsyncMock]:
    """Create a DelegateTool wired for execute() testing.

    Sets up a mocked coordinator with:
    - coordinator.config containing the agents registry
    - session.spawn capability (the mock is returned for inspection)
    - Hooks disabled (returns None)

    Returns:
        Tuple of (DelegateTool, spawn_fn mock) so tests can inspect spawn calls.
    """
    _spawn_fn = spawn_fn or AsyncMock(
        return_value={
            "output": "done",
            "session_id": "child-session-001",
            "status": "success",
        }
    )

    _agents = agents or {}

    coordinator = MagicMock()
    coordinator.session_id = "parent-session-123"

    # coordinator.config must be a real dict – execute() calls .get("agents", {})
    coordinator.config = {"agents": _agents}

    # Capability lookup
    capabilities: dict = {
        "session.spawn": _spawn_fn,
        "session.resume": AsyncMock(return_value={}),
        "self_delegation_depth": 0,
    }

    def get_capability(name: str):
        return capabilities.get(name)

    coordinator.get_capability = get_capability
    coordinator.get = MagicMock(return_value=None)  # hooks = None

    # Parent session mock
    parent_session = MagicMock()
    parent_session.session_id = "parent-session-123"
    parent_session.config = {"session": {"orchestrator": {}}}
    coordinator.session = parent_session

    config: dict = {"features": {}, "settings": {"exclude_tools": []}}
    tool = DelegateTool(coordinator, config)
    return tool, _spawn_fn


# =============================================================================
# Tests
# =============================================================================


class TestAgentProviderPreferences:
    """Tests for agent-level provider_preferences flowing through execute()."""

    @pytest.mark.asyncio
    async def test_agent_defaults_applied_when_caller_omits_prefs(self):
        """When an agent has provider_preferences in its metadata and the
        caller doesn't pass any, the agent's preferences should be used."""

        tool, spawn_fn = _make_delegate_tool(
            agents={
                "budget-agent": {
                    "description": "A budget-friendly agent",
                    "provider_preferences": [
                        {"provider": "anthropic", "model": "claude-haiku-*"},
                        {"provider": "openai", "model": "gpt-5-mini"},
                    ],
                },
            },
        )

        # Call execute() WITHOUT provider_preferences in input
        result = await tool.execute(
            {
                "agent": "budget-agent",
                "instruction": "Do something cheaply",
                # No provider_preferences key
            }
        )

        assert result.success is True

        # Verify spawn_fn was called with the agent's preferences
        spawn_fn.assert_called_once()
        call_kwargs = spawn_fn.call_args.kwargs
        prefs = call_kwargs.get("provider_preferences")

        assert prefs is not None, "Expected agent-level prefs to be forwarded"
        assert len(prefs) == 2
        assert prefs[0].provider == "anthropic"
        assert prefs[0].model == "claude-haiku-*"
        assert prefs[1].provider == "openai"
        assert prefs[1].model == "gpt-5-mini"
