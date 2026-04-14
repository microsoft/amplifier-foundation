"""Tests for tracing.py W3C sub-session ID generation."""

from __future__ import annotations

import re

from amplifier_foundation.tracing import generate_sub_session_id


class TestGenerateSubSessionId:
    """Tests for generate_sub_session_id function."""

    def test_format_with_agent_name(self) -> None:
        """Generates correct format with agent name."""
        sub_id = generate_sub_session_id(agent_name="researcher")
        # Format: {parent-span}-{child-span}_{agent-name}
        pattern = r"^[0-9a-f]{16}-[0-9a-f]{16}_researcher$"
        assert re.match(pattern, sub_id)

    def test_default_agent_name(self) -> None:
        """Uses 'agent' as default name."""
        sub_id = generate_sub_session_id()
        assert sub_id.endswith("_agent")

    def test_sanitizes_agent_name(self) -> None:
        """Sanitizes special characters in agent name."""
        sub_id = generate_sub_session_id(agent_name="My Agent!")
        assert "_my-agent" in sub_id

    def test_root_sub_session_has_zero_parent(self) -> None:
        """First-level sub-session has all-zero parent span."""
        sub_id = generate_sub_session_id(agent_name="test")
        # Root sub-sessions start with 16 zeros
        assert sub_id.startswith("0" * 16 + "-")

    def test_extracts_parent_span_from_parent_session(self) -> None:
        """Extracts child span from parent to use as parent span."""
        # Create a parent sub-session
        parent_id = generate_sub_session_id(agent_name="parent")

        # Extract the child span from parent (second 16-char hex group)
        parent_child_span = parent_id.split("-")[1].split("_")[0]

        # Create child sub-session
        child_id = generate_sub_session_id(agent_name="child", parent_session_id=parent_id)

        # Child's parent span should be parent's child span
        child_parent_span = child_id.split("-")[0]
        assert child_parent_span == parent_child_span

    def test_derives_parent_span_from_trace_id(self) -> None:
        """Derives parent span from trace ID when no parent session."""
        trace_id = "12345678901234567890123456789012"  # 32 hex chars
        sub_id = generate_sub_session_id(agent_name="worker", parent_trace_id=trace_id)

        # Middle 16 chars (positions 8-24) of trace ID should be parent span
        expected_parent_span = trace_id[8:24]
        actual_parent_span = sub_id.split("-")[0]
        assert actual_parent_span == expected_parent_span

    def test_unique_child_spans(self) -> None:
        """Each sub-session gets unique child span."""
        ids = [generate_sub_session_id(agent_name="test") for _ in range(100)]
        # Extract child spans (second 16-char hex group)
        child_spans = [id.split("-")[1].split("_")[0] for id in ids]
        assert len(set(child_spans)) == 100

    def test_empty_agent_name_uses_default(self) -> None:
        """Empty string agent name uses 'agent' default."""
        sub_id = generate_sub_session_id(agent_name="")
        assert sub_id.endswith("_agent")

    def test_hyphenated_agent_name_preserved(self) -> None:
        """Hyphenated agent names are preserved."""
        sub_id = generate_sub_session_id(agent_name="zen-architect")
        assert sub_id.endswith("_zen-architect")

    def test_colon_in_agent_name_sanitized(self) -> None:
        """Colons in agent names are replaced with hyphens (Windows filesystem compatibility)."""
        sub_id = generate_sub_session_id(agent_name="foundation:explorer")
        # Colon should be replaced with hyphen
        assert sub_id.endswith("_foundation-explorer")
        # Verify no colons in the entire ID (Windows doesn't allow colons in paths)
        assert ":" not in sub_id
