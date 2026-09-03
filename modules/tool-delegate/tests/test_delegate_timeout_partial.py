"""Per-delegate timeout with PARTIAL RESULTS.

The defect these tests pin down: when ``settings.timeout`` fires, the
straggler delegate's own work is discarded. There is no channel on which a
caller can see what the sub-session produced before the deadline, and no
flag telling it whether any such work exists.

The contract under test:

  1. A timeout is NEVER reportable as success -- on either channel.
  2. A timed-out delegate RETURNS (it does not raise), so sibling delegates
     running in the same parallel batch keep their completed results.
  3. Whatever the straggler produced is preserved under ``partial_response``,
     never under ``response`` (the success-only key), and its presence is
     stated by the ``partial_available`` boolean.
  4. Partial recovery is best-effort and must never raise out of the timeout
     path -- a failure there would discard the very siblings it protects.

Origin: lane 37n's ``w3-delegate-timeout/`` design, re-targeted onto
foundation main after ``14d5a52`` (wall-clock backstop, timeout RETURNS) and
``8f45ea4`` / PR #350 (resume-path routing) moved the base. See
``docs/lanes/bp0-delegate-timeout-partial-consumer/DONE-NOTE.md`` for the
per-hunk re-targeting record.
"""

from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock

import pytest
from amplifier_module_tool_delegate import DEFAULT_PARTIAL_MAX_CHARS, DelegateTool

_ABSENT = object()


def _make_tool(
    *,
    timeout: object = _ABSENT,
    spawn_fn=None,
    resume_fn=None,
    partial_fn=_ABSENT,
    settings_extra: dict | None = None,
) -> DelegateTool:
    """Build a DelegateTool over a fake coordinator.

    Mirrors ``test_delegate_timeout.py``'s harness so both files exercise the
    same code paths, plus an optional ``session.partial`` capability.
    """
    coordinator = MagicMock()
    coordinator.session_id = "parent-session-123"
    coordinator.config = {"agents": {"test-agent": {}}}
    coordinator.session_state = {}
    coordinator._tool_dispatch_context = {}
    coordinator._tool_dispatch_contexts = {}

    capabilities: dict[str, object] = {
        "session.spawn": spawn_fn or AsyncMock(),
        "session.resume": resume_fn or AsyncMock(),
        "self_delegation_depth": 0,
    }
    if partial_fn is not _ABSENT:
        capabilities["session.partial"] = partial_fn
    coordinator.get_capability = lambda name: capabilities.get(name)
    coordinator.get = MagicMock(return_value=None)

    parent_session = MagicMock()
    parent_session.config = {"session": {"orchestrator": {}}}
    coordinator.session = parent_session

    settings: dict[str, object] = {"exclude_tools": []}
    if timeout is not _ABSENT:
        settings["timeout"] = timeout
    settings.update(settings_extra or {})
    return DelegateTool(coordinator, {"features": {}, "settings": settings})


def _hooks() -> MagicMock:
    hooks = MagicMock()
    hooks.emit = AsyncMock()
    return hooks


def _emissions(hooks: MagicMock) -> list[tuple[str, dict]]:
    return [(args[0], args[1]) for args, _kwargs in hooks.emit.call_args_list]


async def _never_finishes(**_kwargs):
    await asyncio.Future()


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


# --------------------------------------------------------------------------
# 1. Never reportable as success
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_timeout_is_not_success_on_either_channel():
    tool = _make_tool(
        timeout=0.01,
        spawn_fn=AsyncMock(side_effect=_never_finishes),
        partial_fn=lambda sid: {"text": "found three anchors", "segments": 4},
    )
    result = await _spawn(tool, _hooks())

    # Channel 1: the structured flag.
    assert result.success is False
    # Channel 2: what the model actually reads.
    assert result.output["status"] == "timeout"
    assert result.output["completed"] is False
    serialized = result.get_serialized_output()
    assert '"status": "timeout"' in serialized
    assert '"completed": false' in serialized


@pytest.mark.asyncio
async def test_partial_text_never_lands_on_the_success_key():
    """``response`` is the success channel. A partial must not occupy it."""
    tool = _make_tool(
        timeout=0.01,
        spawn_fn=AsyncMock(side_effect=_never_finishes),
        partial_fn=lambda sid: {"text": "half an answer", "segments": 2},
    )
    result = await _spawn(tool, _hooks())

    assert "response" not in result.output
    assert result.output["partial_available"] is True
    assert result.output["partial_response"] == "half an answer"
    assert result.output["partial_segments"] == 2
    # A consumer keyed on the success shape finds nothing to mistake.
    assert json.loads(result.get_serialized_output()).get("response") is None


@pytest.mark.asyncio
async def test_success_result_carries_no_partial_keys():
    """The inverse guard: a completed delegate never looks partial.

    This is the pin for "default behaviour for normal completions is
    unchanged" -- the success shape gains no key from this work.
    """

    async def _completes(**_kwargs):
        return {"output": "the whole answer", "session_id": "sub-1", "turn_count": 3}

    tool = _make_tool(
        timeout=60,
        spawn_fn=AsyncMock(side_effect=_completes),
        partial_fn=lambda sid: {"text": "should never be consulted", "segments": 9},
    )
    result = await _spawn(tool, _hooks())

    assert result.success is True
    assert result.output["response"] == "the whole answer"
    assert result.output.get("status") == "success"
    assert not [k for k in result.output if k.startswith("partial")]
    assert "completed" not in result.output


# --------------------------------------------------------------------------
# 2. Siblings survive the straggler (k64 gate G-D1)
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_straggler_returns_rather_than_raises_so_siblings_survive():
    """The measured harm: a straggler discarding completed siblings' work.

    Reproduces the parallel batch shape the orchestrator uses
    (``asyncio.gather`` over per-tool coroutines, no ``return_exceptions``).
    If the timed-out delegate RAISES, gather propagates and every completed
    sibling result in the batch is discarded. It must RETURN instead.
    """

    async def _fast(**_kwargs):
        return {"output": "sibling finding", "session_id": "sub-fast", "turn_count": 1}

    fast_tool = _make_tool(timeout=60, spawn_fn=AsyncMock(side_effect=_fast))
    slow_tool = _make_tool(
        timeout=0.01,
        spawn_fn=AsyncMock(side_effect=_never_finishes),
        partial_fn=lambda sid: {"text": "straggler got this far", "segments": 7},
    )

    results = await asyncio.gather(
        _spawn(fast_tool, _hooks()),
        _spawn(fast_tool, _hooks()),
        _spawn(slow_tool, _hooks()),
    )

    # G-D1 in miniature: zero completed-delegate findings discarded.
    completed = [r for r in results if r.success]
    assert len(completed) == 2
    assert all(r.output["response"] == "sibling finding" for r in completed)

    straggler = next(r for r in results if not r.success)
    assert straggler.output["status"] == "timeout"
    assert straggler.output["partial_response"] == "straggler got this far"


# --------------------------------------------------------------------------
# 3. Partial recovery is best-effort and never fatal
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_no_partial_capability_degrades_to_no_partial_not_to_error():
    """The state until the app-cli producer half lands. Not a defect."""
    tool = _make_tool(
        timeout=0.01, spawn_fn=AsyncMock(side_effect=_never_finishes)
    )  # no session.partial
    result = await _spawn(tool, _hooks())

    assert result.success is False
    assert result.output["status"] == "timeout"
    assert result.output["partial_available"] is False
    assert result.output["partial_response"] is None
    assert result.output["partial_source"] == "none"


@pytest.mark.asyncio
async def test_partial_capability_raising_does_not_break_the_timeout_path():
    def _explodes(sub_session_id):
        raise RuntimeError("partial store unavailable")

    tool = _make_tool(
        timeout=0.01,
        spawn_fn=AsyncMock(side_effect=_never_finishes),
        partial_fn=_explodes,
    )
    result = await _spawn(tool, _hooks())

    assert result.success is False
    assert result.output["status"] == "timeout"
    assert result.output["partial_available"] is False


@pytest.mark.asyncio
async def test_async_partial_capability_is_supported():
    async def _async_partial(sub_session_id):
        return {"text": "async partial", "segments": 1, "source": "store"}

    tool = _make_tool(
        timeout=0.01,
        spawn_fn=AsyncMock(side_effect=_never_finishes),
        partial_fn=_async_partial,
    )
    result = await _spawn(tool, _hooks())

    assert result.output["partial_response"] == "async partial"
    assert result.output["partial_source"] == "store"


@pytest.mark.asyncio
async def test_partial_text_is_capped_and_keeps_the_tail():
    long_text = "x" * 500 + "THE-RECENT-TAIL"
    tool = _make_tool(
        timeout=0.01,
        spawn_fn=AsyncMock(side_effect=_never_finishes),
        partial_fn=lambda sid: {"text": long_text, "segments": 9},
        settings_extra={"partial_max_chars": 50},
    )
    result = await _spawn(tool, _hooks())

    assert result.output["partial_truncated"] is True
    assert result.output["partial_chars_total"] == len(long_text)
    assert result.output["partial_response"].endswith("THE-RECENT-TAIL")
    assert "truncated" in result.output["partial_response"]


@pytest.mark.asyncio
async def test_malformed_partial_payload_is_ignored():
    tool = _make_tool(
        timeout=0.01,
        spawn_fn=AsyncMock(side_effect=_never_finishes),
        partial_fn=lambda sid: "not a dict",
    )
    result = await _spawn(tool, _hooks())

    assert result.success is False
    assert result.output["partial_available"] is False


# --------------------------------------------------------------------------
# 4. Observability, defaults, and the resume path
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_timeout_event_carries_elapsed_and_partial_flags():
    """G-D3 depends on leg durations being emitted, not inferred."""
    hooks = _hooks()
    tool = _make_tool(
        timeout=0.01,
        spawn_fn=AsyncMock(side_effect=_never_finishes),
        partial_fn=lambda sid: {"text": "some work", "segments": 1},
    )
    await _spawn(tool, hooks)

    errors = [d for (name, d) in _emissions(hooks) if name == "delegate:error"]
    assert len(errors) == 1
    payload = errors[0]
    assert payload["error_type"] == "delegate_timeout"
    assert payload["status"] == "timeout"
    assert payload["timeout_seconds"] == 0.01
    assert payload["elapsed_s"] >= 0.01
    assert payload["partial_available"] is True
    assert payload["partial_chars"] == len("some work")


def test_partial_max_chars_default_is_shipped_not_swept():
    """The cap is a real shipped default; the *timeout* remains main's 14400.

    37n's original asserted ``tool.timeout is None``. That assertion was
    written against a base that predates ``14d5a52`` (Layer 3 wall-clock
    backstop, default 14400s). Re-asserting it here would silently revert
    that commit, so this pins only what this change actually introduces.
    ``test_delegate_timeout.py`` still owns the timeout default.
    """
    tool = _make_tool()
    assert tool.partial_max_chars == DEFAULT_PARTIAL_MAX_CHARS == 20000
    assert tool.timeout == 14400


@pytest.mark.asyncio
async def test_resume_timeout_carries_the_same_partial_contract():
    """The resume path is a second timeout call site; it must not diverge."""
    hooks = _hooks()
    tool = _make_tool(
        timeout=0.01,
        resume_fn=AsyncMock(side_effect=_never_finishes),
        partial_fn=lambda sid: {"text": "resumed partial", "segments": 3},
    )
    result = await _resume(tool, hooks)

    assert result.success is False
    assert "response" not in result.output
    assert result.output["status"] == "timeout"
    assert result.output["completed"] is False
    assert result.output["partial_available"] is True
    assert result.output["partial_response"] == "resumed partial"
    assert result.output["partial_segments"] == 3

    errors = [d for (name, d) in _emissions(hooks) if name == "delegate:error"]
    assert errors[0]["partial_available"] is True
    assert errors[0]["status"] == "timeout"
