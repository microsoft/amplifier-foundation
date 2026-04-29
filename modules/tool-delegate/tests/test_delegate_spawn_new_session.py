"""Tests for _spawn_new_session() helper extracted for testability.

Covers the two bundled changes included in the register_contributor migration:
  1. Extraction of _spawn_new_session() from execute() for direct testability.
  2. isinstance(orch_section, dict) guard: fixes AttributeError when orchestrator
     config is a bare string like "loop-basic" (calling .get("config") on a str
     raised AttributeError before the guard was added).
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from amplifier_module_tool_delegate import DelegateTool


# =============================================================================
# Helpers
# =============================================================================


def _make_tool(*, orchestrator_value=None) -> DelegateTool:
    """Create a DelegateTool wired for _spawn_new_session() tests.

    Args:
        orchestrator_value: Value to use for session.orchestrator in the
            parent session config. Pass a string (e.g. "loop-basic") to
            exercise the bare-string guard, or a dict for normal usage.
            Defaults to an empty dict (the typical safe value).
    """
    coordinator = MagicMock()
    coordinator.session_id = "parent-session-123"
    coordinator.config = {"agents": {}}
    coordinator.session_state = {}
    coordinator._tool_dispatch_context = {}

    spawn_result = {
        "output": "done",
        "session_id": "child-001",
        "status": "success",
        "turn_count": 1,
        "metadata": {},
    }

    capabilities: dict = {
        "session.spawn": AsyncMock(return_value=spawn_result),
        "self_delegation_depth": 0,
    }

    coordinator.get_capability = lambda name: capabilities.get(name)
    coordinator.get = MagicMock(return_value=None)

    parent_session = MagicMock()
    parent_session.session_id = "parent-session-123"
    parent_session.config = {
        "session": {
            "orchestrator": orchestrator_value if orchestrator_value is not None else {}
        }
    }
    coordinator.session = parent_session

    config: dict = {"features": {}, "settings": {"exclude_tools": []}}
    return DelegateTool(coordinator, config)


# =============================================================================
# Tests: isinstance(orch_section, dict) guard (regression for bare-string bug)
# =============================================================================


class TestOrchSectionIsinstanceGuard:
    """isinstance guard prevents AttributeError when orchestrator is a bare string.

    Before the guard was added, orch_section.get("config") raised
    AttributeError: 'str' object has no attribute 'get'
    when the orchestrator config was a string like "loop-basic".
    """

    @pytest.mark.asyncio
    async def test_bare_string_orchestrator_does_not_raise(self):
        """Bare-string orchestrator config must not raise AttributeError.

        Regression: calling .get("config") on a string raises AttributeError.
        The isinstance(orch_section, dict) guard prevents this.
        """
        tool = _make_tool(orchestrator_value="loop-basic")

        # Must not raise AttributeError ('str' object has no attribute 'get')
        result = await tool._spawn_new_session(
            agent_name="test-agent",
            instruction="do something",
            context_depth="none",
            context_scope="conversation",
            context_turns=5,
            provider_preferences=None,
            hooks=None,
        )

        assert result is not None

    @pytest.mark.asyncio
    async def test_bare_string_orchestrator_yields_no_orchestrator_config(self):
        """With a bare-string orchestrator, orchestrator_config is NOT inherited.

        When the orchestrator field is a string, no orchestrator_config is
        passed to spawn (None), rather than crashing or incorrectly passing
        the string itself.
        """
        spawn_fn = AsyncMock(
            return_value={
                "output": "done",
                "session_id": "child-001",
                "status": "success",
                "turn_count": 1,
                "metadata": {},
            }
        )

        coordinator = MagicMock()
        coordinator.session_id = "parent-session-123"
        coordinator.config = {"agents": {}}
        coordinator.session_state = {}
        coordinator._tool_dispatch_context = {}
        coordinator.get_capability = lambda name: (
            spawn_fn if name == "session.spawn" else None
        )
        coordinator.get = MagicMock(return_value=None)

        parent_session = MagicMock()
        parent_session.session_id = "parent-session-123"
        parent_session.config = {"session": {"orchestrator": "loop-basic"}}
        coordinator.session = parent_session

        tool = DelegateTool(
            coordinator, {"features": {}, "settings": {"exclude_tools": []}}
        )

        await tool._spawn_new_session(
            agent_name="test-agent",
            instruction="do something",
            context_depth="none",
            context_scope="conversation",
            context_turns=5,
            provider_preferences=None,
            hooks=None,
        )

        # orchestrator_config kwarg must be None (not the string "loop-basic")
        call_kwargs = spawn_fn.call_args.kwargs
        assert call_kwargs.get("orchestrator_config") is None, (
            f"Expected orchestrator_config=None for bare-string orchestrator, "
            f"got {call_kwargs.get('orchestrator_config')!r}"
        )

    @pytest.mark.asyncio
    async def test_dict_orchestrator_with_config_is_inherited(self):
        """Dict orchestrator with 'config' key is still inherited correctly.

        The isinstance guard must not break the normal dict path.
        """
        spawn_fn = AsyncMock(
            return_value={
                "output": "done",
                "session_id": "child-001",
                "status": "success",
                "turn_count": 1,
                "metadata": {},
            }
        )

        coordinator = MagicMock()
        coordinator.session_id = "parent-session-123"
        coordinator.config = {"agents": {}}
        coordinator.session_state = {}
        coordinator._tool_dispatch_context = {}
        coordinator.get_capability = lambda name: (
            spawn_fn if name == "session.spawn" else None
        )
        coordinator.get = MagicMock(return_value=None)

        expected_orch_config = {"max_turns": 10}
        parent_session = MagicMock()
        parent_session.session_id = "parent-session-123"
        parent_session.config = {
            "session": {
                "orchestrator": {"type": "loop-basic", "config": expected_orch_config}
            }
        }
        coordinator.session = parent_session

        tool = DelegateTool(
            coordinator, {"features": {}, "settings": {"exclude_tools": []}}
        )

        await tool._spawn_new_session(
            agent_name="test-agent",
            instruction="do something",
            context_depth="none",
            context_scope="conversation",
            context_turns=5,
            provider_preferences=None,
            hooks=None,
        )

        call_kwargs = spawn_fn.call_args.kwargs
        assert call_kwargs.get("orchestrator_config") == expected_orch_config, (
            f"Expected orchestrator_config={expected_orch_config!r}, "
            f"got {call_kwargs.get('orchestrator_config')!r}"
        )
