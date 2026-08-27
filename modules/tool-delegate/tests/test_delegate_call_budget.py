"""Tests for the Layer 1 LLM-call budget (spec: 298-replacement, §3, §11 T2).

Covers, per the implementation spec's test plan (T2 series):
  T2.1  Default injection -- settings.max_llm_calls flows into
        orchestrator_config["max_iterations"]
  T2.2  Per-call override -- input["max_llm_calls"] wins
  T2.4  Precedence -- per-call beats the module setting
  T2.5  Opt-out -- max_llm_calls: 0 -> key absent from orchestrator_config
  T2.6  Parent config preserved -- other keys survive, parent dict untouched
  T2.7  Validation -- bad values raise at construction, not at spawn
  T2.8  Status passthrough -- budget_exhausted status forwarded verbatim
  T2.9  Metadata passthrough -- forwarded, plus budget_enforced when relevant
  T2.10 Negotiated-feature warning -- missing llm_call_budget telemetry
  T2.11 Resume carries no orchestrator_config

Plus the frontmatter round-trip verification the spec's open item #1
requires before shipping a per-agent override (it does not round-trip --
see test_agent_frontmatter_budget_key_is_dropped below), and a "ships
dark" regression proving default behavior is unchanged at S0.
"""

from __future__ import annotations

import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
from amplifier_module_tool_delegate import DelegateTool, _validate_call_budget

# ---------------------------------------------------------------------------
# Helpers (pattern mirrors tests/test_delegate_spawn_new_session.py)
# ---------------------------------------------------------------------------


def _make_tool(
    *,
    settings: dict | None = None,
    orchestrator_value: dict | str | None = None,
    spawn_result: dict | None = None,
) -> tuple[DelegateTool, AsyncMock]:
    """Create a DelegateTool wired for _spawn_new_session()-level tests.

    Returns (tool, spawn_fn) so tests can inspect spawn_fn.call_args.
    """
    result = spawn_result or {
        "output": "done",
        "session_id": "child-001",
        "status": "success",
        "turn_count": 1,
        "metadata": {},
    }
    spawn_fn = AsyncMock(return_value=result)

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
    parent_session.config = {
        "session": {
            "orchestrator": (
                orchestrator_value if orchestrator_value is not None else {}
            )
        }
    }
    coordinator.session = parent_session

    config: dict = {"features": {}, "settings": settings or {"exclude_tools": []}}
    tool = DelegateTool(coordinator, config)
    return tool, spawn_fn


# ---------------------------------------------------------------------------
# T2.1 -- Default injection
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestDefaultInjection:
    async def test_settings_default_flows_to_orchestrator_config(self) -> None:
        tool, spawn_fn = _make_tool(
            settings={"exclude_tools": [], "max_llm_calls": 300}
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
        assert call_kwargs["orchestrator_config"]["max_iterations"] == 300
        assert call_kwargs["orchestrator_config"]["budget_warn_ratio"] == 0.8

    async def test_ships_dark_by_default_no_settings(self) -> None:
        """Regression: with no max_llm_calls setting at all (S0 default),
        orchestrator_config is untouched -- exactly today's behavior."""
        tool, spawn_fn = _make_tool()  # no settings.max_llm_calls

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
        # No orchestrator config was inherited and no budget was injected --
        # None, exactly like before this feature existed.
        assert call_kwargs["orchestrator_config"] is None


# ---------------------------------------------------------------------------
# T2.2 / T2.4 -- Per-call override / precedence
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestPerCallOverride:
    async def test_per_call_override_wins_over_default(self) -> None:
        """T2.2 + T2.4: input max_llm_calls=600 beats settings default 300."""
        tool, spawn_fn = _make_tool(
            settings={"exclude_tools": [], "max_llm_calls": 300}
        )

        result = await tool.execute(
            {
                "agent": "self",
                "instruction": "do something",
                "max_llm_calls": 600,
            }
        )

        assert result.success is True
        call_kwargs = spawn_fn.call_args.kwargs
        assert call_kwargs["orchestrator_config"]["max_iterations"] == 600


# ---------------------------------------------------------------------------
# T2.5 -- Opt-out
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestOptOut:
    async def test_zero_disables_budget_for_this_call(self) -> None:
        tool, spawn_fn = _make_tool(
            settings={"exclude_tools": [], "max_llm_calls": 300}
        )

        result = await tool.execute(
            {
                "agent": "self",
                "instruction": "do something",
                "max_llm_calls": 0,
            }
        )

        assert result.success is True
        call_kwargs = spawn_fn.call_args.kwargs
        # No orchestrator config inherited from parent, and 0 means "no
        # budget" -- orchestrator_config collapses back to None entirely.
        assert call_kwargs["orchestrator_config"] is None


# ---------------------------------------------------------------------------
# T2.6 -- Parent config preserved, never mutated
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestParentConfigPreserved:
    async def test_other_keys_survive_and_parent_dict_not_mutated(self) -> None:
        tool, spawn_fn = _make_tool(
            settings={"exclude_tools": [], "max_llm_calls": 300},
            orchestrator_value={
                "type": "loop-basic",
                "config": {"stream_delay": 0.05, "extended_thinking": True},
            },
        )
        parent_orch_config = tool.coordinator.session.config["session"]["orchestrator"][
            "config"
        ]
        original_parent_config = dict(parent_orch_config)

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
        sent = call_kwargs["orchestrator_config"]
        assert sent["stream_delay"] == 0.05
        assert sent["extended_thinking"] is True
        assert sent["max_iterations"] == 300
        # The parent's own config dict must be untouched (no new keys, no
        # mutation) -- _spawn_new_session copies before adding budget keys.
        assert parent_orch_config == original_parent_config
        assert "max_iterations" not in parent_orch_config


# ---------------------------------------------------------------------------
# T2.7 -- Validation at construction, not at spawn
# ---------------------------------------------------------------------------


class TestValidation:
    def test_negative_value_raises_at_construction(self) -> None:
        coordinator = MagicMock()
        with pytest.raises(ValueError, match="max_llm_calls"):
            DelegateTool(
                coordinator,
                {"features": {}, "settings": {"max_llm_calls": -1}},
            )

    def test_bool_raises_at_construction(self) -> None:
        coordinator = MagicMock()
        with pytest.raises(TypeError, match="bool"):
            DelegateTool(
                coordinator,
                {"features": {}, "settings": {"max_llm_calls": True}},
            )

    def test_string_raises_at_construction(self) -> None:
        coordinator = MagicMock()
        with pytest.raises(TypeError, match="max_llm_calls"):
            DelegateTool(
                coordinator,
                {"features": {}, "settings": {"max_llm_calls": "300"}},
            )

    def test_zero_and_none_both_collapse_to_none(self) -> None:
        assert _validate_call_budget(0) is None
        assert _validate_call_budget(None) is None
        assert _validate_call_budget(300) == 300


# ---------------------------------------------------------------------------
# T2.8 / T2.9 -- Status and metadata passthrough
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestStatusAndMetadataPassthrough:
    async def test_budget_exhausted_status_and_metadata_forwarded(self) -> None:
        spawn_result = {
            "output": "Summary: made progress, here is what remains.",
            "session_id": "child-001",
            "status": "budget_exhausted",
            "turn_count": 300,
            "metadata": {
                "llm_calls": 300,
                "llm_call_budget": 300,
                "budget_exhausted": True,
                "resumable": True,
            },
        }
        tool, _spawn_fn = _make_tool(
            settings={"exclude_tools": [], "max_llm_calls": 300},
            spawn_result=spawn_result,
        )

        result = await tool.execute(
            {"agent": "self", "instruction": "do a very large task"}
        )

        assert result.success is True
        assert result.output is not None
        assert result.output["status"] == "budget_exhausted"
        assert result.output["metadata"]["llm_calls"] == 300
        assert result.output["metadata"]["llm_call_budget"] == 300
        assert result.output["metadata"]["budget_exhausted"] is True
        assert result.output["metadata"]["resumable"] is True
        # Budget was requested and the child reported llm_call_budget --
        # negotiated feature confirmed active.
        assert result.output["metadata"]["budget_enforced"] is True


# ---------------------------------------------------------------------------
# T2.10 -- Negotiated-feature warning
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestNegotiatedFeatureWarning:
    async def test_missing_budget_telemetry_sets_budget_enforced_false(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        # Child's orchestrator doesn't implement max_iterations at all --
        # its metadata carries no llm_call_budget key.
        spawn_result = {
            "output": "done",
            "session_id": "child-001",
            "status": "success",
            "turn_count": 1,
            "metadata": {},
        }
        tool, _spawn_fn = _make_tool(
            settings={"exclude_tools": [], "max_llm_calls": 300},
            spawn_result=spawn_result,
        )

        with caplog.at_level("WARNING"):
            result = await tool.execute(
                {"agent": "self", "instruction": "do something"}
            )

        assert result.success is True
        assert result.output is not None
        assert result.output["metadata"]["budget_enforced"] is False
        assert any(
            "Layer 1 bounding is NOT active" in record.message
            for record in caplog.records
        )

    async def test_budget_enforced_absent_when_no_budget_requested(self) -> None:
        """When no budget was ever requested (ships dark, S0), the
        budget_enforced key must not appear at all -- it's not a relevant
        concept for a delegation with no Layer 1 budget."""
        tool, _spawn_fn = _make_tool()  # no settings.max_llm_calls

        result = await tool.execute({"agent": "self", "instruction": "do something"})

        assert result.success is True
        assert result.output is not None
        assert "budget_enforced" not in result.output["metadata"]


# ---------------------------------------------------------------------------
# T2.11 -- Resume carries no orchestrator_config
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestResumeNoOrchestratorConfig:
    async def test_resume_passes_no_orchestrator_config_kwarg(self) -> None:
        resume_fn = AsyncMock(
            return_value={
                "output": "continued",
                "session_id": "child-001",
                "status": "success",
                "turn_count": 2,
                "metadata": {},
            }
        )
        coordinator = MagicMock()
        coordinator.session_id = "parent-session-123"
        coordinator.get_capability = lambda name: (
            resume_fn if name == "session.resume" else None
        )
        coordinator.get = MagicMock(return_value=None)

        tool = DelegateTool(
            coordinator,
            {
                "features": {},
                "settings": {"exclude_tools": [], "max_llm_calls": 300},
            },
        )

        result = await tool.execute(
            {
                "session_id": "abc123-def456_self",
                "instruction": "continue please",
            }
        )

        assert result.success is True
        # Resume's own signature has no orchestrator_config parameter at
        # all -- the stored config (from the original spawn) already
        # carries whatever budget was set. Confirm no such kwarg leaked in.
        assert "orchestrator_config" not in resume_fn.call_args.kwargs


# ---------------------------------------------------------------------------
# Frontmatter round-trip verification (spec §16 open item #1)
# ---------------------------------------------------------------------------


class TestAgentFrontmatterBudgetGap:
    def test_agent_frontmatter_budget_key_is_dropped(self) -> None:
        """Verifies (does not merely assert from reading the source) that a
        top-level `budget:` block in an agent .md file's frontmatter does
        NOT survive into the dict `_load_agent_file_metadata` returns.

        This is the empirical gate the spec's open item #1 requires before
        wiring precedence rank 2 (per-agent frontmatter override). It does
        NOT round-trip today -- `_load_agent_file_metadata` only forwards a
        fixed allowlist of top-level frontmatter keys (tools, providers,
        hooks, session, provider_preferences, model_role, agents); `budget`
        is not among them. Per the owner's decision, rank 2 is therefore
        NOT implemented in this PR -- see the module README's "Known gaps"
        section. `model_role` is asserted as a present-and-working control
        to prove this isn't a wholesale frontmatter-loading failure.

        If this test starts failing (i.e. `budget` starts surviving), that
        is the signal precedence rank 2 can finally be wired -- update this
        test and the delegate's `_resolve_call_budget` together.
        """
        from amplifier_foundation.bundle._dataclass import _load_agent_file_metadata

        content = """---
meta:
  name: test-agent
  description: "A test agent"
model_role: research
budget:
  max_llm_calls: 500
---

Test instruction body.
"""
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "test-agent.md"
            path.write_text(content, encoding="utf-8")
            result = _load_agent_file_metadata(path, "test-agent")

        assert "budget" not in result, (
            "budget now survives agent-frontmatter loading -- precedence "
            "rank 2 (per-agent override) can be wired; update "
            "_resolve_call_budget and this test together."
        )
        # Control: model_role (an allowlisted key) DOES survive, proving
        # this isn't a general frontmatter-loading failure.
        assert result.get("model_role") == "research"
