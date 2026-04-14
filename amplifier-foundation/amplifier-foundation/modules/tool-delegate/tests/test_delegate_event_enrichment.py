"""Tests for CP-4: delegate tool event enrichment with tool_call_id/parallel_group_id.

The delegate tool reads tool_call_id and parallel_group_id from the
coordinator's _tool_dispatch_context attribute, which the orchestrator sets
immediately before calling tool.execute() and clears in a finally block.
"""

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
    resume_fn=None,
    agents: dict | None = None,
    hooks=None,
    dispatch_context: dict | None = None,
) -> DelegateTool:
    """Create a DelegateTool wired for execute()-level tests.

    Sets up coordinator.config so the full execute() path works,
    and wires up hooks to capture event emissions.

    Args:
        dispatch_context: If provided, sets coordinator._tool_dispatch_context
            to this value before the tool is called.  Pass {} or omit to test
            the "no dispatch context" path (empty defaults).
    """
    coordinator = MagicMock()
    coordinator.session_id = "parent-session-123"
    coordinator.config = {"agents": agents or {}}
    coordinator.session_state = {}

    # Explicitly set _tool_dispatch_context to avoid MagicMock auto-attributes.
    # Tests that want to simulate orchestrator-provided context pass dispatch_context.
    # Tests that want empty-defaults pass dispatch_context={} or omit it.
    coordinator._tool_dispatch_context = dispatch_context if dispatch_context is not None else {}

    default_spawn_result = {
        "output": "done",
        "session_id": "child-001",
        "status": "success",
        "turn_count": 1,
        "metadata": {},
    }
    default_resume_result = {
        "output": "resumed",
        "session_id": "child-001",
        "status": "success",
        "turn_count": 2,
        "metadata": {},
    }

    capabilities: dict = {
        "session.spawn": spawn_fn or AsyncMock(return_value=default_spawn_result),
        "session.resume": resume_fn or AsyncMock(return_value=default_resume_result),
        "agents.list": lambda: agents or {},
        "agents.get": lambda name: (agents or {}).get(name),
        "self_delegation_depth": 0,
    }

    def get_capability(name):
        return capabilities.get(name)

    coordinator.get_capability = get_capability

    # Return mock hooks when coordinator.get("hooks") is called
    if hooks is not None:
        coordinator.get = MagicMock(return_value=hooks)
    else:
        coordinator.get = MagicMock(return_value=None)

    parent_session = MagicMock()
    parent_session.session_id = "parent-session-123"
    parent_session.config = {"session": {"orchestrator": {}}}
    coordinator.session = parent_session

    config: dict = {"features": {}, "settings": {"exclude_tools": []}}
    return DelegateTool(coordinator, config)


def _make_hooks() -> MagicMock:
    """Create a mock hooks object with async emit."""
    hooks = MagicMock()
    hooks.emit = AsyncMock()
    return hooks


# =============================================================================
# Tests: tool_call_id in spawn events
# =============================================================================


class TestSpawnEventEnrichment:
    """delegate:agent_spawned and delegate:agent_completed events carry tool_call_id."""

    @pytest.mark.asyncio
    async def test_spawned_event_contains_tool_call_id(self):
        """delegate:agent_spawned payload includes tool_call_id from coordinator dispatch context."""
        hooks = _make_hooks()
        tool = _make_delegate_tool(
            hooks=hooks,
            agents={"test-agent": {"description": "A test agent"}},
            dispatch_context={"tool_call_id": "call_abc123"},
        )

        await tool.execute(
            {
                "agent": "test-agent",
                "instruction": "Do something",
                "context_depth": "none",
            }
        )

        # Find the agent_spawned emission
        emitted = {args[0]: args[1] for args, _ in hooks.emit.call_args_list}
        assert "delegate:agent_spawned" in emitted, (
            "Expected delegate:agent_spawned event"
        )
        payload = emitted["delegate:agent_spawned"]
        assert payload["tool_call_id"] == "call_abc123"

    @pytest.mark.asyncio
    async def test_spawned_event_contains_parallel_group_id(self):
        """delegate:agent_spawned payload includes parallel_group_id from coordinator dispatch context."""
        hooks = _make_hooks()
        tool = _make_delegate_tool(
            hooks=hooks,
            agents={"test-agent": {"description": "A test agent"}},
            dispatch_context={"parallel_group_id": "group_xyz"},
        )

        await tool.execute(
            {
                "agent": "test-agent",
                "instruction": "Do something",
                "context_depth": "none",
            }
        )

        emitted = {args[0]: args[1] for args, _ in hooks.emit.call_args_list}
        assert "delegate:agent_spawned" in emitted
        payload = emitted["delegate:agent_spawned"]
        assert payload["parallel_group_id"] == "group_xyz"

    @pytest.mark.asyncio
    async def test_completed_event_contains_tool_call_id(self):
        """delegate:agent_completed payload includes tool_call_id from coordinator dispatch context."""
        hooks = _make_hooks()
        tool = _make_delegate_tool(
            hooks=hooks,
            agents={"test-agent": {"description": "A test agent"}},
            dispatch_context={"tool_call_id": "call_def456"},
        )

        await tool.execute(
            {
                "agent": "test-agent",
                "instruction": "Do something",
                "context_depth": "none",
            }
        )

        emitted = {args[0]: args[1] for args, _ in hooks.emit.call_args_list}
        assert "delegate:agent_completed" in emitted
        payload = emitted["delegate:agent_completed"]
        assert payload["tool_call_id"] == "call_def456"

    @pytest.mark.asyncio
    async def test_completed_event_contains_parallel_group_id(self):
        """delegate:agent_completed payload includes parallel_group_id from coordinator dispatch context."""
        hooks = _make_hooks()
        tool = _make_delegate_tool(
            hooks=hooks,
            agents={"test-agent": {"description": "A test agent"}},
            dispatch_context={"parallel_group_id": "group_parallel"},
        )

        await tool.execute(
            {
                "agent": "test-agent",
                "instruction": "Do something",
                "context_depth": "none",
            }
        )

        emitted = {args[0]: args[1] for args, _ in hooks.emit.call_args_list}
        assert "delegate:agent_completed" in emitted
        payload = emitted["delegate:agent_completed"]
        assert payload["parallel_group_id"] == "group_parallel"

    @pytest.mark.asyncio
    async def test_events_have_empty_defaults_when_dispatch_context_absent(self):
        """Events include tool_call_id and parallel_group_id with empty defaults
        when no _tool_dispatch_context is set (e.g. older orchestrator)."""
        hooks = _make_hooks()
        tool = _make_delegate_tool(
            hooks=hooks,
            agents={"test-agent": {"description": "A test agent"}},
            # dispatch_context omitted → defaults to {} → empty string / None
        )

        await tool.execute(
            {
                "agent": "test-agent",
                "instruction": "Do something",
                "context_depth": "none",
            }
        )

        emitted = {args[0]: args[1] for args, _ in hooks.emit.call_args_list}
        # Both spawned and completed events should have these keys
        assert "delegate:agent_spawned" in emitted
        spawned = emitted["delegate:agent_spawned"]
        assert "tool_call_id" in spawned
        assert "parallel_group_id" in spawned
        # tool_call_id defaults to empty string, parallel_group_id to None
        assert spawned["tool_call_id"] == ""
        assert spawned["parallel_group_id"] is None


# =============================================================================
# Tests: spawn error events enrichment
# =============================================================================


class TestSpawnErrorEventEnrichment:
    """delegate:error events on spawn path carry tool_call_id and parallel_group_id."""

    @pytest.mark.asyncio
    async def test_spawn_error_event_contains_tool_call_id(self):
        """delegate:error (general Exception) payload includes tool_call_id."""
        hooks = _make_hooks()
        spawn_fn = AsyncMock(side_effect=RuntimeError("spawn exploded"))
        tool = _make_delegate_tool(
            hooks=hooks,
            spawn_fn=spawn_fn,
            agents={"test-agent": {"description": "A test agent"}},
            dispatch_context={"tool_call_id": "call_err001"},
        )

        await tool.execute(
            {
                "agent": "test-agent",
                "instruction": "Do something",
                "context_depth": "none",
            }
        )

        emitted = {args[0]: args[1] for args, _ in hooks.emit.call_args_list}
        assert "delegate:error" in emitted
        payload = emitted["delegate:error"]
        assert payload["tool_call_id"] == "call_err001"

    @pytest.mark.asyncio
    async def test_spawn_error_event_contains_parallel_group_id(self):
        """delegate:error (general Exception) payload includes parallel_group_id."""
        hooks = _make_hooks()
        spawn_fn = AsyncMock(side_effect=RuntimeError("spawn exploded"))
        tool = _make_delegate_tool(
            hooks=hooks,
            spawn_fn=spawn_fn,
            agents={"test-agent": {"description": "A test agent"}},
            dispatch_context={"parallel_group_id": "group_err"},
        )

        await tool.execute(
            {
                "agent": "test-agent",
                "instruction": "Do something",
                "context_depth": "none",
            }
        )

        emitted = {args[0]: args[1] for args, _ in hooks.emit.call_args_list}
        assert "delegate:error" in emitted
        payload = emitted["delegate:error"]
        assert payload["parallel_group_id"] == "group_err"


# =============================================================================
# Tests: session_metadata passed to spawn_fn
# =============================================================================


class TestSessionMetadataInSpawn:
    """spawn_fn receives session_metadata kwarg with agent_name, tool_call_id, parallel_group_id."""

    @pytest.mark.asyncio
    async def test_spawn_fn_receives_session_metadata_with_tool_call_id(self):
        """spawn_fn is called with session_metadata containing tool_call_id."""
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
            dispatch_context={"tool_call_id": "call_spawn_meta"},
        )

        await tool.execute(
            {
                "agent": "test-agent",
                "instruction": "Do work",
                "context_depth": "none",
            }
        )

        spawn_fn.assert_called_once()
        _, kwargs = spawn_fn.call_args
        assert "session_metadata" in kwargs, (
            "spawn_fn should receive session_metadata kwarg"
        )
        meta = kwargs["session_metadata"]
        assert meta["tool_call_id"] == "call_spawn_meta"

    @pytest.mark.asyncio
    async def test_spawn_fn_receives_session_metadata_with_agent_name(self):
        """spawn_fn session_metadata always includes agent_name."""
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
            agents={"my-agent": {"description": "An agent"}},
        )

        await tool.execute(
            {
                "agent": "my-agent",
                "instruction": "Do work",
                "context_depth": "none",
            }
        )

        spawn_fn.assert_called_once()
        _, kwargs = spawn_fn.call_args
        assert "session_metadata" in kwargs
        meta = kwargs["session_metadata"]
        assert meta["agent_name"] == "my-agent"

    @pytest.mark.asyncio
    async def test_spawn_fn_receives_session_metadata_with_parallel_group_id(self):
        """spawn_fn session_metadata includes parallel_group_id when provided."""
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
            dispatch_context={"parallel_group_id": "group_meta"},
        )

        await tool.execute(
            {
                "agent": "test-agent",
                "instruction": "Do work",
                "context_depth": "none",
            }
        )

        spawn_fn.assert_called_once()
        _, kwargs = spawn_fn.call_args
        assert "session_metadata" in kwargs
        meta = kwargs["session_metadata"]
        assert meta["parallel_group_id"] == "group_meta"

    @pytest.mark.asyncio
    async def test_spawn_fn_session_metadata_omits_empty_fields(self):
        """spawn_fn session_metadata omits tool_call_id/parallel_group_id when absent."""
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
            # No dispatch_context → tool_call_id="" and parallel_group_id=None
        )

        await tool.execute(
            {
                "agent": "test-agent",
                "instruction": "Do work",
                "context_depth": "none",
            }
        )

        spawn_fn.assert_called_once()
        _, kwargs = spawn_fn.call_args
        assert "session_metadata" in kwargs
        meta = kwargs["session_metadata"]
        assert meta["agent_name"] == "test-agent"
        # Empty/None values should NOT be included
        assert "tool_call_id" not in meta
        assert "parallel_group_id" not in meta


# =============================================================================
# Tests: resume event enrichment
# =============================================================================


class TestResumeEventEnrichment:
    """delegate:agent_resumed and delegate:agent_completed events on resume path."""

    @pytest.mark.asyncio
    async def test_resumed_event_contains_tool_call_id(self):
        """delegate:agent_resumed payload includes tool_call_id from coordinator dispatch context."""
        hooks = _make_hooks()
        resume_fn = AsyncMock(
            return_value={
                "output": "resumed",
                "session_id": "child-001",
                "status": "success",
                "turn_count": 3,
                "metadata": {},
            }
        )
        tool = _make_delegate_tool(
            hooks=hooks,
            resume_fn=resume_fn,
            dispatch_context={"tool_call_id": "call_resume_001"},
        )

        await tool.execute(
            {
                "session_id": "child-001",
                "instruction": "Continue",
            }
        )

        emitted = {args[0]: args[1] for args, _ in hooks.emit.call_args_list}
        assert "delegate:agent_resumed" in emitted
        payload = emitted["delegate:agent_resumed"]
        assert payload["tool_call_id"] == "call_resume_001"

    @pytest.mark.asyncio
    async def test_resumed_event_contains_parallel_group_id(self):
        """delegate:agent_resumed payload includes parallel_group_id from coordinator dispatch context."""
        hooks = _make_hooks()
        resume_fn = AsyncMock(
            return_value={
                "output": "resumed",
                "session_id": "child-001",
                "status": "success",
                "turn_count": 3,
                "metadata": {},
            }
        )
        tool = _make_delegate_tool(
            hooks=hooks,
            resume_fn=resume_fn,
            dispatch_context={"parallel_group_id": "group_resume"},
        )

        await tool.execute(
            {
                "session_id": "child-001",
                "instruction": "Continue",
            }
        )

        emitted = {args[0]: args[1] for args, _ in hooks.emit.call_args_list}
        assert "delegate:agent_resumed" in emitted
        payload = emitted["delegate:agent_resumed"]
        assert payload["parallel_group_id"] == "group_resume"

    @pytest.mark.asyncio
    async def test_resume_completed_event_contains_tool_call_id(self):
        """delegate:agent_completed on resume path includes tool_call_id."""
        hooks = _make_hooks()
        resume_fn = AsyncMock(
            return_value={
                "output": "resumed",
                "session_id": "child-001",
                "status": "success",
                "turn_count": 3,
                "metadata": {},
            }
        )
        tool = _make_delegate_tool(
            hooks=hooks,
            resume_fn=resume_fn,
            dispatch_context={"tool_call_id": "call_resume_complete"},
        )

        await tool.execute(
            {
                "session_id": "child-001",
                "instruction": "Continue",
            }
        )

        emitted = {args[0]: args[1] for args, _ in hooks.emit.call_args_list}
        assert "delegate:agent_completed" in emitted
        payload = emitted["delegate:agent_completed"]
        assert payload["tool_call_id"] == "call_resume_complete"

    @pytest.mark.asyncio
    async def test_resume_events_defaults_when_dispatch_context_absent(self):
        """Resume events include tool_call_id/parallel_group_id with empty defaults
        when no _tool_dispatch_context is set."""
        hooks = _make_hooks()
        resume_fn = AsyncMock(
            return_value={
                "output": "resumed",
                "session_id": "child-001",
                "status": "success",
                "turn_count": 3,
                "metadata": {},
            }
        )
        tool = _make_delegate_tool(
            hooks=hooks,
            resume_fn=resume_fn,
            # dispatch_context omitted → empty defaults
        )

        await tool.execute(
            {
                "session_id": "child-001",
                "instruction": "Continue",
            }
        )

        emitted = {args[0]: args[1] for args, _ in hooks.emit.call_args_list}
        assert "delegate:agent_resumed" in emitted
        resumed_payload = emitted["delegate:agent_resumed"]
        assert "tool_call_id" in resumed_payload
        assert "parallel_group_id" in resumed_payload
        assert resumed_payload["tool_call_id"] == ""
        assert resumed_payload["parallel_group_id"] is None
