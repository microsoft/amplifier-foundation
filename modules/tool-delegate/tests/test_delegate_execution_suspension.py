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


def continuation():
    return {"delegation": {
        "version": 1, "engine": "tool-delegate", "parent_session_id": "parent",
        "child_session_id": "child", "tool_call_id": "original-call",
        "agent": "worker", "parallel_group_id": "original-group",
        "timeout_remaining": 10,
    }}


def events(tool, name):
    return [call.args[1] for call in tool.coordinator.hooks.emit.call_args_list
            if call.args[0] == name]


@pytest.mark.asyncio
@pytest.mark.parametrize("bad", ["wrong-child", "missing-child", "bad-metadata"])
async def test_invalid_resume_result_never_publishes_completion(bad):
    tool, capabilities = make_tool()
    result = {"session_id": "child", "output": "finished"}
    if bad == "wrong-child":
        result["session_id"] = "another-child"
    elif bad == "missing-child":
        del result["session_id"]
    else:
        result["metadata"] = 7
    resume = AsyncMock(return_value=result)
    capabilities["session.execution_resume"] = resume
    held = continuation()
    returned = await tool.resume_execution(held)
    assert returned.success is False
    assert events(tool, "delegate:agent_completed") == []
    assert events(tool, "delegate:error")[0]["session_id"] == "child"
    resume.assert_awaited_once_with(held)


@pytest.mark.asyncio
async def test_resumed_child_failure_returns_attributed_error():
    tool, capabilities = make_tool()
    resume = AsyncMock(side_effect=RuntimeError("child failed"))
    capabilities["session.execution_resume"] = resume
    result = await tool.resume_execution(continuation())
    assert result.success is False
    assert "child failed" in result.error["message"]
    assert events(tool, "delegate:agent_completed") == []
    error, = events(tool, "delegate:error")
    assert error["tool_call_id"] == "original-call"
    assert error["session_id"] == "child"
    resume.assert_awaited_once()


@pytest.mark.asyncio
async def test_resumed_timeout_keeps_completed_sibling():
    tool, capabilities = make_tool()
    held = continuation()
    held["delegation"]["timeout_remaining"] = 0.01
    cleaned = asyncio.Event()

    async def waiting(frame):
        try:
            await asyncio.Event().wait()
        finally:
            cleaned.set()

    capabilities["session.execution_resume"] = waiting

    async def sibling():
        return "settled sibling"

    results = await asyncio.gather(sibling(), tool.resume_execution(held))
    await asyncio.wait_for(cleaned.wait(), 1)
    assert results[0] == "settled sibling"
    result = results[1]
    assert result.success is False and result.output["status"] == "timeout"
    assert result.output["session_id"] == "child"
    assert result.output["metadata"]["timeout_seconds"] == 0.01
    assert result.output["metadata"]["resumable"] is False
    assert events(tool, "delegate:agent_completed") == []
    assert events(tool, "delegate:error")[0]["error_type"] == "delegate_timeout"


@pytest.mark.asyncio
async def test_resumed_cancellation_propagates_with_attribution():
    tool, capabilities = make_tool()
    resume = AsyncMock(side_effect=asyncio.CancelledError())
    capabilities["session.execution_resume"] = resume
    with pytest.raises(asyncio.CancelledError):
        await tool.resume_execution(continuation())
    assert events(tool, "delegate:agent_completed") == []
    cancelled, = events(tool, "delegate:agent_cancelled")
    assert cancelled["tool_call_id"] == "original-call"
    assert cancelled["session_id"] == "child"
    resume.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("reason", ["stopped", "unknown prior claim", "already consumed"])
async def test_host_admission_refusal_is_not_retried(reason):
    tool, capabilities = make_tool()
    resume, spawn = AsyncMock(side_effect=RuntimeError(reason)), AsyncMock()
    capabilities.update({"session.execution_resume": resume, "session.spawn": spawn})
    held = continuation()
    result = await tool.resume_execution(held)
    assert not result.success
    resume.assert_awaited_once_with(held)
    spawn.assert_not_called()
    assert events(tool, "delegate:agent_completed") == []


@pytest.mark.asyncio
async def test_unknown_completion_ack_does_not_retry_child_or_event():
    tool, capabilities = make_tool()
    resume = AsyncMock(return_value={"session_id": "child", "output": "finished"})
    capabilities["session.execution_resume"] = resume
    observed = []

    async def emit(name, data):
        observed.append((name, data))
        raise OSError("acknowledgment unknown")

    tool.coordinator.hooks.emit = emit
    with pytest.raises(OSError, match="acknowledgment unknown"):
        await tool.resume_execution(continuation())
    resume.assert_awaited_once()
    assert len(observed) == 1 and observed[0][0] == "delegate:agent_completed"
    # This is deliberately not retried: the host owns the consumed claim.


@pytest.mark.asyncio
async def test_repeated_suspension_keeps_child_and_remaining_deadline():
    tool, capabilities = make_tool()
    held = continuation()

    class Owner:
        def capture(self, error, record):
            if isinstance(error, ChildSuspended):
                return DelegationSuspended({"delegation": record, "child": "second-boundary"})
            return None

    capabilities.update({"session.execution_resume": AsyncMock(side_effect=ChildSuspended()),
                         "session.suspension": Owner()})
    with pytest.raises(DelegationSuspended) as paused:
        await tool.resume_execution(held)
    record = paused.value.continuation["delegation"]
    assert record["child_session_id"] == "child"
    assert record["tool_call_id"] == "original-call"
    assert 0 < record["timeout_remaining"] <= 10
    assert events(tool, "delegate:agent_completed") == []
    assert events(tool, "delegate:error") == []
