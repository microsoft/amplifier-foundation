"""Tests for delegate timeout configuration, results, and lifecycle events."""

from __future__ import annotations

import asyncio
import gc
import logging
from unittest.mock import AsyncMock, MagicMock

import pytest
from amplifier_module_tool_delegate import _NO_PARTIAL_GUIDANCE, DelegateTool

_ABSENT = object()

#: The additive partial-result keys a timeout result carries when NOTHING was
#: recovered -- i.e. with no ``session.partial`` capability registered, which
#: is every case until the app-layer producer half lands. Kept here so the
#: two exact-shape assertions below stay exact rather than degrading into
#: "contains these keys".
_NO_PARTIAL_FIELDS = {
    "completed": False,
    "partial_available": False,
    "partial_response": None,
    "partial_segments": 0,
    "partial_source": "none",
    "partial_truncated": False,
    "partial_chars_total": 0,
    "guidance": _NO_PARTIAL_GUIDANCE,
}


def _pop_elapsed(output: dict) -> float:
    """Remove and return metadata.elapsed_s, which varies run to run."""
    return output["metadata"].pop("elapsed_s")


def _make_tool(
    *,
    timeout: object = _ABSENT,
    spawn_fn=None,
    resume_fn=None,
) -> DelegateTool:
    coordinator = MagicMock()
    coordinator.session_id = "parent-session-123"
    coordinator.config = {"agents": {"test-agent": {}}}
    coordinator.session_state = {}
    coordinator._tool_dispatch_context = {}
    coordinator._tool_dispatch_contexts = {}

    capabilities = {
        "session.spawn": spawn_fn or AsyncMock(),
        "session.resume": resume_fn or AsyncMock(),
        "self_delegation_depth": 0,
    }
    coordinator.get_capability = lambda name: capabilities.get(name)
    coordinator.get = MagicMock(return_value=None)

    parent_session = MagicMock()
    parent_session.config = {"session": {"orchestrator": {}}}
    coordinator.session = parent_session

    settings: dict[str, object] = {"exclude_tools": []}
    if timeout is not _ABSENT:
        settings["timeout"] = timeout
    return DelegateTool(coordinator, {"features": {}, "settings": settings})


def _hooks() -> MagicMock:
    hooks = MagicMock()
    hooks.emit = AsyncMock()
    return hooks


async def _never_finishes(**_kwargs):
    await asyncio.Future()


async def _is_cancelled(**_kwargs):
    raise asyncio.CancelledError


async def _capability_times_out(**_kwargs):
    raise TimeoutError("capability timeout")


async def _spawn(tool: DelegateTool, hooks):
    return await tool._spawn_new_session(
        agent_name="test-agent",
        instruction="Do something",
        context_depth="none",
        context_scope="conversation",
        context_turns=5,
        provider_preferences=None,
        hooks=hooks,
        tool_call_id="call-timeout",
        parallel_group_id="parallel-timeout",
    )


async def _resume(tool: DelegateTool, hooks):
    return await tool._resume_existing_session(
        session_id="child-session-001_test-agent",
        instruction="Continue",
        hooks=hooks,
        tool_call_id="call-resume-timeout",
        parallel_group_id="parallel-resume-timeout",
    )


def _emissions(hooks: MagicMock) -> list[tuple[str, dict]]:
    return [(args[0], args[1]) for args, _kwargs in hooks.emit.call_args_list]


def test_timeout_defaults_only_when_key_is_absent():
    assert _make_tool().timeout == 14400
    assert _make_tool(timeout=None).timeout is None


@pytest.mark.parametrize("timeout", [1, 0.5, 14400, 10**100])
def test_timeout_accepts_positive_finite_non_bool_numbers(timeout):
    assert _make_tool(timeout=timeout).timeout == timeout


@pytest.mark.parametrize("timeout", [True, False, "1", []])
def test_timeout_rejects_invalid_types_eagerly(timeout):
    with pytest.raises(TypeError, match="settings.timeout"):
        _make_tool(timeout=timeout)


@pytest.mark.parametrize(
    "timeout",
    [0, -1, -0.5, float("inf"), float("-inf"), float("nan")],
)
def test_timeout_rejects_invalid_numeric_values_eagerly(timeout):
    with pytest.raises(ValueError, match="settings.timeout"):
        _make_tool(timeout=timeout)


@pytest.mark.parametrize("timeout", [10**1000, float("inf")])
def test_timeout_rejects_unrepresentable_values_before_creating_a_child(
    timeout, recwarn
):
    spawn_fn = MagicMock()

    with pytest.raises(ValueError, match="settings.timeout"):
        _make_tool(timeout=timeout, spawn_fn=spawn_fn)

    assert spawn_fn.call_count == 0
    assert not recwarn


@pytest.mark.asyncio
async def test_spawn_timeout_reports_pending_cleanup_error_without_completed_event():
    spawn_fn = AsyncMock(side_effect=_never_finishes)
    hooks = _hooks()
    tool = _make_tool(timeout=0.01, spawn_fn=spawn_fn)

    result = await _spawn(tool, hooks)

    child_session_id = spawn_fn.call_args.kwargs["sub_session_id"]
    assert result.success is False
    elapsed_s = _pop_elapsed(result.output)
    assert elapsed_s >= 0.01
    assert result.output == {
        "session_id": child_session_id,
        "agent": "test-agent",
        "status": "timeout",
        **_NO_PARTIAL_FIELDS,
        "metadata": {
            "timeout_seconds": 0.01,
            "resumable": False,
            "resume_status": "pending_child_cleanup",
            "recovery_message": (
                "Child cancellation cleanup is still in progress; do not resume "
                "this session until cleanup and persistence complete."
            ),
        },
    }

    emissions = _emissions(hooks)
    event_names = [name for name, _payload in emissions]
    assert "delegate:agent_cancelled" not in event_names
    assert "delegate:agent_completed" not in event_names
    error = next(payload for name, payload in emissions if name == "delegate:error")
    assert error["agent"] == "test-agent"
    assert error["sub_session_id"] == child_session_id
    assert error["error_type"] == "delegate_timeout"
    assert error["status"] == "timeout"
    assert error["timeout_seconds"] == 0.01
    assert error["resumable"] is False
    assert error["resume_status"] == "pending_child_cleanup"
    assert error["tool_call_id"] == "call-timeout"
    assert error["parallel_group_id"] == "parallel-timeout"


@pytest.mark.asyncio
async def test_resume_timeout_reports_pending_cleanup_error_without_completed_event():
    resume_fn = AsyncMock(side_effect=_never_finishes)
    hooks = _hooks()
    tool = _make_tool(timeout=0.01, resume_fn=resume_fn)
    session_id = "child-session-001_test-agent"

    result = await tool._resume_existing_session(
        session_id=session_id,
        instruction="Continue",
        hooks=hooks,
        tool_call_id="call-resume-timeout",
        parallel_group_id="parallel-resume-timeout",
    )

    assert result.success is False
    elapsed_s = _pop_elapsed(result.output)
    assert elapsed_s >= 0.01
    assert result.output == {
        "session_id": session_id,
        "agent": "test-agent",
        "status": "timeout",
        **_NO_PARTIAL_FIELDS,
        "metadata": {
            "timeout_seconds": 0.01,
            "resumable": False,
            "resume_status": "pending_child_cleanup",
            "recovery_message": (
                "Child cancellation cleanup is still in progress; do not resume "
                "this session until cleanup and persistence complete."
            ),
        },
    }

    emissions = _emissions(hooks)
    event_names = [name for name, _payload in emissions]
    assert "delegate:agent_cancelled" not in event_names
    assert "delegate:agent_completed" not in event_names
    error = next(payload for name, payload in emissions if name == "delegate:error")
    assert error["agent"] == "test-agent"
    assert error["session_id"] == session_id
    assert error["error_type"] == "delegate_timeout"
    assert error["status"] == "timeout"
    assert error["timeout_seconds"] == 0.01
    assert error["resumable"] is False
    assert error["resume_status"] == "pending_child_cleanup"
    assert error["tool_call_id"] == "call-resume-timeout"
    assert error["parallel_group_id"] == "parallel-resume-timeout"


@pytest.mark.timeout(1)
@pytest.mark.asyncio
async def test_spawn_deadline_releases_parent_when_child_suppresses_cancellation():
    child_finished = asyncio.Event()

    async def suppresses_cancellation(**_kwargs):
        try:
            await asyncio.Future()
        except asyncio.CancelledError:
            child_finished.set()
            return {"session_id": "ignored", "output": "ignored"}

    hooks = _hooks()
    tool = _make_tool(timeout=0.02, spawn_fn=suppresses_cancellation)
    started_at = asyncio.get_running_loop().time()

    result = await _spawn(tool, hooks)
    elapsed = asyncio.get_running_loop().time() - started_at

    assert result.output is not None
    assert result.output["status"] == "timeout"
    assert 0.01 <= elapsed < 0.12
    await asyncio.wait_for(child_finished.wait(), timeout=0.5)


@pytest.mark.timeout(1)
@pytest.mark.asyncio
async def test_detached_child_registry_survives_gc_and_cleans_up():
    release_child = asyncio.Event()
    cancellation_suppressed = asyncio.Event()
    loop_errors: list[dict] = []
    loop = asyncio.get_running_loop()
    previous_handler = loop.get_exception_handler()
    loop.set_exception_handler(lambda _loop, context: loop_errors.append(context))

    async def remains_pending_after_cancellation(**_kwargs):
        try:
            await asyncio.Future()
        except asyncio.CancelledError:
            cancellation_suppressed.set()
            await release_child.wait()
            return {"session_id": "ignored", "output": "ignored"}

    try:
        hooks = _hooks()
        tool = _make_tool(timeout=0.02, spawn_fn=remains_pending_after_cancellation)

        result = await _spawn(tool, hooks)
        await asyncio.wait_for(cancellation_suppressed.wait(), timeout=0.5)

        assert result.output is not None
        assert result.output["status"] == "timeout"
        assert len(tool._detached_child_tasks) == 1

        gc.collect()
        await asyncio.sleep(0)
        assert not any(
            context.get("message") == "Task was destroyed but it is pending!"
            for context in loop_errors
        )
        assert len(tool._detached_child_tasks) == 1

        release_child.set()
        for _ in range(10):
            if not tool._detached_child_tasks:
                break
            await asyncio.sleep(0)
        assert not tool._detached_child_tasks
    finally:
        release_child.set()
        await asyncio.sleep(0)
        loop.set_exception_handler(previous_handler)


@pytest.mark.timeout(1)
@pytest.mark.asyncio
async def test_timeout_consumes_late_child_exception(caplog):
    child_finished = asyncio.Event()

    async def raises_after_cancellation(**_kwargs):
        try:
            await asyncio.Future()
        except asyncio.CancelledError:
            child_finished.set()
            raise RuntimeError("late child failure")

    caplog.set_level(logging.ERROR, logger="asyncio")
    hooks = _hooks()
    tool = _make_tool(timeout=0.02, spawn_fn=raises_after_cancellation)

    result = await _spawn(tool, hooks)

    assert result.output is not None
    assert result.output["status"] == "timeout"
    await asyncio.wait_for(child_finished.wait(), timeout=0.5)
    await asyncio.sleep(0)
    assert "Task exception was never retrieved" not in caplog.text


@pytest.mark.timeout(1)
@pytest.mark.asyncio
async def test_resume_deadline_releases_parent_during_slow_cancellation_cleanup():
    cleanup_started = asyncio.Event()
    cleanup_finished = asyncio.Event()

    async def slow_cancellation_cleanup(**_kwargs):
        try:
            await asyncio.Future()
        except asyncio.CancelledError:
            cleanup_started.set()
            await asyncio.sleep(0.2)
            cleanup_finished.set()
            return {"session_id": "ignored", "output": "ignored"}

    hooks = _hooks()
    tool = _make_tool(timeout=0.02, resume_fn=slow_cancellation_cleanup)
    started_at = asyncio.get_running_loop().time()

    result = await _resume(tool, hooks)
    elapsed = asyncio.get_running_loop().time() - started_at

    assert result.output is not None
    assert result.output["status"] == "timeout"
    assert 0.01 <= elapsed < 0.12
    await asyncio.wait_for(cleanup_started.wait(), timeout=0.5)
    await asyncio.wait_for(cleanup_finished.wait(), timeout=0.5)


@pytest.mark.timeout(1)
@pytest.mark.asyncio
async def test_external_parent_cancellation_reraises_while_cancelling_child():
    child_started = asyncio.Event()
    child_cancelled = asyncio.Event()

    async def pending_child(**_kwargs):
        child_started.set()
        try:
            await asyncio.Future()
        except asyncio.CancelledError:
            child_cancelled.set()
            raise

    hooks = _hooks()
    tool = _make_tool(timeout=1, spawn_fn=pending_child)
    parent_task = asyncio.create_task(_spawn(tool, hooks))
    await asyncio.wait_for(child_started.wait(), timeout=0.5)

    parent_task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await parent_task

    await asyncio.wait_for(child_cancelled.wait(), timeout=0.5)
    event_names = [name for name, _payload in _emissions(hooks)]
    assert "delegate:agent_cancelled" in event_names
    assert "delegate:agent_completed" not in event_names


@pytest.mark.parametrize("timeout", [None, 0.01], ids=["disabled", "enabled"])
@pytest.mark.asyncio
async def test_spawn_capability_timeout_error_uses_ordinary_error_handling(timeout):
    hooks = _hooks()
    tool = _make_tool(
        timeout=timeout,
        spawn_fn=AsyncMock(side_effect=_capability_times_out),
    )

    result = await _spawn(tool, hooks)

    expected_message = "Agent delegation failed (TimeoutError): capability timeout"
    assert result.success is False
    assert result.output == expected_message
    assert result.error == {"message": expected_message}

    emissions = _emissions(hooks)
    event_names = [name for name, _payload in emissions]
    assert "delegate:error" in event_names
    assert "delegate:agent_completed" not in event_names
    assert "delegate:agent_cancelled" not in event_names
    assert all(payload.get("status") != "timeout" for _name, payload in emissions)
    error_payload = next(
        payload for name, payload in emissions if name == "delegate:error"
    )
    assert error_payload["error"] == expected_message


@pytest.mark.parametrize("timeout", [None, 0.01], ids=["disabled", "enabled"])
@pytest.mark.asyncio
async def test_resume_capability_timeout_error_uses_ordinary_error_handling(timeout):
    hooks = _hooks()
    tool = _make_tool(
        timeout=timeout,
        resume_fn=AsyncMock(side_effect=_capability_times_out),
    )

    result = await tool._resume_existing_session(
        session_id="child-session-001_test-agent",
        instruction="Continue",
        hooks=hooks,
        tool_call_id="call-capability-timeout",
        parallel_group_id="parallel-capability-timeout",
    )

    expected_message = "Agent resume failed (TimeoutError): capability timeout"
    assert result.success is False
    assert result.output == expected_message
    assert result.error == {"message": expected_message}

    emissions = _emissions(hooks)
    event_names = [name for name, _payload in emissions]
    assert "delegate:error" in event_names
    assert "delegate:agent_completed" not in event_names
    assert "delegate:agent_cancelled" not in event_names
    assert all(payload.get("status") != "timeout" for _name, payload in emissions)
    error_payload = next(
        payload for name, payload in emissions if name == "delegate:error"
    )
    assert error_payload["error"] == expected_message


@pytest.mark.asyncio
async def test_spawn_cancellation_emits_cancelled_and_reraises():
    hooks = _hooks()
    tool = _make_tool(timeout=0.01, spawn_fn=AsyncMock(side_effect=_is_cancelled))

    with pytest.raises(asyncio.CancelledError):
        await _spawn(tool, hooks)

    event_names = [name for name, _payload in _emissions(hooks)]
    assert "delegate:agent_cancelled" in event_names
    assert "delegate:agent_completed" not in event_names


@pytest.mark.asyncio
async def test_resume_cancellation_emits_cancelled_and_reraises():
    hooks = _hooks()
    tool = _make_tool(timeout=0.01, resume_fn=AsyncMock(side_effect=_is_cancelled))

    with pytest.raises(asyncio.CancelledError):
        await tool._resume_existing_session(
            session_id="child-session-001_test-agent",
            instruction="Continue",
            hooks=hooks,
            tool_call_id="call-cancelled",
            parallel_group_id="parallel-cancelled",
        )

    event_names = [name for name, _payload in _emissions(hooks)]
    assert "delegate:agent_cancelled" in event_names
    assert "delegate:agent_completed" not in event_names
