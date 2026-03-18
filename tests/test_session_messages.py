"""Tests for amplifier_foundation.session.messages module.

Layer Level 1 (Message Algebra): pure list[dict] -> list[dict], zero I/O.
"""

from __future__ import annotations

import json

from amplifier_foundation.session.messages import (
    add_synthetic_tool_results,
    count_turns,
    find_orphaned_tool_calls,
    get_turn_boundaries,
    get_turn_summary,
    is_real_user_message,
    slice_to_turn,
)


# =============================================================================
# TestIsRealUserMessage
# =============================================================================


class TestIsRealUserMessage:
    def test_plain_user_message_returns_true(self):
        """A plain user message with string content is real."""
        entry = {"role": "user", "content": "Hello, world!"}
        assert is_real_user_message(entry) is True

    def test_tool_call_id_present_returns_false(self):
        """A user-role message with tool_call_id is not real."""
        entry = {"role": "user", "tool_call_id": "call_abc123", "content": "result data"}
        assert is_real_user_message(entry) is False

    def test_role_tool_returns_false(self):
        """A message with role='tool' is not a real user message."""
        entry = {"role": "tool", "tool_call_id": "call_abc123", "content": "result data"}
        assert is_real_user_message(entry) is False

    def test_string_content_starting_with_system_reminder_returns_false(self):
        """A user message with string content starting with <system-reminder> is not real."""
        entry = {
            "role": "user",
            "content": "<system-reminder>You are helpful</system-reminder>",
        }
        assert is_real_user_message(entry) is False

    def test_list_content_with_system_reminder_text_returns_false(self):
        """A user message with list content containing <system-reminder> is not real."""
        entry = {
            "role": "user",
            "content": [
                {
                    "type": "text",
                    "text": "<system-reminder>injected context</system-reminder>",
                }
            ],
        }
        assert is_real_user_message(entry) is False

    def test_assistant_role_returns_false(self):
        """An assistant message is not a real user message."""
        entry = {"role": "assistant", "content": "I can help with that."}
        assert is_real_user_message(entry) is False

    def test_empty_content_user_message_returns_true(self):
        """A user message with empty string content is still real."""
        entry = {"role": "user", "content": ""}
        assert is_real_user_message(entry) is True

    def test_list_content_without_system_reminder_returns_true(self):
        """A user message with list content that has no system-reminder is real."""
        entry = {
            "role": "user",
            "content": [{"type": "text", "text": "regular user text"}],
        }
        assert is_real_user_message(entry) is True


# =============================================================================
# TestParameterizedSyntheticContent
# =============================================================================


class TestParameterizedSyntheticContent:
    """Tests for the synthetic_content parameter in add_synthetic_tool_results."""

    def _make_messages_with_orphan(self, tool_id: str = "tc_a") -> list[dict]:
        """Helper: assistant message with an orphaned tool call."""
        return [
            {"role": "user", "content": "Do something"},
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {
                        "id": tool_id,
                        "type": "function",
                        "function": {"name": "bash", "arguments": "{}"},
                    }
                ],
            },
        ]

    def test_default_content_contains_forked_key(self):
        """Default synthetic content is fork-style JSON with a 'forked' key."""
        messages = self._make_messages_with_orphan("tc_a")
        result = add_synthetic_tool_results(messages, ["tc_a"])
        tool_results = [m for m in result if m.get("role") == "tool"]
        assert len(tool_results) == 1
        content = json.loads(tool_results[0]["content"])
        assert "forked" in content

    def test_custom_synthetic_content_string_used_when_provided(self):
        """When synthetic_content is given, it is used as the tool result content."""
        messages = self._make_messages_with_orphan("tc_b")
        custom = '{"error": "custom error message"}'
        result = add_synthetic_tool_results(messages, ["tc_b"], synthetic_content=custom)
        tool_results = [m for m in result if m.get("role") == "tool"]
        assert len(tool_results) == 1
        assert tool_results[0]["content"] == custom

    def test_custom_content_applied_to_all_results_for_multiple_orphans(self):
        """Custom synthetic_content is applied to every orphaned tool call."""
        messages = [
            {"role": "user", "content": "Do two things"},
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {
                        "id": "tc_x",
                        "type": "function",
                        "function": {"name": "bash", "arguments": "{}"},
                    },
                    {
                        "id": "tc_y",
                        "type": "function",
                        "function": {"name": "grep", "arguments": "{}"},
                    },
                ],
            },
        ]
        custom = '{"error": "all interrupted"}'
        result = add_synthetic_tool_results(
            messages, ["tc_x", "tc_y"], synthetic_content=custom
        )
        tool_results = [m for m in result if m.get("role") == "tool"]
        assert len(tool_results) == 2
        for tr in tool_results:
            assert tr["content"] == custom


# =============================================================================
# TestExistingFunctionsAvailable
# =============================================================================


class TestExistingFunctionsAvailable:
    """Verify all functions from slice.py are available via messages.py."""

    def test_get_turn_boundaries_returns_user_indices(self):
        """get_turn_boundaries returns [0, 2] for 3-message conversation."""
        messages = [
            {"role": "user", "content": "Q1"},
            {"role": "assistant", "content": "A1"},
            {"role": "user", "content": "Q2"},
        ]
        assert get_turn_boundaries(messages) == [0, 2]

    def test_count_turns_returns_1_for_user_plus_assistant(self):
        """count_turns returns 1 for a user + assistant message pair."""
        messages = [
            {"role": "user", "content": "Hello"},
            {"role": "assistant", "content": "Hi"},
        ]
        assert count_turns(messages) == 1

    def test_slice_to_turn_returns_2_messages(self):
        """slice_to_turn(messages, 1) returns 2 messages for a 2-turn conversation."""
        messages = [
            {"role": "user", "content": "Q1"},
            {"role": "assistant", "content": "A1"},
            {"role": "user", "content": "Q2"},
            {"role": "assistant", "content": "A2"},
        ]
        sliced = slice_to_turn(messages, 1)
        assert len(sliced) == 2

    def test_find_orphaned_tool_calls_finds_orphan(self):
        """find_orphaned_tool_calls correctly identifies orphaned tool call 'tc_a'."""
        messages = [
            {"role": "user", "content": "Do something"},
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {
                        "id": "tc_a",
                        "type": "function",
                        "function": {"name": "bash", "arguments": "{}"},
                    }
                ],
            },
        ]
        orphans = find_orphaned_tool_calls(messages)
        assert "tc_a" in orphans

    def test_get_turn_summary_returns_dict_with_turn(self):
        """get_turn_summary returns a dict with the correct turn number."""
        messages = [
            {"role": "user", "content": "Hello"},
            {"role": "assistant", "content": "Hi there"},
        ]
        summary = get_turn_summary(messages, 1)
        assert isinstance(summary, dict)
        assert summary["turn"] == 1
