"""Tests for amplifier_foundation.session.diagnosis module.

Layer Level 1 (Diagnosis Algebra): pure list[dict] -> list[dict], zero I/O.
"""

from __future__ import annotations

from amplifier_foundation.session.diagnosis import (
    DiagnosisResult,
    build_tool_index,
    diagnose_transcript,
    repair_transcript,
    rewind_transcript,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_entries_with_lines(messages: list[dict]) -> list[dict]:
    """Add 1-based line_num to each message."""
    return [{**m, "line_num": i + 1} for i, m in enumerate(messages)]


def _tc(tc_id: str, name: str) -> dict:
    """Build a tool_call dict (OpenAI format)."""
    return {"id": tc_id, "function": {"name": name}}


def _tc_amplifier(tc_id: str, name: str) -> dict:
    """Build a tool_call dict (Amplifier format — 'tool' key, no 'function' wrapper)."""
    return {"id": tc_id, "tool": name, "arguments": {}}


def _tool_result(tc_id: str, name: str, content: str) -> dict:
    """Build a tool result dict."""
    return {"role": "tool", "tool_call_id": tc_id, "name": name, "content": content}


# ---------------------------------------------------------------------------
# TestBuildToolIndex
# ---------------------------------------------------------------------------


class TestBuildToolIndex:
    def test_finds_tool_use_ids(self):
        """build_tool_index finds tool_use IDs with line_num, tool_name, entry_index."""
        entries = _make_entries_with_lines(
            [
                {"role": "user", "content": "Hello"},
                {
                    "role": "assistant",
                    "tool_calls": [_tc("call_abc", "my_tool")],
                },
            ]
        )
        index = build_tool_index(entries)
        assert "call_abc" in index["tool_uses"]
        info = index["tool_uses"]["call_abc"]
        assert info["line_num"] == 2
        assert info["tool_name"] == "my_tool"
        assert info["entry_index"] == 1

    def test_finds_tool_result_ids(self):
        """build_tool_index finds tool_result IDs with entry_index."""
        entries = _make_entries_with_lines(
            [
                {"role": "user", "content": "Hello"},
                {
                    "role": "assistant",
                    "tool_calls": [_tc("call_abc", "my_tool")],
                },
                _tool_result("call_abc", "my_tool", "result"),
            ]
        )
        index = build_tool_index(entries)
        assert "call_abc" in index["tool_results"]
        info = index["tool_results"]["call_abc"]
        assert info["entry_index"] == 2


# ---------------------------------------------------------------------------
# TestDiagnoseTranscript
# ---------------------------------------------------------------------------


class TestDiagnoseTranscript:
    def test_healthy_transcript(self):
        """A complete turn with no issues is healthy."""
        entries = _make_entries_with_lines(
            [
                {"role": "user", "content": "Hello"},
                {
                    "role": "assistant",
                    "tool_calls": [_tc("call_1", "do_thing")],
                },
                _tool_result("call_1", "do_thing", "ok"),
                {"role": "assistant", "content": "Done"},
            ]
        )
        result = diagnose_transcript(entries)
        assert result["status"] == "healthy"
        assert result["failure_modes"] == []
        assert result["orphaned_tool_ids"] == []
        assert result["misplaced_tool_ids"] == []
        assert result["incomplete_turns"] == []

    def test_detects_missing_tool_results(self):
        """Orphaned tool_calls (no result) produce missing_tool_results failure mode."""
        entries = _make_entries_with_lines(
            [
                {"role": "user", "content": "Hello"},
                {
                    "role": "assistant",
                    "tool_calls": [_tc("call_1", "tool_a"), _tc("call_2", "tool_b")],
                },
            ]
        )
        result = diagnose_transcript(entries)
        assert result["status"] == "broken"
        assert "missing_tool_results" in result["failure_modes"]
        assert set(result["orphaned_tool_ids"]) == {"call_1", "call_2"}

    def test_detects_ordering_violation(self):
        """Tool result after a user message produces ordering_violation."""
        tc_id = "call_1"
        entries = _make_entries_with_lines(
            [
                {"role": "user", "content": "Turn 1"},
                {
                    "role": "assistant",
                    "tool_calls": [_tc(tc_id, "my_tool")],
                },
                {"role": "user", "content": "Turn 2"},  # intervening user message
                _tool_result(tc_id, "my_tool", "result"),
            ]
        )
        result = diagnose_transcript(entries)
        assert "ordering_violation" in result["failure_modes"]
        assert tc_id in result["misplaced_tool_ids"]

    def test_detects_incomplete_assistant_turn(self):
        """Tool results followed immediately by user message = incomplete turn."""
        entries = _make_entries_with_lines(
            [
                {"role": "user", "content": "Turn 1"},
                {
                    "role": "assistant",
                    "tool_calls": [_tc("call_1", "my_tool")],
                },
                _tool_result("call_1", "my_tool", "result"),
                {"role": "user", "content": "Turn 2"},  # no closing assistant response
            ]
        )
        result = diagnose_transcript(entries)
        assert "incomplete_assistant_turn" in result["failure_modes"]
        assert len(result["incomplete_turns"]) == 1
        assert result["incomplete_turns"][0]["missing"] == "assistant_response"

    def test_no_tool_calls_is_healthy(self):
        """A transcript with no tool calls is healthy."""
        entries = _make_entries_with_lines(
            [
                {"role": "user", "content": "Hello"},
                {"role": "assistant", "content": "Hi"},
                {"role": "user", "content": "How are you?"},
                {"role": "assistant", "content": "Great"},
            ]
        )
        result = diagnose_transcript(entries)
        assert result["status"] == "healthy"

    def test_detects_partial_orphan_incomplete_turn(self):
        """Partial orphan (some results present, some missing) also detects incomplete turn."""
        entries = _make_entries_with_lines(
            [
                {"role": "user", "content": "Hello"},
                {
                    "role": "assistant",
                    "tool_calls": [_tc("call_1", "tool_a"), _tc("call_2", "tool_b")],
                },
                _tool_result("call_1", "tool_a", "ok"),  # tc_1 completed
                # tc_2 never completed — crash happened here
            ]
        )
        result = diagnose_transcript(entries)
        assert result["status"] == "broken"
        assert "missing_tool_results" in result["failure_modes"]
        assert "incomplete_assistant_turn" in result["failure_modes"]
        assert result["orphaned_tool_ids"] == ["call_2"]
        assert len(result["incomplete_turns"]) == 1
        assert result["incomplete_turns"][0]["after_index"] == 2

    def test_recommended_action(self):
        """recommended_action is 'none' for healthy and 'repair' for broken."""
        healthy_entries = _make_entries_with_lines(
            [
                {"role": "user", "content": "Hello"},
                {"role": "assistant", "content": "Hi"},
            ]
        )
        broken_entries = _make_entries_with_lines(
            [
                {"role": "user", "content": "Hello"},
                {
                    "role": "assistant",
                    "tool_calls": [_tc("call_1", "my_tool")],
                },
            ]
        )
        healthy = diagnose_transcript(healthy_entries)
        broken = diagnose_transcript(broken_entries)
        assert healthy["recommended_action"] == "none"
        assert broken["recommended_action"] == "repair"


# ---------------------------------------------------------------------------
# TestRepairTranscript
# ---------------------------------------------------------------------------


class TestRepairTranscript:
    def test_healthy_transcript_unchanged(self):
        """Healthy transcript is returned unchanged (minus line_num)."""
        entries = _make_entries_with_lines(
            [
                {"role": "user", "content": "Hello"},
                {"role": "assistant", "content": "Hi"},
            ]
        )
        diagnosis: DiagnosisResult = {
            "status": "healthy",
            "failure_modes": [],
            "orphaned_tool_ids": [],
            "misplaced_tool_ids": [],
            "incomplete_turns": [],
            "recommended_action": "none",
        }
        result = repair_transcript(entries, diagnosis)
        assert len(result) == 2
        assert all("line_num" not in e for e in result)
        assert result[0]["role"] == "user"
        assert result[1]["role"] == "assistant"

    def test_injects_synthetics_for_orphans(self):
        """Orphaned tool calls get synthetic tool results injected."""
        entries = _make_entries_with_lines(
            [
                {"role": "user", "content": "Hello"},
                {
                    "role": "assistant",
                    "tool_calls": [_tc("call_1", "tool_a"), _tc("call_2", "tool_b")],
                },
                {"role": "user", "content": "Next turn"},
            ]
        )
        diagnosis = diagnose_transcript(entries)
        result = repair_transcript(entries, diagnosis)
        tool_results = [e for e in result if e.get("role") == "tool"]
        assert len(tool_results) == 2
        for tr in tool_results:
            assert "error" in tr["content"]

    def test_removes_misplaced_and_injects_synthetic(self):
        """Misplaced tool result is removed; synthetic result is injected in order."""
        tc_id = "call_1"
        original_result = _tool_result(tc_id, "my_tool", "original_content")
        entries = _make_entries_with_lines(
            [
                {"role": "user", "content": "Turn 1"},
                {
                    "role": "assistant",
                    "tool_calls": [_tc(tc_id, "my_tool")],
                },
                {"role": "user", "content": "Turn 2 (disruption)"},
                original_result,
            ]
        )
        diagnosis = diagnose_transcript(entries)
        result = repair_transcript(entries, diagnosis)
        # Original misplaced result should NOT be in output
        assert not any(e.get("content") == "original_content" for e in result)
        # Exactly 1 synthetic tool result should be present
        tool_results = [e for e in result if e.get("role") == "tool"]
        assert len(tool_results) == 1


# ---------------------------------------------------------------------------
# TestRewindTranscript
# ---------------------------------------------------------------------------


class TestRewindTranscript:
    def test_rewinds_before_first_issue(self):
        """Rewind returns 2 entries from the first healthy turn."""
        entries = _make_entries_with_lines(
            [
                {"role": "user", "content": "Turn 1"},  # index 0
                {"role": "assistant", "content": "Response"},  # index 1
                {"role": "user", "content": "Turn 2"},  # index 2
                {
                    "role": "assistant",
                    "tool_calls": [_tc("call_1", "my_tool")],  # orphan
                },  # index 3
            ]
        )
        diagnosis = diagnose_transcript(entries)
        result = rewind_transcript(entries, diagnosis)
        assert len(result) == 2
        assert result[0]["role"] == "user"
        assert result[1]["role"] == "assistant"

    def test_healthy_returns_all(self):
        """Healthy transcript rewind returns all entries stripped of line_num."""
        entries = _make_entries_with_lines(
            [
                {"role": "user", "content": "Hello"},
                {"role": "assistant", "content": "Hi"},
            ]
        )
        diagnosis: DiagnosisResult = {
            "status": "healthy",
            "failure_modes": [],
            "orphaned_tool_ids": [],
            "misplaced_tool_ids": [],
            "incomplete_turns": [],
            "recommended_action": "none",
        }
        result = rewind_transcript(entries, diagnosis)
        assert len(result) == 2
        assert all("line_num" not in e for e in result)

    def test_first_turn_broken_returns_empty(self):
        """Rewind with first turn broken returns empty list."""
        entries = _make_entries_with_lines(
            [
                {"role": "user", "content": "Turn 1"},
                {
                    "role": "assistant",
                    "tool_calls": [_tc("call_1", "my_tool")],  # orphan
                },
            ]
        )
        diagnosis = diagnose_transcript(entries)
        result = rewind_transcript(entries, diagnosis)
        assert result == []


# ---------------------------------------------------------------------------
# TestIntegrationRoundtrip
# ---------------------------------------------------------------------------


class TestIntegrationRoundtrip:
    def test_orphans_roundtrip(self):
        """diagnose -> repair -> re-diagnose for orphans produces healthy."""
        entries = _make_entries_with_lines(
            [
                {"role": "user", "content": "Hello"},
                {
                    "role": "assistant",
                    "tool_calls": [_tc("call_1", "my_tool")],
                },
                {"role": "user", "content": "Follow up"},
            ]
        )
        diagnosis = diagnose_transcript(entries)
        repaired = repair_transcript(entries, diagnosis)
        # Re-diagnose: add line_nums back for the re-diagnosis
        re_entries = _make_entries_with_lines(repaired)
        re_diagnosis = diagnose_transcript(re_entries)
        assert re_diagnosis["status"] == "healthy"

    def test_partial_orphan_roundtrip(self):
        """Partial orphan (some results present) is fully healed in one pass.

        Regression test: before the fix, repair only injected a synthetic
        result for the orphan but not the closing assistant response, requiring
        a second diagnose-repair cycle.
        """
        entries = _make_entries_with_lines(
            [
                {"role": "user", "content": "Hello"},
                {
                    "role": "assistant",
                    "tool_calls": [_tc("call_1", "tool_a"), _tc("call_2", "tool_b")],
                },
                _tool_result("call_1", "tool_a", "ok"),  # tc_1 completed
                # tc_2 never completed — crash happened here
            ]
        )
        diagnosis = diagnose_transcript(entries)
        repaired = repair_transcript(entries, diagnosis)

        # Verify structure: should have synthetic result for tc_2 AND closing response
        tool_results = [e for e in repaired if e.get("role") == "tool"]
        assert len(tool_results) == 2  # real + synthetic
        assistant_responses = [
            e
            for e in repaired
            if e.get("role") == "assistant" and "tool_calls" not in e
        ]
        assert len(assistant_responses) >= 1  # closing response injected

        # Re-diagnose: must be healthy in ONE pass
        re_entries = _make_entries_with_lines(repaired)
        re_diagnosis = diagnose_transcript(re_entries)
        assert re_diagnosis["status"] == "healthy"

    def test_partial_orphan_roundtrip_without_line_num(self):
        """Partial orphan repair works even without line_num on entries.

        The app-CLI loads transcripts without line_num. After a first repair
        pass strips line_num, any subsequent diagnosis must still work via
        after_index rather than after_line.
        """
        entries = [
            {"role": "user", "content": "Hello"},
            {
                "role": "assistant",
                "tool_calls": [_tc("call_1", "tool_a"), _tc("call_2", "tool_b")],
            },
            {
                "role": "tool",
                "tool_call_id": "call_1",
                "name": "tool_a",
                "content": "ok",
            },
        ]
        diagnosis = diagnose_transcript(entries)
        repaired = repair_transcript(entries, diagnosis)
        # Re-diagnose WITHOUT adding line_num back (simulating app-CLI path)
        re_diagnosis = diagnose_transcript(repaired)
        assert re_diagnosis["status"] == "healthy"

    def test_amplifier_format_tool_calls(self):
        """Tool calls using Amplifier format ('tool' key) are indexed and repaired correctly."""
        entries = _make_entries_with_lines(
            [
                {"role": "user", "content": "Hello"},
                {
                    "role": "assistant",
                    "tool_calls": [_tc_amplifier("call_1", "bash")],
                },
            ]
        )
        # Index should find the tool name
        index = build_tool_index(entries)
        assert index["tool_uses"]["call_1"]["tool_name"] == "bash"

        # Repair should use the correct name in synthetic results
        diagnosis = diagnose_transcript(entries)
        repaired = repair_transcript(entries, diagnosis)
        synthetic = [e for e in repaired if e.get("role") == "tool"]
        assert len(synthetic) == 1
        assert synthetic[0]["name"] == "bash"

    def test_combined_failures_roundtrip(self):
        """diagnose -> repair -> re-diagnose for ordering+orphans produces healthy."""
        tc_orphan = "call_orphan"
        tc_misplaced = "call_misplaced"
        original_result = _tool_result(tc_misplaced, "tool_b", "original")
        entries = _make_entries_with_lines(
            [
                {"role": "user", "content": "Turn 1"},
                {
                    "role": "assistant",
                    "tool_calls": [
                        _tc(tc_orphan, "tool_a"),
                        _tc(tc_misplaced, "tool_b"),
                    ],
                },
                {"role": "user", "content": "Turn 2"},  # disruption
                original_result,  # misplaced result
            ]
        )
        diagnosis = diagnose_transcript(entries)
        repaired = repair_transcript(entries, diagnosis)
        re_entries = _make_entries_with_lines(repaired)
        re_diagnosis = diagnose_transcript(re_entries)
        assert re_diagnosis["status"] == "healthy"
