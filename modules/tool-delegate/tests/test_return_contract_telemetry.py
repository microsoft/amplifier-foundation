"""Telemetry enrichment tests for the structured delegation return contract.

`delegate:agent_completed` gains five additive fields (contract_conformant,
findings_count, evidence_backed_count, not_covered_count, artifacts_count) --
see spec §7. No new events are introduced; this suite pins that the existing
event is enriched correctly on both the spawn and resume completion paths.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from amplifier_module_tool_delegate import DelegateTool

_CONFORMANT_RESPONSE = (
    "Here is what I found.\n\n"
    "```json\n"
    '{"summary": "done", '
    '"findings": ['
    '{"claim": "a", "evidence": "foo.py:1", "confidence": "high"}, '
    '{"claim": "b", "evidence": "", "confidence": "low"}, '
    '{"claim": "c", "evidence": "bar.py:9", "confidence": "medium"}'
    "], "
    '"not_covered": ["thing one", "thing two"], '
    '"artifacts": [{"path": "src/x.py", "description": "edited"}]}\n'
    "```"
)


def _make_delegate_tool(
    *,
    spawn_output: str | None = None,
    resume_output: str | None = None,
    hooks=None,
    return_contract_enabled: bool = True,
) -> DelegateTool:
    coordinator = MagicMock()
    coordinator.session_id = "parent-session-123"
    coordinator.config = {"agents": {"test-agent": {"description": "d"}}}
    coordinator.session_state = {}
    coordinator._tool_dispatch_context = {}
    coordinator._tool_dispatch_contexts = {}

    spawn_fn = AsyncMock(
        return_value={
            "output": spawn_output if spawn_output is not None else "done",
            "session_id": "child-001",
            "status": "success",
            "turn_count": 1,
            "metadata": {},
        }
    )
    resume_fn = AsyncMock(
        return_value={
            "output": resume_output if resume_output is not None else "done",
            "session_id": "child-001",
            "status": "success",
            "turn_count": 2,
            "metadata": {},
        }
    )
    capabilities: dict = {
        "session.spawn": spawn_fn,
        "session.resume": resume_fn,
        "self_delegation_depth": 0,
    }
    coordinator.get_capability = lambda name: capabilities.get(name)
    coordinator.get = MagicMock(return_value=hooks)

    parent_session = MagicMock()
    parent_session.session_id = "parent-session-123"
    parent_session.config = {"session": {"orchestrator": {}}}
    coordinator.session = parent_session

    config: dict = {
        "features": {"return_contract": {"enabled": return_contract_enabled}},
        "settings": {"exclude_tools": []},
    }
    return DelegateTool(coordinator, config)


def _make_hooks() -> MagicMock:
    hooks = MagicMock()
    hooks.emit = AsyncMock()
    return hooks


class TestSpawnTelemetry:
    @pytest.mark.asyncio
    async def test_conformant_return_carries_correct_counts(self):
        hooks = _make_hooks()
        tool = _make_delegate_tool(spawn_output=_CONFORMANT_RESPONSE, hooks=hooks)

        await tool.execute(
            {
                "agent": "test-agent",
                "instruction": "investigate",
                "context_depth": "none",
            }
        )

        emitted = {args[0]: args[1] for args, _ in hooks.emit.call_args_list}
        completed = emitted["delegate:agent_completed"]
        assert completed["contract_conformant"] is True
        assert completed["findings_count"] == 3
        assert completed["not_covered_count"] == 2
        assert completed["artifacts_count"] == 1

    @pytest.mark.asyncio
    async def test_evidence_backed_count_only_counts_non_empty_evidence(self):
        """Of the 3 findings above, only 2 have non-empty evidence."""
        hooks = _make_hooks()
        tool = _make_delegate_tool(spawn_output=_CONFORMANT_RESPONSE, hooks=hooks)

        await tool.execute(
            {
                "agent": "test-agent",
                "instruction": "investigate",
                "context_depth": "none",
            }
        )

        emitted = {args[0]: args[1] for args, _ in hooks.emit.call_args_list}
        completed = emitted["delegate:agent_completed"]
        assert completed["evidence_backed_count"] == 2

    @pytest.mark.asyncio
    async def test_non_conformant_return_has_false_and_zero_counts(self):
        hooks = _make_hooks()
        tool = _make_delegate_tool(spawn_output="just plain prose", hooks=hooks)

        await tool.execute(
            {
                "agent": "test-agent",
                "instruction": "investigate",
                "context_depth": "none",
            }
        )

        emitted = {args[0]: args[1] for args, _ in hooks.emit.call_args_list}
        completed = emitted["delegate:agent_completed"]
        assert completed["contract_conformant"] is False
        assert completed["findings_count"] == 0
        assert completed["evidence_backed_count"] == 0
        assert completed["not_covered_count"] == 0
        assert completed["artifacts_count"] == 0


class TestResumeTelemetry:
    @pytest.mark.asyncio
    async def test_resume_path_emits_same_enrichment_as_spawn(self):
        hooks = _make_hooks()
        tool = _make_delegate_tool(resume_output=_CONFORMANT_RESPONSE, hooks=hooks)

        await tool.execute({"session_id": "child-001", "instruction": "continue"})

        emitted = {args[0]: args[1] for args, _ in hooks.emit.call_args_list}
        completed = emitted["delegate:agent_completed"]
        assert completed["contract_conformant"] is True
        assert completed["findings_count"] == 3
        assert completed["evidence_backed_count"] == 2
        assert completed["not_covered_count"] == 2
        assert completed["artifacts_count"] == 1
