"""A maintenance continuation retains delegation's own completion contract."""

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from amplifier_module_tool_delegate import DelegateTool


class ChildSuspended(BaseException):
    pass


class DelegationSuspended(BaseException):
    def __init__(self, continuation):
        self.continuation = continuation


def make_tool():
    coordinator = MagicMock()
    coordinator.session_id = "parent"
    coordinator.session_state = {}
    coordinator.config = {"agents": {"worker": {}}}
    coordinator._tool_dispatch_contexts = {}
    coordinator._tool_dispatch_context = {"tool_call_id": "original-call"}
    coordinator.get = MagicMock(return_value=None)
    coordinator.session.config = {"session": {"orchestrator": {}}}
    coordinator.hooks.emit = AsyncMock()
    capabilities = {}
    coordinator.get_capability = capabilities.get
    tool = DelegateTool(coordinator, {"features": {}, "settings": {"timeout": 120}})
    return tool, capabilities


@pytest.mark.asyncio
async def test_suspend_resume_keeps_original_tool_and_child_completion_owner():
    tool, capabilities = make_tool()
    spawned = []

    async def spawn(**kwargs):
        spawned.append(kwargs)
        raise ChildSuspended()

    class Owner:
        def capture(self, error, record):
            if isinstance(error, ChildSuspended):
                return DelegationSuspended({"delegation": record, "child": {"checkpoint": "boundary-1"}})
            return None

    capabilities.update({"session.spawn": spawn, "session.suspension": Owner()})
    with pytest.raises(DelegationSuspended) as held:
        await tool._spawn_new_session(
            "worker", "perform once", "none", "conversation", 5, None,
            tool.coordinator.hooks, tool_call_id="original-call", call_budget_override=3)
    record = held.value.continuation["delegation"]
    assert len(spawned) == 1
    assert record["child_session_id"] == spawned[0]["sub_session_id"]
    assert 0 < record["timeout_remaining"] <= 120
    completed_before = [call for call in tool.coordinator.hooks.emit.call_args_list
                        if call.args[0] == "delegate:agent_completed"]
    assert completed_before == []
    resume = AsyncMock(return_value={"session_id": record["child_session_id"], "output": "finished",
                                    "metadata": {"llm_call_budget": {"llm_calls": 2}},
                                    "turn_count": 2})
    capabilities["session.execution_resume"] = resume
    # A fresh tool owner can finish the original call; no spawn/input replay.
    replacement = DelegateTool(tool.coordinator, {"features": {}, "settings": {"timeout": 120}})
    result = await replacement.resume_execution(held.value.continuation)
    assert len(spawned) == 1
    resume.assert_awaited_once_with(held.value.continuation)
    assert result.output["session_id"] == record["child_session_id"]
    assert result.output["response"] == "finished"
    assert result.output["agent"] == "worker"
    assert result.output["metadata"]["budget_enforced"] is True
    completed = [call.args[1] for call in tool.coordinator.hooks.emit.call_args_list
                 if call.args[0] == "delegate:agent_completed"]
    assert len(completed) == 1
    assert completed[0]["tool_call_id"] == "original-call"
    assert completed[0]["sub_session_id"] == record["child_session_id"]


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["parent", "call", "deadline", "unsupported"])
async def test_resume_refuses_changed_authority_without_invoking_child(change):
    tool, capabilities = make_tool()
    resume = AsyncMock()
    capabilities["session.execution_resume"] = resume
    record = {"version": 1, "engine": "tool-delegate", "parent_session_id": "parent",
              "child_session_id": "child", "tool_call_id": "original-call",
              "agent": "worker", "parallel_group_id": None, "timeout_remaining": 10}
    if change == "parent":
        record["parent_session_id"] = "other-parent"
    elif change == "call":
        record["tool_call_id"] = "different-call"
    elif change == "deadline":
        record["timeout_remaining"] = 0
    else:
        capabilities.pop("session.execution_resume")
    with pytest.raises((ValueError, RuntimeError)):
        await tool.resume_execution({"delegation": record})
    resume.assert_not_called()


@pytest.mark.asyncio
async def test_ordinary_cancellation_is_not_a_suspend_receipt():
    tool, capabilities = make_tool()
    captures = []

    class Owner:
        def capture(self, error, record):
            captures.append(error)
            return None

    capabilities["session.suspension"] = Owner()

    async def cancelled():
        raise asyncio.CancelledError()

    with pytest.raises(asyncio.CancelledError):
        await tool._await_suspendable_child(cancelled(), {"tool_call_id": "original-call"})
    assert len(captures) == 1 and isinstance(captures[0], asyncio.CancelledError)
