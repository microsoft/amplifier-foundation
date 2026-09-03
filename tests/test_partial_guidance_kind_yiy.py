"""Pins the answer to model_performance-yiy.

THE DEFECT. ``modules/tool-delegate`` picked its timeout guidance string from
``bool(text)`` alone. Both kinds of recovered partial therefore got the same
sentence:

    "INCOMPLETE: ... The text in 'partial_response' is unfinished work salvaged
     from the agent mid-flight -- it has NOT been checked, concluded, or
     self-reviewed by that agent."

That is true of unfinished assistant prose. It is NOT true of raw private
reasoning. Prose is at least addressed to a reader; reasoning never was.
Framing an agent's own reasoning as "unfinished work ... not self-reviewed"
invites the caller to read it as a draft answer -- which is precisely what it
is not.

WHY THIS BECAME REACHABLE. app-cli ``8c83a9b`` (PR #298) widened its partial
accumulator: when a timed-out delegate emitted no assistant text at all, it now
recovers the agent's ``thinking`` and ``tool_call`` trace instead. Measured on
k64's 18 legs the recoverable window went 0.05% -> 82.2% of a leg. Before that
change this case could not occur; after it, it is the common one.

THE FIELD TO SELECT ON. The producer distinguishes the two kinds for us:
``source`` is ``"spawn-accumulator:reasoning"`` for the reasoning channel and
``"spawn-accumulator"`` for a text partial. The consumer branches on that --
never on the prose, which is exactly what the field exists to avoid.

WHAT THESE TESTS PIN, and why each matters:

  1. Reasoning kind gets a guidance string that describes what it actually is,
     and does NOT carry the prose sentence. (Fails on the parent commit.)
  2. The TEXT case is BYTE-IDENTICAL to before. The literal is spelled out
     here rather than compared to the module constant, so a reword of the
     constant fails this test instead of silently redefining "unchanged".
     app-cli's round-trip test ``test_guidance_string_is_unchanged_for_the_text_case``
     asserts the same bytes from the producer side.
  3. The no-partial case is unchanged, pinned the same literal way.
  4. An unknown, absent, or non-string ``source`` must not crash and must not
     silently receive the reasoning frame -- it degrades to the pre-existing
     behaviour. The producer is a different repo on its own release cadence;
     a value this code has never seen has to be safe.

These tests live under ``tests/`` rather than ``modules/tool-delegate/tests/``
deliberately: CI runs ``pytest tests/`` only, so a test placed only in the
module directory would never run there.
"""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock
from unittest.mock import MagicMock

import pytest
from amplifier_module_tool_delegate import DelegateTool

# ---------------------------------------------------------------------------
# The two guidance strings that must not move, quoted here as literals.
#
# Copying them rather than importing the constants is the point: importing
# would make the assertion tautological, and "byte-identical to today" is only
# a real pin if today's bytes are written down somewhere a reword must edit.
# ---------------------------------------------------------------------------

TEXT_GUIDANCE_AS_SHIPPED = (
    "INCOMPLETE: this delegate did not finish. The text in 'partial_response' "
    "is unfinished work salvaged from the agent mid-flight -- it has NOT been "
    "checked, concluded, or self-reviewed by that agent. Do not report it as a "
    "completed result and do not treat its conclusions as final. Re-delegate a "
    "narrower task or complete the work yourself; see metadata.recovery_message "
    "before considering this session for resumption."
)

NO_PARTIAL_GUIDANCE_AS_SHIPPED = (
    "INCOMPLETE: this delegate did not finish and no partial output could be "
    "recovered. Nothing here is a result. Re-delegate a narrower task or "
    "complete the work yourself; see metadata.recovery_message before "
    "considering this session for resumption."
)

#: The producer's own values, verbatim (app-cli ``8c83a9b``,
#: ``amplifier_app_cli/session_spawner.py::get_partial_output``).
REASONING_SOURCE = "spawn-accumulator:reasoning"
TEXT_SOURCE = "spawn-accumulator"

#: The sentence that must never appear over recovered reasoning. It is the
#: whole defect in one clause.
PROSE_CLAUSE = "unfinished work salvaged from the agent mid-flight"

_ABSENT = object()


# ---------------------------------------------------------------------------
# Harness: a DelegateTool over a fake coordinator whose spawn never finishes,
# so the real timeout path runs. Mirrors
# ``modules/tool-delegate/tests/test_delegate_timeout_partial.py`` so both
# files exercise the same code, not a reimplementation of it.
# ---------------------------------------------------------------------------


def _make_tool(*, timeout: float, partial_fn: Any = _ABSENT) -> DelegateTool:
    coordinator = MagicMock()
    coordinator.session_id = "parent-session-yiy"
    coordinator.config = {"agents": {"test-agent": {}}}
    coordinator.session_state = {}
    coordinator._tool_dispatch_context = {}
    coordinator._tool_dispatch_contexts = {}

    capabilities: dict[str, Any] = {
        "session.spawn": AsyncMock(side_effect=_never_finishes),
        "session.resume": AsyncMock(side_effect=_never_finishes),
        "self_delegation_depth": 0,
    }
    if partial_fn is not _ABSENT:
        capabilities["session.partial"] = partial_fn
    coordinator.get_capability = lambda name: capabilities.get(name)
    coordinator.get = MagicMock(return_value=None)

    parent_session = MagicMock()
    parent_session.config = {"session": {"orchestrator": {}}}
    coordinator.session = parent_session

    return DelegateTool(
        coordinator,
        {"features": {}, "settings": {"exclude_tools": [], "timeout": timeout}},
    )


async def _never_finishes(**_kwargs):
    await asyncio.Future()


def _hooks() -> MagicMock:
    hooks = MagicMock()
    hooks.emit = AsyncMock()
    return hooks


async def _spawn(tool: DelegateTool) -> Any:
    return await tool._spawn_new_session(
        agent_name="test-agent",
        instruction="Do something",
        context_depth="none",
        context_scope="conversation",
        context_turns=5,
        provider_preferences=None,
        hooks=_hooks(),
        tool_call_id="call-yiy",
        parallel_group_id="parallel-yiy",
    )


async def _resume(tool: DelegateTool) -> Any:
    return await tool._resume_existing_session(
        session_id="child-session-yiy_test-agent",
        instruction="Continue",
        hooks=_hooks(),
        tool_call_id="call-yiy-resume",
        parallel_group_id="parallel-yiy-resume",
    )


def _partial(text: str, source: Any = _ABSENT) -> Any:
    payload: dict[str, Any] = {"text": text, "segments": 2}
    if source is not _ABSENT:
        payload["source"] = source
    return lambda _sid: payload


# ---------------------------------------------------------------------------
# 1. The defect: reasoning must not be framed as unfinished prose.
#    THIS IS THE FAIL-BEFORE TEST.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_reasoning_partial_is_not_framed_as_unfinished_prose():
    """FAILS on the parent commit (5d8db2f), passes after.

    On the parent, guidance is selected by ``bool(text)`` alone, so a
    reasoning partial receives ``_PARTIAL_GUIDANCE`` verbatim -- including
    the "unfinished work ... not been checked, concluded, or self-reviewed"
    clause, which is false of raw reasoning.
    """
    tool = _make_tool(
        timeout=0.01,
        partial_fn=_partial("I should check the config first...", REASONING_SOURCE),
    )
    result = await _spawn(tool)

    guidance = result.output["guidance"]
    assert result.output["partial_source"] == REASONING_SOURCE
    assert PROSE_CLAUSE not in guidance
    assert "self-reviewed" not in guidance
    assert guidance != TEXT_GUIDANCE_AS_SHIPPED


@pytest.mark.asyncio
async def test_reasoning_guidance_says_what_the_payload_actually_is():
    """It is evidence of what the agent was doing -- not a draft answer."""
    tool = _make_tool(
        timeout=0.01,
        partial_fn=_partial("thinking, then a tool call", REASONING_SOURCE),
    )
    guidance = (await _spawn(tool)).output["guidance"]

    lowered = guidance.lower()
    assert "reasoning" in lowered
    assert "tool call" in lowered or "tool-call" in lowered
    # It still has to say, unmistakably, that the delegate did not finish.
    assert guidance.startswith("INCOMPLETE:")
    # And it must not invite the caller to treat reasoning as an answer.
    assert "not a" in lowered or "never" in lowered


@pytest.mark.asyncio
async def test_reasoning_kind_is_honoured_on_the_resume_path_too():
    """The resume path is a second timeout call site; it must not diverge."""
    tool = _make_tool(
        timeout=0.01,
        partial_fn=_partial("resumed reasoning trace", REASONING_SOURCE),
    )
    result = await _resume(tool)

    assert result.output["partial_source"] == REASONING_SOURCE
    assert PROSE_CLAUSE not in result.output["guidance"]


# ---------------------------------------------------------------------------
# 2. The text case is byte-identical to today.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_text_partial_guidance_is_byte_identical_to_today():
    tool = _make_tool(
        timeout=0.01,
        partial_fn=_partial("half an answer", TEXT_SOURCE),
    )
    result = await _spawn(tool)

    assert result.output["partial_source"] == TEXT_SOURCE
    assert result.output["guidance"] == TEXT_GUIDANCE_AS_SHIPPED


@pytest.mark.asyncio
async def test_text_partial_guidance_is_byte_identical_on_resume_too():
    tool = _make_tool(
        timeout=0.01,
        partial_fn=_partial("half an answer", TEXT_SOURCE),
    )
    result = await _resume(tool)

    assert result.output["guidance"] == TEXT_GUIDANCE_AS_SHIPPED


# ---------------------------------------------------------------------------
# 3. The no-partial case is unchanged.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_no_partial_guidance_is_byte_identical_to_today():
    tool = _make_tool(timeout=0.01)  # no session.partial capability at all
    result = await _spawn(tool)

    assert result.output["partial_available"] is False
    assert result.output["partial_source"] == "none"
    assert result.output["guidance"] == NO_PARTIAL_GUIDANCE_AS_SHIPPED


@pytest.mark.asyncio
async def test_empty_text_with_a_reasoning_source_still_reads_as_no_partial():
    """No recovered characters means nothing was recovered, whatever the kind.

    ``partial_available`` is False here, so the reasoning frame would be
    describing an empty payload. The no-partial guidance is the honest one.
    """
    tool = _make_tool(timeout=0.01, partial_fn=_partial("", REASONING_SOURCE))
    result = await _spawn(tool)

    assert result.output["partial_available"] is False
    assert result.output["guidance"] == NO_PARTIAL_GUIDANCE_AS_SHIPPED


# ---------------------------------------------------------------------------
# 4. An unrecognised source degrades; it never crashes and never gets the
#    reasoning frame by accident.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "source",
    [
        "capability",
        "store",
        "spawn-accumulator:something-invented-later",
        "SPAWN-ACCUMULATOR:REASONING",  # case matters; this is not the value
        "spawn-accumulator:reasoning ",  # trailing space; not the value either
        "",
        None,
        123,
        ["spawn-accumulator:reasoning"],
        {"kind": "reasoning"},
    ],
)
async def test_unknown_source_degrades_to_the_text_guidance(source: Any):
    """A value this repo has never seen must be safe and must not be reasoning.

    The producer ships on its own cadence. Selecting by exact match on the one
    value it documents means an unrecognised value falls back to the
    pre-existing behaviour rather than inheriting a frame that may be wrong
    for it.
    """
    tool = _make_tool(timeout=0.01, partial_fn=_partial("some recovered text", source))
    result = await _spawn(tool)

    assert result.success is False
    assert result.output["status"] == "timeout"
    assert result.output["partial_available"] is True
    assert result.output["guidance"] == TEXT_GUIDANCE_AS_SHIPPED


@pytest.mark.asyncio
async def test_absent_source_key_degrades_to_the_text_guidance():
    """The payload need not carry ``source`` at all."""
    tool = _make_tool(timeout=0.01, partial_fn=_partial("some recovered text"))
    result = await _spawn(tool)

    assert result.output["guidance"] == TEXT_GUIDANCE_AS_SHIPPED


@pytest.mark.asyncio
async def test_the_other_partial_fields_are_untouched_for_the_reasoning_kind():
    """Only the guidance frame changes. The payload contract does not move."""
    tool = _make_tool(
        timeout=0.01,
        partial_fn=_partial("reasoning text", REASONING_SOURCE),
    )
    result = await _spawn(tool)

    assert "response" not in result.output
    assert result.output["completed"] is False
    assert result.output["status"] == "timeout"
    assert result.output["partial_available"] is True
    assert result.output["partial_response"] == "reasoning text"
    assert result.output["partial_segments"] == 2
    assert result.output["partial_truncated"] is False
    assert result.output["partial_chars_total"] == len("reasoning text")
