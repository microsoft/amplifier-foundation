"""Tests for tool-delegate surfacing status and metadata from spawn results."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from amplifier_core import ToolResult

from amplifier_module_tool_delegate import DelegateTool


# =============================================================================
# Helpers
# =============================================================================


def _make_delegate_tool(
    *,
    spawn_fn=None,
    resume_fn=None,
    agents: dict | None = None,
) -> DelegateTool:
    """Create a DelegateTool with mocked coordinator capabilities."""
    coordinator = MagicMock()
    coordinator.session_id = "parent-session-123"

    # Build capability lookup
    capabilities: dict = {
        "session.spawn": spawn_fn or AsyncMock(return_value={}),
        "session.resume": resume_fn or AsyncMock(return_value={}),
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
    parent_session.config = {"session": {"orchestrator": "loop-basic"}}
    coordinator.session = parent_session

    config: dict = {"features": {}, "settings": {"exclude_tools": []}}
    return DelegateTool(coordinator, config)


# =============================================================================
# Tests: spawn path
# =============================================================================


@pytest.mark.asyncio
async def test_spawn_surfaces_status_and_metadata():
    """ToolResult output should include status and metadata from spawn result."""
    spawn_result = {
        "output": "Task completed",
        "session_id": "child-session-001",
        "status": "success",
        "turn_count": 3,
        "metadata": {"orchestrator": "loop-basic"},
    }

    tool = _make_delegate_tool(
        spawn_fn=AsyncMock(return_value=spawn_result),
        agents={"test-agent": {"description": "A test agent"}},
    )

    result: ToolResult = await tool._spawn_new_session(
        agent_name="test-agent",
        instruction="Do something",
        context_depth="none",
        context_scope="conversation",
        context_turns=5,
        provider_preferences=None,
        hooks=None,
    )

    assert result.success is True
    assert result.output["response"] == "Task completed"
    assert result.output["session_id"] == "child-session-001"
    assert result.output["turn_count"] == 3
    assert result.output["status"] == "success"
    assert result.output["metadata"] == {"orchestrator": "loop-basic"}


@pytest.mark.asyncio
async def test_spawn_defaults_status_when_missing():
    """ToolResult should have default status/metadata when spawn result lacks them."""
    spawn_result = {
        "output": "Done",
        "session_id": "child-session-002",
        # No status, no metadata — old-style spawn result
    }

    tool = _make_delegate_tool(
        spawn_fn=AsyncMock(return_value=spawn_result),
        agents={"test-agent": {"description": "A test agent"}},
    )

    result: ToolResult = await tool._spawn_new_session(
        agent_name="test-agent",
        instruction="Do something",
        context_depth="none",
        context_scope="conversation",
        context_turns=5,
        provider_preferences=None,
        hooks=None,
    )

    assert result.success is True
    assert result.output["status"] == "success"
    assert result.output["metadata"] == {}


# =============================================================================
# Tests: resume path
# =============================================================================


@pytest.mark.asyncio
async def test_resume_surfaces_status_and_metadata():
    """ToolResult output should include status and metadata from resume result."""
    resume_result = {
        "output": "Resumed response",
        "session_id": "child-session-001_test-agent",
        "status": "incomplete",
        "turn_count": 7,
        "metadata": {"reason": "max_turns"},
    }

    tool = _make_delegate_tool(
        resume_fn=AsyncMock(return_value=resume_result),
    )

    result: ToolResult = await tool._resume_existing_session(
        session_id="child-session-001_test-agent",
        instruction="Continue",
        hooks=None,
    )

    assert result.success is True
    assert result.output["response"] == "Resumed response"
    assert result.output["session_id"] == "child-session-001_test-agent"
    assert result.output["turn_count"] == 7
    assert result.output["status"] == "incomplete"
    assert result.output["metadata"] == {"reason": "max_turns"}


@pytest.mark.asyncio
async def test_resume_defaults_status_when_missing():
    """ToolResult should have default status/metadata when resume result lacks them."""
    resume_result = {
        "output": "Resumed",
        "session_id": "child-session-003_agent",
        # No status, no metadata
    }

    tool = _make_delegate_tool(
        resume_fn=AsyncMock(return_value=resume_result),
    )

    result: ToolResult = await tool._resume_existing_session(
        session_id="child-session-003_agent",
        instruction="Continue",
        hooks=None,
    )

    assert result.success is True
    assert result.output["status"] == "success"
    assert result.output["metadata"] == {}
