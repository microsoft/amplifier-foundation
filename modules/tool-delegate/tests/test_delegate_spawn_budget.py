"""Tests for the SPAWN-BUDGET treatment (LOCAL PATCH, not upstream).

Covers the test plan from the treatment's own construction task:
  B1  Default (unset) -> unlimited, zero behavior change vs. today
  B2  budget=3 -> 3 spawns succeed with footers; 4th is rejected and
      spawns nothing
  B3  Resume calls never decrement the budget
  B4  String "3" coerces to int 3
  B5  Garbage value warns and falls back to unlimited
  B6  The description gets exactly one factual budget line, iff configured

Mirrors the helper pattern established in test_delegate_call_budget.py.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from amplifier_module_tool_delegate import DelegateTool, _parse_spawn_budget

# ---------------------------------------------------------------------------
# Helpers (pattern mirrors tests/test_delegate_call_budget.py)
# ---------------------------------------------------------------------------


def _make_tool(
    *,
    settings: dict | None = None,
    spawn_result: dict | None = None,
    resume_result: dict | None = None,
) -> tuple[DelegateTool, AsyncMock, AsyncMock]:
    """Create a DelegateTool wired for execute()-level spawn/resume tests.

    Returns (tool, spawn_fn, resume_fn) so tests can inspect call counts.
    Each call to spawn_fn returns a FRESH copy of spawn_result so mutating
    one call's dict (e.g. via output_metadata = dict(result_metadata))
    never leaks into another call's assertions.
    """
    default_spawn_result = {
        "output": "done",
        "session_id": "child-001",
        "status": "success",
        "turn_count": 1,
        "metadata": {},
    }
    base_spawn_result = spawn_result or default_spawn_result

    async def _spawn_side_effect(*_args, **_kwargs):
        return dict(base_spawn_result)

    spawn_fn = AsyncMock(side_effect=_spawn_side_effect)

    default_resume_result = {
        "output": "continued",
        "session_id": "child-001",
        "status": "success",
        "turn_count": 2,
        "metadata": {},
    }
    resume_fn = AsyncMock(return_value=resume_result or default_resume_result)

    coordinator = MagicMock()
    coordinator.session_id = "parent-session-123"
    coordinator.config = {"agents": {}}
    coordinator.session_state = {}
    coordinator._tool_dispatch_context = {}

    def _get_capability(name: str):
        if name == "session.spawn":
            return spawn_fn
        if name == "session.resume":
            return resume_fn
        return None

    coordinator.get_capability = _get_capability
    coordinator.get = MagicMock(return_value=None)

    parent_session = MagicMock()
    parent_session.session_id = "parent-session-123"
    parent_session.config = {"session": {"orchestrator": {}}}
    coordinator.session = parent_session

    config: dict = {"features": {}, "settings": settings or {"exclude_tools": []}}
    tool = DelegateTool(coordinator, config)
    return tool, spawn_fn, resume_fn


# ---------------------------------------------------------------------------
# B1 -- Default (unset) -> unlimited, zero behavior change
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestDefaultUnlimited:
    async def test_default_is_none(self) -> None:
        tool, _spawn_fn, _resume_fn = _make_tool()
        assert tool.max_spawns_per_session is None

    async def test_no_footer_on_response_when_unlimited(self) -> None:
        tool, _spawn_fn, _resume_fn = _make_tool()
        result = await tool.execute({"agent": "self", "instruction": "do it"})
        assert result.success is True
        assert result.output is not None
        assert "new-session budget" not in result.output["response"]

    async def test_no_description_line_when_unlimited(self) -> None:
        tool, _spawn_fn, _resume_fn = _make_tool()
        assert "budget of" not in tool.description
        assert "new agent sessions" not in tool.description

    async def test_many_spawns_never_rejected(self) -> None:
        """Zero behavior change: an unset budget never rejects a spawn,
        no matter how many new sessions this tool instance has spawned."""
        tool, spawn_fn, _resume_fn = _make_tool()
        for _ in range(10):
            result = await tool.execute({"agent": "self", "instruction": "go"})
            assert result.success is True
        assert spawn_fn.call_count == 10


# ---------------------------------------------------------------------------
# B2 -- budget=3: 3 succeed with footers, 4th rejected, spawns nothing
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestBudgetEnforcement:
    async def test_three_succeed_with_footers_fourth_rejected(self) -> None:
        tool, spawn_fn, _resume_fn = _make_tool(
            settings={"exclude_tools": [], "max_spawns_per_session": 3}
        )

        expected_footers = [
            "(new-session budget: 2 of 3 remaining)",
            "(new-session budget: 1 of 3 remaining)",
            "(new-session budget: 0 of 3 remaining)",
        ]
        for expected_footer in expected_footers:
            result = await tool.execute({"agent": "self", "instruction": "go"})
            assert result.success is True
            assert result.output is not None
            assert expected_footer in result.output["response"]

        assert spawn_fn.call_count == 3
        assert tool._new_spawns_used == 3

        # 4th call: rejected, and spawn_fn must NOT be called at all.
        result = await tool.execute({"agent": "self", "instruction": "go"})
        assert result.success is False
        assert result.error is not None
        assert (
            "Delegation budget reached (3/3 new agent sessions used)"
            in (result.error["message"])
        )
        assert result.error["code"] == "SPAWN_BUDGET_EXCEEDED"
        assert spawn_fn.call_count == 3  # unchanged -- nothing was spawned
        assert tool._new_spawns_used == 3  # unchanged -- rejection doesn't count

    async def test_rejection_message_is_not_scolding_and_mentions_resume(
        self,
    ) -> None:
        tool, _spawn_fn, _resume_fn = _make_tool(
            settings={"exclude_tools": [], "max_spawns_per_session": 0}
        )
        result = await tool.execute({"agent": "self", "instruction": "go"})
        assert result.success is False
        assert result.error is not None
        message = result.error["message"]
        assert len(message.split()) <= 60
        assert "session_id=" in message
        assert "filesystem" in message


# ---------------------------------------------------------------------------
# B3 -- Resume calls never decrement the budget
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestResumeDoesNotCountAgainstBudget:
    async def test_resume_does_not_consume_a_spawn_slot(self) -> None:
        tool, spawn_fn, resume_fn = _make_tool(
            settings={"exclude_tools": [], "max_spawns_per_session": 1}
        )

        # Resume an "existing" session several times -- none of this should
        # touch the new-spawn counter or ever be rejected by the budget.
        resume_results = []
        for _ in range(5):
            resume_result = await tool.execute(
                {
                    "session_id": "abc123-def456_self",
                    "instruction": "continue please",
                }
            )
            assert resume_result.success is True
            resume_results.append(resume_result)

        assert resume_fn.call_count == 5
        assert tool._new_spawns_used == 0
        # No footer on resume responses -- footer is a spawn-only feature.
        last_resume = resume_results[-1]
        assert last_resume.output is not None
        assert "new-session budget" not in last_resume.output["response"]

        # The budget is still fully available for a genuine new spawn.
        spawn_result = await tool.execute({"agent": "self", "instruction": "go"})
        assert spawn_result.success is True
        assert spawn_fn.call_count == 1
        assert tool._new_spawns_used == 1

        # And now it's exhausted for a second new spawn.
        second_spawn = await tool.execute({"agent": "self", "instruction": "go"})
        assert second_spawn.success is False
        assert second_spawn.error["code"] == "SPAWN_BUDGET_EXCEEDED"
        assert spawn_fn.call_count == 1  # still just the one real spawn


# ---------------------------------------------------------------------------
# B4 / B5 -- Defensive parsing: string coercion, garbage -> warn + unlimited
# ---------------------------------------------------------------------------


class TestDefensiveParsing:
    def test_string_int_coerces(self) -> None:
        assert _parse_spawn_budget("3") == 3
        assert _parse_spawn_budget(" 3 ") == 3

    def test_none_is_unlimited(self) -> None:
        assert _parse_spawn_budget(None) is None

    def test_int_passes_through(self) -> None:
        assert _parse_spawn_budget(3) == 3
        assert _parse_spawn_budget(0) == 0

    def test_garbage_string_warns_and_falls_back_to_unlimited(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        with caplog.at_level("WARNING"):
            result = _parse_spawn_budget("lots")
        assert result is None
        assert any(
            "max_spawns_per_session" in record.message for record in caplog.records
        )

    def test_negative_warns_and_falls_back_to_unlimited(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        with caplog.at_level("WARNING"):
            result = _parse_spawn_budget(-1)
        assert result is None
        assert any("max_spawns_per_session" in r.message for r in caplog.records)

    def test_bool_warns_and_falls_back_to_unlimited(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        with caplog.at_level("WARNING"):
            result = _parse_spawn_budget(True)
        assert result is None
        assert any("max_spawns_per_session" in r.message for r in caplog.records)

    def test_float_warns_and_falls_back_to_unlimited(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        with caplog.at_level("WARNING"):
            result = _parse_spawn_budget(3.5)
        assert result is None
        assert any("max_spawns_per_session" in r.message for r in caplog.records)

    async def _unused(self) -> None:  # pragma: no cover
        """Keeps this class importable if a runner assumes async collection."""


@pytest.mark.asyncio
class TestGarbageBudgetEndToEnd:
    async def test_garbage_config_value_degrades_to_unlimited_behavior(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """A misconfigured max_spawns_per_session must not crash tool
        construction, must not silently block every spawn, and must warn."""
        with caplog.at_level("WARNING"):
            tool, spawn_fn, _resume_fn = _make_tool(
                settings={
                    "exclude_tools": [],
                    "max_spawns_per_session": "banana",
                }
            )
        assert tool.max_spawns_per_session is None
        assert any(
            "max_spawns_per_session" in record.message for record in caplog.records
        )

        for _ in range(5):
            result = await tool.execute({"agent": "self", "instruction": "go"})
            assert result.success is True
            assert "new-session budget" not in result.output["response"]
        assert spawn_fn.call_count == 5


# ---------------------------------------------------------------------------
# B6 -- Description line present iff a budget is configured
# ---------------------------------------------------------------------------


class TestDescriptionBudgetLine:
    def test_line_present_when_configured(self) -> None:
        tool, _spawn_fn, _resume_fn = _make_tool(
            settings={"exclude_tools": [], "max_spawns_per_session": 5}
        )
        assert (
            "This session has a budget of 5 new agent sessions; resuming "
            "existing sessions is unmetered." in tool.description
        )

    def test_line_absent_when_unconfigured(self) -> None:
        tool, _spawn_fn, _resume_fn = _make_tool()
        assert "This session has a budget of" not in tool.description

    def test_line_absent_when_garbage(self) -> None:
        tool, _spawn_fn, _resume_fn = _make_tool(
            settings={"exclude_tools": [], "max_spawns_per_session": "nonsense"}
        )
        assert "This session has a budget of" not in tool.description
