"""Backward-compatibility guard for the structured delegation return contract.

Pins Stage 0's success criterion (spec §12): with the feature off (the
default), the delegate tool's behavior must be indistinguishable from before
this change existed. This is the test suite that would fail loudly if the
"purely additive" claim in the spec were ever violated.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from amplifier_module_tool_delegate import RETURN_CONTRACT_INSTRUCTION, DelegateTool

# =============================================================================
# Helpers
# =============================================================================


def _make_delegate_tool(
    *,
    spawn_fn=None,
    resume_fn=None,
    agents: dict | None = None,
    hooks=None,
    return_contract_config: dict | None = None,
) -> DelegateTool:
    coordinator = MagicMock()
    coordinator.session_id = "parent-session-123"
    coordinator.config = {"agents": agents or {}}
    coordinator.session_state = {}
    coordinator._tool_dispatch_context = {}
    coordinator._tool_dispatch_contexts = {}

    default_spawn_result = {
        "output": "The answer is 42.",
        "session_id": "child-001",
        "status": "success",
        "turn_count": 1,
        "metadata": {},
    }
    default_resume_result = {
        "output": "Still 42 on resume.",
        "session_id": "child-001",
        "status": "success",
        "turn_count": 2,
        "metadata": {},
    }

    capabilities: dict = {
        "session.spawn": spawn_fn or AsyncMock(return_value=default_spawn_result),
        "session.resume": resume_fn or AsyncMock(return_value=default_resume_result),
        "self_delegation_depth": 0,
    }
    coordinator.get_capability = lambda name: capabilities.get(name)
    coordinator.get = MagicMock(return_value=hooks)

    parent_session = MagicMock()
    parent_session.session_id = "parent-session-123"
    parent_session.config = {"session": {"orchestrator": {}}}
    coordinator.session = parent_session

    features: dict = {}
    if return_contract_config is not None:
        features["return_contract"] = return_contract_config

    config: dict = {"features": features, "settings": {"exclude_tools": []}}
    return DelegateTool(coordinator, config)


def _make_hooks() -> MagicMock:
    hooks = MagicMock()
    hooks.emit = AsyncMock()
    return hooks


# =============================================================================
# Tests: feature off by default -- response byte-identical, contract inert
# =============================================================================


class TestFeatureOffByDefault:
    @pytest.mark.asyncio
    async def test_spawn_response_byte_identical_when_feature_off(self):
        """With no return_contract config at all (the shipped default),
        output["response"] is byte-identical to what spawn returned."""
        spawn_fn = AsyncMock(
            return_value={
                "output": "The answer is 42.",
                "session_id": "child-001",
                "status": "success",
                "turn_count": 1,
                "metadata": {},
            }
        )
        tool = _make_delegate_tool(
            spawn_fn=spawn_fn, agents={"test-agent": {"description": "d"}}
        )

        result = await tool.execute(
            {
                "agent": "test-agent",
                "instruction": "Do something",
                "context_depth": "none",
            }
        )

        assert result.success
        assert result.output["response"] == "The answer is 42."
        assert result.output["contract"]["conformant"] is None

    @pytest.mark.asyncio
    async def test_resume_response_byte_identical_when_feature_off(self):
        resume_fn = AsyncMock(
            return_value={
                "output": "Still 42 on resume.",
                "session_id": "child-001",
                "status": "success",
                "turn_count": 2,
                "metadata": {},
            }
        )
        tool = _make_delegate_tool(resume_fn=resume_fn)

        result = await tool.execute(
            {"session_id": "child-001", "instruction": "Continue"}
        )

        assert result.success
        assert result.output["response"] == "Still 42 on resume."
        assert result.output["contract"]["conformant"] is None

    @pytest.mark.asyncio
    async def test_no_contract_instruction_injected_on_spawn_when_off(self):
        """The instruction reaching session.spawn must NOT contain
        RETURN_CONTRACT_INSTRUCTION when the feature is disabled."""
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
            spawn_fn=spawn_fn, agents={"test-agent": {"description": "d"}}
        )

        await tool.execute(
            {
                "agent": "test-agent",
                "instruction": "Do something",
                "context_depth": "none",
            }
        )

        spawn_fn.assert_called_once()
        _, kwargs = spawn_fn.call_args
        assert RETURN_CONTRACT_INSTRUCTION not in kwargs["instruction"]
        assert kwargs["instruction"] == "Do something"

    @pytest.mark.asyncio
    async def test_no_contract_instruction_injected_on_resume_when_off(self):
        resume_fn = AsyncMock(
            return_value={
                "output": "done",
                "session_id": "child-001",
                "status": "success",
                "turn_count": 1,
                "metadata": {},
            }
        )
        tool = _make_delegate_tool(resume_fn=resume_fn)

        await tool.execute({"session_id": "child-001", "instruction": "Continue"})

        resume_fn.assert_called_once()
        _, kwargs = resume_fn.call_args
        assert RETURN_CONTRACT_INSTRUCTION not in kwargs["instruction"]
        assert kwargs["instruction"] == "Continue"

    @pytest.mark.asyncio
    async def test_completed_event_carries_contract_conformant_none(self):
        hooks = _make_hooks()
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
            hooks=hooks,
            agents={"test-agent": {"description": "d"}},
        )

        await tool.execute(
            {
                "agent": "test-agent",
                "instruction": "Do something",
                "context_depth": "none",
            }
        )

        emitted = {args[0]: args[1] for args, _ in hooks.emit.call_args_list}
        completed = emitted["delegate:agent_completed"]
        assert completed["contract_conformant"] is None
        assert completed["findings_count"] is None
        assert completed["evidence_backed_count"] is None
        assert completed["not_covered_count"] is None
        assert completed["artifacts_count"] is None


# =============================================================================
# Tests: per-agent opt-out overrides a globally-enabled feature
# =============================================================================


class TestPerAgentOptOut:
    @pytest.mark.asyncio
    async def test_per_agent_opt_out_suppresses_injection_with_feature_on(self):
        """An agent config with return_contract: false suppresses injection
        even though the tool-level feature is globally enabled."""
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
                "git-ops": {"description": "d", "return_contract": False},
            },
            return_contract_config={"enabled": True},
        )

        result = await tool.execute(
            {
                "agent": "git-ops",
                "instruction": "Commit this",
                "context_depth": "none",
            }
        )

        _, kwargs = spawn_fn.call_args
        assert RETURN_CONTRACT_INSTRUCTION not in kwargs["instruction"]
        # The parser still runs (feature is globally on), but since no agent
        # opted-out response ever carries a fenced block, it degrades to a
        # normal non-conformant parse -- never None, since the FEATURE
        # itself is enabled (only this agent's injection was suppressed).
        assert result.output["contract"]["conformant"] is False
