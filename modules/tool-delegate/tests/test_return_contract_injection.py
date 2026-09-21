"""Injection coverage tests for the structured delegation return contract.

Pins spec §4.3's coverage claim: injecting RETURN_CONTRACT_INSTRUCTION at the
tool level (rather than in 16+ agent .md files) covers every delegation
target uniformly -- registered agents, agent="self", and bare bundle paths --
none of which share a common agent file.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from amplifier_module_tool_delegate import RETURN_CONTRACT_INSTRUCTION, DelegateTool


def _make_delegate_tool(
    *,
    spawn_fn=None,
    resume_fn=None,
    agents: dict | None = None,
    return_contract_config: dict | None = None,
) -> DelegateTool:
    coordinator = MagicMock()
    coordinator.session_id = "parent-session-123"
    coordinator.config = {"agents": agents or {}}
    coordinator.session_state = {}
    coordinator._tool_dispatch_context = {}
    coordinator._tool_dispatch_contexts = {}

    default_spawn_result = {
        "output": "done",
        "session_id": "child-001",
        "status": "success",
        "turn_count": 1,
        "metadata": {},
    }
    default_resume_result = {
        "output": "done",
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
    coordinator.get = MagicMock(return_value=None)

    parent_session = MagicMock()
    parent_session.session_id = "parent-session-123"
    parent_session.config = {"session": {"orchestrator": {}}}
    coordinator.session = parent_session

    config: dict = {
        "features": {"return_contract": return_contract_config or {"enabled": True}},
        "settings": {"exclude_tools": []},
    }
    return DelegateTool(coordinator, config)


class TestSpawnInjection:
    @pytest.mark.asyncio
    async def test_instruction_ends_with_contract_instruction(self):
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

        _, kwargs = spawn_fn.call_args
        assert kwargs["instruction"].endswith(RETURN_CONTRACT_INSTRUCTION)

    @pytest.mark.asyncio
    async def test_your_task_composition_preserved_with_context_inheritance(self):
        """When context inheritance is also on, the [YOUR TASK] composition
        from the context-formatting step must still be present, with the
        contract instruction appended after it (not replacing it)."""
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
        # Force the parent-context-inheritance path to produce parent_messages.
        tool._build_inherited_context = AsyncMock(
            return_value=[{"role": "user", "content": "earlier turn"}]
        )

        await tool.execute(
            {
                "agent": "test-agent",
                "instruction": "Do something",
                "context_depth": "recent",
            }
        )

        _, kwargs = spawn_fn.call_args
        instruction = kwargs["instruction"]
        assert "[YOUR TASK]" in instruction
        assert "Do something" in instruction
        assert instruction.endswith(RETURN_CONTRACT_INSTRUCTION)

    @pytest.mark.asyncio
    async def test_agent_self_receives_instruction(self):
        """agent="self" has no shared agent .md file, but must still receive
        the contract instruction via the tool-level injection point."""
        spawn_fn = AsyncMock(
            return_value={
                "output": "done",
                "session_id": "child-001",
                "status": "success",
                "turn_count": 1,
                "metadata": {},
            }
        )
        tool = _make_delegate_tool(spawn_fn=spawn_fn)

        result = await tool.execute(
            {
                "agent": "self",
                "instruction": "Recurse on this",
                "context_depth": "none",
            }
        )

        assert result.success
        _, kwargs = spawn_fn.call_args
        assert kwargs["instruction"].endswith(RETURN_CONTRACT_INSTRUCTION)

    @pytest.mark.asyncio
    async def test_bare_bundle_path_receives_instruction(self):
        """A bare bundle path (e.g. "foundation:explorer") is not in the
        agents registry and has no shared agent file, but must still
        receive the contract instruction."""
        spawn_fn = AsyncMock(
            return_value={
                "output": "done",
                "session_id": "child-001",
                "status": "success",
                "turn_count": 1,
                "metadata": {},
            }
        )
        tool = _make_delegate_tool(spawn_fn=spawn_fn)

        result = await tool.execute(
            {
                "agent": "foundation:explorer",
                "instruction": "Explore the codebase",
                "context_depth": "none",
            }
        )

        assert result.success
        _, kwargs = spawn_fn.call_args
        assert kwargs["instruction"].endswith(RETURN_CONTRACT_INSTRUCTION)


class TestResumeInjection:
    @pytest.mark.asyncio
    async def test_resume_instruction_carries_contract_instruction(self):
        resume_fn = AsyncMock(
            return_value={
                "output": "done",
                "session_id": "child-001",
                "status": "success",
                "turn_count": 2,
                "metadata": {},
            }
        )
        tool = _make_delegate_tool(resume_fn=resume_fn)

        await tool.execute({"session_id": "child-001", "instruction": "Continue"})

        _, kwargs = resume_fn.call_args
        assert kwargs["instruction"].endswith(RETURN_CONTRACT_INSTRUCTION)
        assert "Continue" in kwargs["instruction"]

    @pytest.mark.asyncio
    async def test_resume_respects_per_agent_opt_out_via_spawn_cache(self):
        """A session originally spawned for an agent with return_contract:
        false must not receive the instruction on resume either -- opt-out
        is resolved via the same agent identity used at spawn time."""
        spawn_fn = AsyncMock(
            side_effect=lambda **kwargs: {
                "output": "spawned",
                "session_id": kwargs["sub_session_id"],
                "status": "success",
                "turn_count": 1,
                "metadata": {},
            }
        )
        resume_fn = AsyncMock(
            return_value={
                "output": "resumed",
                "session_id": "will-be-overwritten",
                "status": "success",
                "turn_count": 2,
                "metadata": {},
            }
        )
        tool = _make_delegate_tool(
            spawn_fn=spawn_fn,
            resume_fn=resume_fn,
            agents={"git-ops": {"description": "d", "return_contract": False}},
        )

        spawn_result = await tool.execute(
            {
                "agent": "git-ops",
                "instruction": "Commit this",
                "context_depth": "none",
            }
        )
        real_session_id = spawn_result.output["session_id"]

        await tool.execute(
            {"session_id": real_session_id, "instruction": "Now push it"}
        )

        _, resume_kwargs = resume_fn.call_args
        assert RETURN_CONTRACT_INSTRUCTION not in resume_kwargs["instruction"]
        assert resume_kwargs["instruction"] == "Now push it"


class TestFeatureOffNoInjectionAnywhere:
    @pytest.mark.asyncio
    async def test_self_and_bundle_path_unaffected_when_feature_off(self):
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
            spawn_fn=spawn_fn, return_contract_config={"enabled": False}
        )

        await tool.execute(
            {
                "agent": "self",
                "instruction": "Recurse on this",
                "context_depth": "none",
            }
        )

        _, kwargs = spawn_fn.call_args
        assert kwargs["instruction"] == "Recurse on this"
