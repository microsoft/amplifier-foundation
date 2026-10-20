"""Tests for amplifier_foundation.session.diagnosis module.

Layer Level 1 (Diagnosis Algebra): pure list[dict] -> list[dict], zero I/O.
"""

from __future__ import annotations

import copy
import json

import pytest

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

    def test_relocates_misplaced_original_payload(self):
        """A late receipt is moved intact, never replaced with an unknown result."""
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
        tool_results = [e for e in result if e.get("role") == "tool"]
        assert tool_results == [original_result]
        assert result[2] == original_result


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

    def test_late_receipt_preserves_entire_messages_and_inputs(self):
        receipt = {
            "role": "tool",
            "tool_call_id": "observed",
            "name": "action",
            "content": [
                {"type": "text", "text": "REAL-RESULT: completed action"},
                {"type": "image", "data": "opaque"},
            ],
            "metadata": {"receipt": 7, "_seq": 4},
            "cost": {"total": 0.25},
            "signature": "receipt-signature",
            "extra": {"nested": ["untouched"]},
        }
        call = {
            "role": "assistant",
            "content": [
                {
                    "type": "thinking",
                    "thinking": "opaque",
                    "signature": "signed-thinking",
                }
            ],
            "tool_calls": [{"id": "observed", "name": "action", "arguments": {}}],
            "metadata": {"_seq": 2},
            "cost": {"total": 0.5},
            "continuation": {"opaque": True},
        }
        messages = [
            {"role": "user", "content": "Perform authorized action"},
            call,
            {"role": "user", "content": "Stop further work"},
            receipt,
            {"role": "assistant", "content": "Observed result recorded"},
        ]
        entries = _make_entries_with_lines(messages)
        before = copy.deepcopy(entries)
        diagnosis = diagnose_transcript(entries)
        diagnosis_before = copy.deepcopy(diagnosis)
        repaired = repair_transcript(entries, diagnosis)
        assert repaired[1:3] == [call, receipt]
        assert [e for e in repaired if e["role"] == "tool"] == [receipt]
        # Original non-result messages retain their relative order.
        assert [e for e in repaired if e in messages and e["role"] != "tool"] == [
            e for e in messages if e["role"] != "tool"
        ]
        assert entries == before
        assert diagnosis == diagnosis_before
        assert all("line_num" not in e for e in repaired)
        for _ in range(3):
            next_diagnosis = diagnose_transcript(repaired)
            assert next_diagnosis["status"] == "healthy"
            assert repair_transcript(repaired, next_diagnosis) == repaired

    @pytest.mark.parametrize("with_missing", [False, True])
    def test_parallel_batch_gathers_adjacent_and_late_receipts(self, with_missing):
        calls = [_tc("a", "first"), _tc("b", "second"), _tc("c", "third")]
        if with_missing:
            calls.append(_tc("missing", "fourth"))
        adjacent = _tool_result("b", "second", "ADJACENT")
        late_c = _tool_result("c", "third", "LATE-C")
        late_a = _tool_result("a", "first", "LATE-A")
        entries = [
            {"role": "user", "content": "Start"},
            {"role": "assistant", "tool_calls": calls},
            adjacent,
            {"role": "user", "content": "Stop"},
            late_c,
            late_a,
        ]
        before = copy.deepcopy(entries)
        diagnosis = diagnose_transcript(entries)
        assert set(diagnosis["misplaced_tool_ids"]) == {"a", "c"}
        repaired = repair_transcript(entries, diagnosis)
        # Original result completion order is retained within the gathered batch.
        assert repaired[2:5] == [adjacent, late_c, late_a]
        batch_size = 4 if with_missing else 3
        assert all(e["role"] == "tool" for e in repaired[2 : 2 + batch_size])
        assert repaired[2 + batch_size]["role"] == "assistant"
        assert repaired[3 + batch_size] == {"role": "user", "content": "Stop"}
        if with_missing:
            placeholder = repaired[5]
            assert placeholder["tool_call_id"] == "missing"
            assert "outcome is unknown" in json.loads(placeholder["content"])["message"]
        assert entries == before
        assert diagnose_transcript(repaired)["status"] == "healthy"
        assert repair_transcript(repaired, diagnose_transcript(repaired)) == repaired

    def test_partial_orphan_closes_after_actual_last_result(self):
        first = _tool_result("b", "second", "B")
        last = _tool_result("a", "first", "A")
        entries = [
            {
                "role": "assistant",
                "tool_calls": [
                    _tc("a", "first"),
                    _tc("missing", "third"),
                    _tc("b", "second"),
                ],
            },
            first,
            last,
        ]
        diagnosis = diagnose_transcript(entries)
        assert diagnosis["incomplete_turns"][0]["after_index"] == 2
        repaired = repair_transcript(entries, diagnosis)
        assert repaired[1:3] == [first, last]
        assert repaired[3]["tool_call_id"] == "missing"
        assert repaired[4]["role"] == "assistant"
        assert diagnose_transcript(repaired)["status"] == "healthy"

    @pytest.mark.parametrize("id_key", ["id", "tool_call_id"])
    def test_canonical_block_call_and_single_block_receipt(self, id_key):
        call = {
            "role": "assistant",
            "content": [
                {"type": "tool_call", id_key: "a", "name": "action", "arguments": {}}
            ],
        }
        receipt = {
            "role": "tool",
            "content": [
                {"type": "tool_result", "tool_call_id": "a", "output": {"ok": 1}}
            ],
            "metadata": {"receipt": "canonical"},
            "extra": ["preserve"],
        }
        entries = [call, {"role": "user", "content": "Stop"}, receipt]
        index = build_tool_index(entries)
        assert index["tool_uses"]["a"]["tool_name"] == "action"
        assert index["tool_results"]["a"]["entry_index"] == 2
        repaired = repair_transcript(entries, diagnose_transcript(entries))
        assert repaired[:2] == [call, receipt]
        assert len([e for e in repaired if e["role"] == "tool"]) == 1
        assert diagnose_transcript(repaired)["status"] == "healthy"

    def test_equivalent_same_entry_mirrors_are_one_healthy_call(self):
        call = {
            "role": "assistant",
            "tool_calls": [
                {"id": "a", "function": {"name": "action", "arguments": '{"value": 1}'}}
            ],
            "content": [
                {
                    "type": "tool_call",
                    "id": "a",
                    "name": "action",
                    "input": {"value": 1},
                }
            ],
        }
        receipt = {
            **_tool_result("a", "action", ""),
            "content": [{"type": "tool_result", "tool_call_id": "a", "output": "REAL"}],
        }
        entries = [call, receipt, {"role": "assistant", "content": "Done"}]
        assert len(build_tool_index(entries)["tool_uses"]) == 1
        diagnosis = diagnose_transcript(entries)
        assert diagnosis["status"] == "healthy"
        assert repair_transcript(entries, diagnosis) == entries

    def test_mirrored_missing_call_produces_only_one_unknown_placeholder(self):
        entries = [
            {
                "role": "assistant",
                "tool_calls": [_tc_amplifier("a", "action")],
                "content": [
                    {"type": "tool_call", "id": "a", "name": "action", "arguments": {}}
                ],
            }
        ]
        repaired = repair_transcript(entries, diagnose_transcript(entries))
        results = [e for e in repaired if e["role"] == "tool"]
        assert len(results) == 1
        payload = json.loads(results[0]["content"])
        assert payload["error"] == "unknown_error"
        assert payload["message"] == (
            "No result was recorded; actual execution outcome is unknown. "
            "Verify independent evidence before repeating an action."
        )
        notice = repaired[-1]["content"][0]["text"]
        assert "Derived repair notice" in notice
        assert not any(
            word in notice.lower() for word in ("interrupted", "failed", "cancelled")
        )
        assert diagnose_transcript(repaired)["status"] == "healthy"

    @pytest.mark.parametrize("first,second", [(True, 1), (False, 0), (1, 1.0)])
    def test_mirror_argument_comparison_preserves_json_types(self, first, second):
        entries = [
            {
                "role": "assistant",
                "tool_calls": [
                    {"id": "a", "name": "action", "arguments": {"value": first}}
                ],
                "content": [
                    {
                        "type": "tool_call",
                        "id": "a",
                        "name": "action",
                        "input": {"value": second},
                    }
                ],
            },
            _tool_result("a", "action", "ACTUAL"),
            {"role": "assistant", "content": "Done"},
        ]
        before = copy.deepcopy(entries)
        diagnosis = diagnose_transcript(entries)
        assert diagnosis["status"] == "broken"
        assert diagnosis["unresolved_tool_ids"] == ["a"]
        assert repair_transcript(entries, diagnosis) == entries == before

    @pytest.mark.parametrize(
        "case",
        [
            "reused_call",
            "conflicting_reused_call",
            "duplicate_call",
            "conflicting_mirror_name",
            "conflicting_mirror_arguments",
            "duplicate_result",
            "conflicting_result",
            "late_duplicate_result",
            "result_before_call",
            "unbound_result",
            "idless_result",
            "conflicting_result_ids",
            "multiple_result_blocks",
            "unrelated_call_boundary",
        ],
    )
    def test_unsafe_associations_remain_broken_and_preserve_evidence(self, case):
        call = {"role": "assistant", "tool_calls": [_tc_amplifier("a", "action")]}
        receipt = _tool_result("a", "action", "REAL")
        closing = {"role": "assistant", "content": "Done"}
        entries = [call, receipt, closing]
        if case in ("reused_call", "conflicting_reused_call"):
            other = copy.deepcopy(call)
            if case == "conflicting_reused_call":
                other["tool_calls"][0]["tool"] = "different"
            entries = [
                call,
                receipt,
                closing,
                other,
                _tool_result("a", "action", "SECOND"),
                closing,
            ]
        elif case == "duplicate_call":
            call["tool_calls"].append(copy.deepcopy(call["tool_calls"][0]))
        elif case.startswith("conflicting_mirror"):
            block = {"type": "tool_call", "id": "a", "name": "action", "arguments": {}}
            if case == "conflicting_mirror_name":
                block["name"] = "different"
            else:
                block["arguments"] = {"different": True}
            call["content"] = [block]
        elif case in ("duplicate_result", "conflicting_result"):
            entries.insert(
                2,
                _tool_result(
                    "a", "action", "REAL" if case == "duplicate_result" else "CONFLICT"
                ),
            )
        elif case == "late_duplicate_result":
            entries = [
                call,
                {"role": "user", "content": "Stop"},
                receipt,
                copy.deepcopy(receipt),
                closing,
            ]
        elif case == "result_before_call":
            entries = [receipt, call, closing]
        elif case == "unbound_result":
            entries = [receipt, closing]
        elif case == "idless_result":
            entries = [{"role": "tool", "content": "UNBOUND"}, closing]
        elif case == "conflicting_result_ids":
            receipt["content"] = [
                {"type": "tool_result", "tool_call_id": "other", "output": "REAL"}
            ]
        elif case == "multiple_result_blocks":
            call["tool_calls"].append(_tc_amplifier("b", "second"))
            entries = [
                call,
                {
                    "role": "tool",
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_call_id": "a",
                            "output": "REAL-A",
                        },
                        {
                            "type": "tool_result",
                            "tool_call_id": "b",
                            "output": "REAL-B",
                        },
                    ],
                    "extra": "whole message",
                },
                closing,
            ]
        elif case == "unrelated_call_boundary":
            entries = [
                call,
                {"role": "assistant", "tool_calls": [_tc("b", "second")]},
                _tool_result("b", "second", "B"),
                receipt,
                closing,
            ]
        before = copy.deepcopy(entries)
        diagnosis = diagnose_transcript(entries)
        assert diagnosis["status"] == "broken"
        assert diagnosis["unresolved_tool_ids"]
        assert "unresolved_tool_association" in diagnosis["failure_modes"]
        repaired = repair_transcript(entries, diagnosis)
        assert repaired == before
        assert entries == before
        assert diagnose_transcript(repaired)["status"] == "broken"
        assert repair_transcript(repaired, diagnose_transcript(repaired)) == repaired

    def test_reused_id_index_keeps_first_occurrence_and_legacy_keys(self):
        entries = _make_entries_with_lines(
            [
                {"role": "assistant", "tool_calls": [_tc("a", "first")]},
                _tool_result("a", "first", "FIRST"),
                {"role": "assistant", "tool_calls": [_tc("a", "second")]},
                _tool_result("a", "second", "SECOND"),
            ]
        )
        index = build_tool_index(entries)
        assert index["tool_uses"]["a"] == {
            "line_num": 1,
            "tool_name": "first",
            "entry_index": 0,
        }
        assert index["tool_results"]["a"] == {"line_num": 2, "entry_index": 1}
        assert index["unresolved_tool_ids"] == ["a"]

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
