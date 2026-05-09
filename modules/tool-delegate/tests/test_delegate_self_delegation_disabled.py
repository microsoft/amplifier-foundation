"""Tests for the `self_delegation_enabled=False` configuration path.

The DelegateTool exposes `features.self_delegation.enabled` as a runtime
config knob. Three things need to behave consistently when it's flipped to
False:

  1. execute(agent="self", ...) must hard-reject with a ToolResult error
     before any spawn attempt — preventing the runaway delegation loops
     observed when models hallucinate `agent="self"` against bundles
     whose parent has only orchestration tools.

  2. The composed `description` property (served as the LLM-facing tool
     description) must NOT advertise `agent="self"` — otherwise the
     description encourages a call the runtime will reject.

  3. The `input_schema` `agent` parameter description (served as the
     function-spec to tool-using models) must reflect the disabled state
     so the schema-only signal aligns with the description-level signal.

These tests exist because there were no tests on the disabled path — the
existing test suite always constructed DelegateTool with
`config={"features": {}, "settings": {"exclude_tools": []}}`, leaving every
feature flag at its default of True.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from amplifier_module_tool_delegate import DelegateTool


# =============================================================================
# Helpers
# =============================================================================


def _make_tool(*, self_delegation_enabled: bool) -> DelegateTool:
    """Construct a DelegateTool with the self-delegation flag explicitly set.

    Mirrors _make_tool() in test_delegate_spawn_new_session.py — sufficient
    coordinator/session_state stubs to instantiate the tool, with the only
    config-level variation being the flag under test.
    """
    coordinator = MagicMock()
    coordinator.session_id = "parent-session-123"
    coordinator.config = {"agents": {}}
    coordinator.session_state = {}
    coordinator._tool_dispatch_context = {}

    capabilities: dict = {
        "session.spawn": AsyncMock(
            return_value={
                "output": "done",
                "session_id": "child-001",
                "status": "success",
                "turn_count": 1,
                "metadata": {},
            }
        ),
        "self_delegation_depth": 0,
    }
    coordinator.get_capability = lambda name: capabilities.get(name)
    coordinator.get = MagicMock(return_value=None)

    parent_session = MagicMock()
    parent_session.session_id = "parent-session-123"
    parent_session.config = {"session": {"orchestrator": {}}}
    coordinator.session = parent_session

    config: dict = {
        "features": {"self_delegation": {"enabled": self_delegation_enabled}},
        "settings": {"exclude_tools": []},
    }
    return DelegateTool(coordinator, config)


# =============================================================================
# 1. execute(agent="self") behavior
# =============================================================================


class TestExecuteWithSelfWhenDisabled:
    """execute() must hard-reject `agent="self"` when the flag is False."""

    @pytest.mark.asyncio
    async def test_returns_error_result(self):
        """execute(agent="self") with self_delegation disabled returns an error
        ToolResult before any spawn attempt."""
        tool = _make_tool(self_delegation_enabled=False)

        result = await tool.execute(
            {
                "agent": "self",
                "instruction": "Do something",
                "context_depth": "none",
            }
        )

        # The exact return shape of ToolResult depends on the version of the
        # core protocol, but `success=False` and a non-empty error message are
        # invariants we depend on here.
        assert getattr(result, "success", None) is False, (
            "execute(agent='self') must return success=False when self-delegation is disabled"
        )
        error = getattr(result, "error", None)
        assert error is not None, "Expected error payload on rejection"
        # Accept either dict or string error shapes; the diagnostic must
        # reference self-delegation so the parent knows what to fix.
        error_text = (
            error.get("message", "") if isinstance(error, dict) else str(error)
        ).lower()
        assert "self" in error_text and "disabled" in error_text, (
            f"Error message should reference self-delegation being disabled; got: {error!r}"
        )

    @pytest.mark.asyncio
    async def test_does_not_call_session_spawn(self):
        """execute(agent="self") with the flag off must not invoke session.spawn —
        the rejection happens before any spawn attempt."""
        tool = _make_tool(self_delegation_enabled=False)
        spawn_capability = tool.coordinator.get_capability("session.spawn")

        await tool.execute(
            {"agent": "self", "instruction": "Do something", "context_depth": "none"}
        )

        spawn_capability.assert_not_called()


class TestExecuteWithSelfWhenEnabled:
    """When the flag is True, execute(agent="self") must NOT short-circuit on
    the disabled-check — it must proceed into the normal spawn path. We assert
    only that the disabled-check rejection does not fire; spawn behavior is
    covered by other test files."""

    @pytest.mark.asyncio
    async def test_does_not_short_circuit_with_disabled_error(self):
        tool = _make_tool(self_delegation_enabled=True)

        result = await tool.execute(
            {"agent": "self", "instruction": "Do something", "context_depth": "none"}
        )

        # If the disabled-check fired incorrectly, success would be False and
        # the error would say "disabled". Either result is acceptable here as
        # long as the error is NOT the disabled-marker — the spawn path may
        # produce its own outcomes which are tested elsewhere.
        error = getattr(result, "error", None) or {}
        error_text = (
            error.get("message", "") if isinstance(error, dict) else str(error)
        ).lower()
        if not getattr(result, "success", True):
            assert "disabled" not in error_text, (
                "execute(agent='self') with flag enabled must not return the"
                f" disabled-rejection; got: {error!r}"
            )


# =============================================================================
# 2. description property
# =============================================================================


class TestDescriptionFeatureLineConditional:
    """The conditional feature-listing line in the description (`-
    agent="self": Spawn yourself as a sub-agent`) must be present iff the
    flag is True. The generic "no agents currently registered. Use
    agent='self' or a bundle path." fallback is intentionally NOT gated
    here — that's tool-level boilerplate left generic across all bundles.
    Bundle-specific guidance (e.g. "self-delegation is disabled in this
    bundle, use a named specialist") belongs in the bundle's own context
    file (e.g. delegation-mechanics.md in the build-up bundle), not in
    the tool's generic description."""

    FEATURE_LINE = '- agent="self": Spawn yourself as a sub-agent'

    def test_description_omits_feature_line_when_disabled(self):
        tool = _make_tool(self_delegation_enabled=False)
        description = tool.description

        assert self.FEATURE_LINE not in description, (
            "Description must not advertise the self-delegation feature line"
            f" when disabled. Got:\n{description}"
        )

    def test_description_includes_feature_line_when_enabled(self):
        tool = _make_tool(self_delegation_enabled=True)
        description = tool.description

        assert self.FEATURE_LINE in description, (
            "Description must advertise the self-delegation feature line"
            f" when enabled. Got:\n{description}"
        )


# =============================================================================
# 3. input_schema agent description note
# =============================================================================


class TestInputSchemaAgentDescriptionReflectsFlag:
    """The schema's `agent` parameter description carries a status note that
    matches the runtime self_delegation flag — so the function-spec the model
    sees aligns with what execute() will actually accept."""

    def test_disabled_note_present_when_disabled(self):
        tool = _make_tool(self_delegation_enabled=False)
        schema = tool.input_schema

        agent_desc = schema["properties"]["agent"]["description"]
        assert "DISABLED" in agent_desc, (
            "Disabled-state note must be present in the agent param description"
            f" when self-delegation is disabled. Got: {agent_desc!r}"
        )
        assert "rejected" in agent_desc.lower() or "reject" in agent_desc.lower(), (
            "Disabled-state note should indicate that 'self' will be rejected"
            f". Got: {agent_desc!r}"
        )

    def test_enabled_note_present_when_enabled(self):
        tool = _make_tool(self_delegation_enabled=True)
        schema = tool.input_schema

        agent_desc = schema["properties"]["agent"]["description"]
        assert "enabled" in agent_desc.lower(), (
            "Enabled-state note should be present in the agent param description"
            f" when self-delegation is enabled. Got: {agent_desc!r}"
        )
        # The disabled-marker must NOT be present in the enabled state.
        assert "DISABLED" not in agent_desc, (
            "Disabled marker must not appear when self-delegation is enabled."
            f" Got: {agent_desc!r}"
        )

    def test_example_list_preserved_in_both_states(self):
        """The example targets ('foundation:explorer', 'self', bundle path) stay
        in the description in both states — only the appended status note
        changes. This is the explicit design choice: keep the schema
        descriptive about all valid forms, let the status note clarify which
        ones the current session will accept."""
        for enabled in (True, False):
            tool = _make_tool(self_delegation_enabled=enabled)
            agent_desc = tool.input_schema["properties"]["agent"]["description"]
            assert "foundation:explorer" in agent_desc, (
                f"Example list missing in {'enabled' if enabled else 'disabled'} state: {agent_desc!r}"
            )
            assert "bundle path" in agent_desc, (
                f"Example list missing in {'enabled' if enabled else 'disabled'} state: {agent_desc!r}"
            )
