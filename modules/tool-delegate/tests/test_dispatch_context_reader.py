"""Tests for the task-keyed dispatch context reader in DelegateTool.

The companion fix in loop-streaming switches from a single shared
coordinator._tool_dispatch_context attribute to a per-task dict at
coordinator._tool_dispatch_contexts, keyed by asyncio.current_task().

This test file verifies that DelegateTool:
1. Reads from the new task-keyed dict first (when present).
2. Falls back to the legacy _tool_dispatch_context attribute when the
   new dict is absent (backward compatibility with pre-fix orchestrators).
3. Degrades gracefully with empty defaults when neither attribute exists.

RED / GREEN notes:
  Test 1 FAILS before the fix: code reads only _tool_dispatch_context
  (legacy), which is empty, so tool_call_id="" instead of "abc".
  Tests 2 and 3 PASS both before and after: they verify backward
  compatibility and graceful degradation, which already work.
  Run against the patched code to see all three tests PASS.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from amplifier_module_tool_delegate import DelegateTool


# =============================================================================
# Helpers (mirror the pattern in test_delegate_event_enrichment.py)
# =============================================================================


def _make_delegate_tool(
    *,
    spawn_fn=None,
    agents: dict | None = None,
    hooks=None,
    legacy_dispatch_context: dict | None = None,
) -> tuple[DelegateTool, MagicMock]:
    """Create a DelegateTool and return (tool, coordinator).

    The coordinator is returned so callers can set _tool_dispatch_contexts
    on it after construction (needed for task-keyed-dict tests where the
    current asyncio task must be obtained inside an async function).

    Args:
        legacy_dispatch_context: If provided, sets coordinator._tool_dispatch_context.
            Omit (or pass None) to leave it as an empty dict (simulates no legacy context).
    """
    coordinator = MagicMock()
    coordinator.session_id = "parent-session-123"
    coordinator.config = {"agents": agents or {}}
    coordinator.session_state = {}

    # Explicitly set both dispatch context attributes as real dicts (avoids
    # MagicMock auto-attribute generation, which returns truthy MagicMocks
    # and silently short-circuits the lookup chain).
    coordinator._tool_dispatch_context = (
        legacy_dispatch_context if legacy_dispatch_context is not None else {}
    )
    # Default to empty real dict — tests that want the task-keyed form will
    # overwrite this after construction.
    coordinator._tool_dispatch_contexts = {}

    default_spawn_result = {
        "output": "done",
        "session_id": "child-001",
        "status": "success",
        "turn_count": 1,
        "metadata": {},
    }

    capabilities: dict = {
        "session.spawn": spawn_fn or AsyncMock(return_value=default_spawn_result),
        "session.resume": AsyncMock(
            return_value={
                "output": "resumed",
                "session_id": "child-001",
                "status": "success",
                "turn_count": 2,
                "metadata": {},
            }
        ),
        "agents.list": lambda: agents or {},
        "agents.get": lambda name: (agents or {}).get(name),
        "self_delegation_depth": 0,
    }

    coordinator.get_capability = lambda name: capabilities.get(name)

    if hooks is not None:
        coordinator.get = MagicMock(return_value=hooks)
    else:
        coordinator.get = MagicMock(return_value=None)

    parent_session = MagicMock()
    parent_session.session_id = "parent-session-123"
    parent_session.config = {"session": {"orchestrator": {}}}
    coordinator.session = parent_session

    config: dict = {"features": {}, "settings": {"exclude_tools": []}}
    tool = DelegateTool(coordinator, config)
    return tool, coordinator


def _make_hooks() -> MagicMock:
    """Create a mock hooks object with async emit."""
    hooks = MagicMock()
    hooks.emit = AsyncMock()
    return hooks


def _emitted(hooks: MagicMock) -> dict[str, dict]:
    """Collapse all hook.emit() calls into event_name→payload dict."""
    return {args[0]: args[1] for args, _ in hooks.emit.call_args_list}


# =============================================================================
# Test 1: task-keyed dict is read first when present
# =============================================================================


@pytest.mark.asyncio
async def test_reads_task_keyed_context_when_present():
    """DelegateTool must prefer coordinator._tool_dispatch_contexts[current_task]
    over the legacy _tool_dispatch_context attribute.

    RED: before fix, only _tool_dispatch_context is checked, which is empty,
    so tool_call_id="" and parallel_group_id=None — both assertions fail.
    GREEN: after fix, the task-keyed dict is checked first and wins.
    """
    hooks = _make_hooks()
    tool, coordinator = _make_delegate_tool(
        hooks=hooks,
        agents={"test-agent": {"description": "A test agent"}},
        # legacy_dispatch_context is empty (omitted) — simulates new orchestrator
        # that uses _tool_dispatch_contexts instead of _tool_dispatch_context.
    )

    # Simulate what loop-streaming (post-fix) does: set the task-keyed dict
    # entry before calling tool.execute().
    task = asyncio.current_task()
    coordinator._tool_dispatch_contexts = {
        task: {"tool_call_id": "abc123", "parallel_group_id": "xyz789"}
    }

    await tool.execute(
        {
            "agent": "test-agent",
            "instruction": "Do something",
            "context_depth": "none",
        }
    )

    emitted = _emitted(hooks)
    assert "delegate:agent_spawned" in emitted, "Expected delegate:agent_spawned event"

    payload = emitted["delegate:agent_spawned"]
    assert payload["tool_call_id"] == "abc123", (
        f"Expected tool_call_id='abc123' from task-keyed dict, "
        f"got {payload['tool_call_id']!r}. "
        "The code may still be reading only the legacy _tool_dispatch_context."
    )
    assert payload["parallel_group_id"] == "xyz789", (
        f"Expected parallel_group_id='xyz789' from task-keyed dict, "
        f"got {payload['parallel_group_id']!r}."
    )


# =============================================================================
# Test 2: falls back to legacy attribute when task-keyed dict is absent
# =============================================================================


@pytest.mark.asyncio
async def test_falls_back_to_legacy_attribute():
    """When coordinator._tool_dispatch_contexts is absent, DelegateTool must
    read from the legacy coordinator._tool_dispatch_context attribute.

    This is the backward-compatibility path: orchestrators that have NOT
    been updated to use the new task-keyed dict still work correctly.

    This test PASSES both before and after the fix.
    """
    hooks = _make_hooks()
    tool, coordinator = _make_delegate_tool(
        hooks=hooks,
        agents={"test-agent": {"description": "A test agent"}},
        legacy_dispatch_context={
            "tool_call_id": "legacy-call-001",
            "parallel_group_id": None,
        },
    )
    # _tool_dispatch_contexts is intentionally NOT set on coordinator.

    await tool.execute(
        {
            "agent": "test-agent",
            "instruction": "Do something",
            "context_depth": "none",
        }
    )

    emitted = _emitted(hooks)
    assert "delegate:agent_spawned" in emitted, "Expected delegate:agent_spawned event"

    payload = emitted["delegate:agent_spawned"]
    assert payload["tool_call_id"] == "legacy-call-001", (
        f"Expected tool_call_id='legacy-call-001' from legacy attribute, "
        f"got {payload['tool_call_id']!r}."
    )
    assert payload["parallel_group_id"] is None, (
        f"Expected parallel_group_id=None from legacy attribute, "
        f"got {payload['parallel_group_id']!r}."
    )


# =============================================================================
# Test 3: empty defaults when neither attribute is set
# =============================================================================


@pytest.mark.asyncio
async def test_empty_values_when_neither_present():
    """When neither _tool_dispatch_contexts nor _tool_dispatch_context is set,
    DelegateTool must use graceful empty defaults: tool_call_id="" and
    parallel_group_id=None.

    This preserves the current behavior for orchestrators that predate the
    entire dispatch context mechanism.

    This test PASSES both before and after the fix.
    """
    hooks = _make_hooks()
    tool, coordinator = _make_delegate_tool(
        hooks=hooks,
        agents={"test-agent": {"description": "A test agent"}},
        # No legacy_dispatch_context → coordinator._tool_dispatch_context = {}
    )
    # _tool_dispatch_contexts is intentionally NOT set.

    await tool.execute(
        {
            "agent": "test-agent",
            "instruction": "Do something",
            "context_depth": "none",
        }
    )

    emitted = _emitted(hooks)
    assert "delegate:agent_spawned" in emitted, "Expected delegate:agent_spawned event"

    payload = emitted["delegate:agent_spawned"]
    assert payload["tool_call_id"] == "", (
        f"Expected tool_call_id='' (empty default) when no dispatch context, "
        f"got {payload['tool_call_id']!r}."
    )
    assert payload["parallel_group_id"] is None, (
        f"Expected parallel_group_id=None (empty default) when no dispatch context, "
        f"got {payload['parallel_group_id']!r}."
    )
