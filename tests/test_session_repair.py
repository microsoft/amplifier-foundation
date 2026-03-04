"""Tests for scripts/session-repair.py session repair tool."""

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

# Load session-repair.py as a module (it's a script, not a package)
_script_path = Path(__file__).resolve().parent.parent / "scripts" / "session-repair.py"
_spec = importlib.util.spec_from_file_location("session_repair", _script_path)
if _spec is None or _spec.loader is None:
    raise ImportError(f"Could not load {_script_path}")
_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mod)

parse_transcript = _mod.parse_transcript
build_tool_index = _mod.build_tool_index
is_real_user_message = _mod.is_real_user_message
diagnose = _mod.diagnose
repair_transcript = _mod.repair_transcript
rewind_transcript = _mod.rewind_transcript


def _write_transcript(tmp_path: Path, entries: list[dict]) -> Path:
    """Write a list of dicts as transcript.jsonl and return the session dir.

    Creates *tmp_path* as a directory (if needed), writes ``transcript.jsonl``
    inside it, and returns *tmp_path* (the session directory).
    """
    tmp_path.mkdir(parents=True, exist_ok=True)
    p = tmp_path / "transcript.jsonl"
    with open(p, "w", encoding="utf-8") as f:
        for entry in entries:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    return tmp_path


# ---------------------------------------------------------------------------
# TestParseTranscript
# ---------------------------------------------------------------------------
class TestParseTranscript:
    def test_parses_simple_transcript(self, tmp_path):
        """A 2-turn conversation is parsed into 2 entries."""
        entries = [
            {"role": "user", "content": "Hello"},
            {"role": "assistant", "content": "Hi there"},
        ]
        session_dir = _write_transcript(tmp_path, entries)
        result = parse_transcript(session_dir / "transcript.jsonl")
        assert len(result) == 2
        assert result[0]["role"] == "user"
        assert result[0]["content"] == "Hello"
        assert result[1]["role"] == "assistant"
        assert result[1]["content"] == "Hi there"

    def test_entries_have_line_numbers(self, tmp_path):
        """Each entry has a 1-based line_num key."""
        entries = [
            {"role": "user", "content": "First"},
            {"role": "assistant", "content": "Second"},
            {"role": "user", "content": "Third"},
        ]
        session_dir = _write_transcript(tmp_path, entries)
        result = parse_transcript(session_dir / "transcript.jsonl")
        assert result[0]["line_num"] == 1
        assert result[1]["line_num"] == 2
        assert result[2]["line_num"] == 3

    def test_empty_transcript(self, tmp_path):
        """An empty transcript returns an empty list."""
        session_dir = _write_transcript(tmp_path, [])
        result = parse_transcript(session_dir / "transcript.jsonl")
        assert result == []

    def test_blank_lines_are_skipped(self, tmp_path):
        """Blank lines are skipped and line_num reflects original file positions."""
        p = tmp_path / "transcript.jsonl"
        p.write_text(
            '{"role": "user", "content": "first"}\n'
            "\n"
            '{"role": "assistant", "content": "second"}\n',
            encoding="utf-8",
        )
        result = parse_transcript(p)
        assert len(result) == 2
        assert result[0]["line_num"] == 1
        assert result[1]["line_num"] == 3

    def test_malformed_json_raises_value_error(self, tmp_path):
        """Invalid JSON raises ValueError identifying the bad line number."""
        p = tmp_path / "transcript.jsonl"
        p.write_text(
            '{"role": "user", "content": "ok"}\nNOT VALID JSON\n',
            encoding="utf-8",
        )
        with pytest.raises(ValueError, match="line 2"):
            parse_transcript(p)


# ---------------------------------------------------------------------------
# TestBuildToolIndex
# ---------------------------------------------------------------------------
class TestBuildToolIndex:
    def test_finds_tool_use_ids(self):
        """Extracts tool_use IDs from assistant messages with tool_calls."""
        entries = [
            {"role": "user", "content": "Do something", "line_num": 1},
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": "call_abc123",
                        "type": "function",
                        "function": {"name": "read_file", "arguments": "{}"},
                    }
                ],
                "line_num": 2,
            },
        ]
        index = build_tool_index(entries)
        assert "call_abc123" in index["tool_uses"]
        assert index["tool_uses"]["call_abc123"]["line_num"] == 2
        assert index["tool_uses"]["call_abc123"]["tool_name"] == "read_file"
        assert index["tool_uses"]["call_abc123"]["entry_index"] == 1

    def test_finds_tool_result_ids(self):
        """Extracts tool_result IDs from tool role messages."""
        entries = [
            {
                "role": "tool",
                "tool_call_id": "call_abc123",
                "content": "file contents here",
                "line_num": 3,
            },
        ]
        index = build_tool_index(entries)
        assert "call_abc123" in index["tool_results"]
        assert index["tool_results"]["call_abc123"]["line_num"] == 3
        assert index["tool_results"]["call_abc123"]["entry_index"] == 0


# ---------------------------------------------------------------------------
# TestIsRealUserMessage
# ---------------------------------------------------------------------------
class TestIsRealUserMessage:
    def test_plain_user_message(self):
        """A plain user message with string content is real."""
        entry = {"role": "user", "content": "Hello, world!"}
        assert is_real_user_message(entry) is True

    def test_tool_result_is_not_real(self):
        """A user-role message with tool_call_id is not real (it's a tool result)."""
        entry = {
            "role": "user",
            "tool_call_id": "call_abc123",
            "content": "result data",
        }
        assert is_real_user_message(entry) is False

    def test_tool_role_is_not_real(self):
        """A tool role message is not real."""
        entry = {
            "role": "tool",
            "tool_call_id": "call_abc123",
            "content": "result data",
        }
        assert is_real_user_message(entry) is False

    def test_system_reminder_is_not_real(self):
        """A user message with string content wrapped in <system-reminder> is not real."""
        entry = {
            "role": "user",
            "content": "<system-reminder>You are helpful</system-reminder>",
        }
        assert is_real_user_message(entry) is False

    def test_system_reminder_in_list_content(self):
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

    def test_assistant_is_not_real(self):
        """An assistant message is not real."""
        entry = {"role": "assistant", "content": "I can help with that."}
        assert is_real_user_message(entry) is False


# ---------------------------------------------------------------------------
# TestDiagnose
# ---------------------------------------------------------------------------
class TestDiagnose:
    """Tests for diagnose — detects all 3 failure modes."""

    def test_healthy_transcript(self, tmp_path):
        """A well-formed transcript is diagnosed as healthy."""
        lines = [
            {"role": "user", "content": "Hi"},
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {"id": "tc1", "function": {"name": "bash", "arguments": "{}"}},
                ],
            },
            {"role": "tool", "tool_call_id": "tc1", "name": "bash", "content": "ok"},
            {"role": "assistant", "content": "Done"},
        ]
        session_dir = _write_transcript(tmp_path / "healthy", lines)
        result = diagnose(session_dir)

        assert result["status"] == "healthy"
        assert result["failure_modes"] == []
        assert result["orphaned_tool_ids"] == []
        assert result["misplaced_tool_ids"] == []
        assert result["incomplete_turns"] == []

    def test_detects_missing_tool_results(self, tmp_path):
        """Detects failure mode 1: tool_use with no matching tool_result."""
        lines = [
            {"role": "user", "content": "Hi"},
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {"id": "tc1", "function": {"name": "bash", "arguments": "{}"}},
                    {"id": "tc2", "function": {"name": "grep", "arguments": "{}"}},
                ],
            },
            # No tool results at all
        ]
        session_dir = _write_transcript(tmp_path / "orphan", lines)
        result = diagnose(session_dir)

        assert result["status"] == "broken"
        assert "missing_tool_results" in result["failure_modes"]
        assert set(result["orphaned_tool_ids"]) == {"tc1", "tc2"}

    def test_detects_ordering_violation(self, tmp_path):
        """Detects failure mode 2: tool_results exist but a real user message
        appears between the tool_use and its results."""
        lines = [
            {"role": "user", "content": "Hi"},
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {"id": "tc1", "function": {"name": "bash", "arguments": "{}"}},
                ],
            },
            # Real user message wedged in (shouldn't be here)
            {"role": "user", "content": "What happened?"},
            # Tool result appears AFTER the interrupting user message
            {"role": "tool", "tool_call_id": "tc1", "name": "bash", "content": "ok"},
            {"role": "assistant", "content": "Done"},
        ]
        session_dir = _write_transcript(tmp_path / "ordering", lines)
        result = diagnose(session_dir)

        assert result["status"] == "broken"
        assert "ordering_violation" in result["failure_modes"]
        assert "tc1" in result["misplaced_tool_ids"]

    def test_detects_incomplete_assistant_turn(self, tmp_path):
        """Detects failure mode 3: tool results present but no final
        assistant text response before the next real user message."""
        lines = [
            {"role": "user", "content": "Hi"},
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {"id": "tc1", "function": {"name": "bash", "arguments": "{}"}},
                ],
            },
            {"role": "tool", "tool_call_id": "tc1", "name": "bash", "content": "ok"},
            # Missing assistant response — next entry is a real user message
            {"role": "user", "content": "What happened?"},
        ]
        session_dir = _write_transcript(tmp_path / "incomplete", lines)
        result = diagnose(session_dir)

        assert result["status"] == "broken"
        assert "incomplete_assistant_turn" in result["failure_modes"]
        assert len(result["incomplete_turns"]) == 1
        assert result["incomplete_turns"][0]["missing"] == "assistant_response"

    def test_detects_multiple_failure_modes(self, tmp_path):
        """Detects all three failure modes in a single transcript.

        Structure:
        - Turn 1: normal (healthy baseline)
        - Turn 2: ordering violation (tc1 misplaced — FM3 skips this turn)
        - Turn 3: missing tool results (tc2 orphaned — FM3 skips this turn)
        - Turn 4: clean tool call but incomplete (tc3 has result, no assistant
          response at end of transcript — FM3 detects this)
        """
        lines = [
            # Turn 1: normal
            {"role": "user", "content": "Hi"},
            {"role": "assistant", "content": "Hello"},
            # Turn 2: ordering violation (tc1 is misplaced, so FM3 skips it)
            {"role": "user", "content": "Do stuff"},
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {"id": "tc1", "function": {"name": "bash", "arguments": "{}"}},
                ],
            },
            {"role": "user", "content": "Hmm?"},  # interrupting real user message
            {"role": "tool", "tool_call_id": "tc1", "name": "bash", "content": "ok"},
            # Turn 3: missing tool results (tc2 is orphaned)
            {"role": "user", "content": "More stuff"},
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {"id": "tc2", "function": {"name": "grep", "arguments": "{}"}},
                ],
            },
            # No tool result for tc2
            # Turn 4: clean tool call but incomplete (no assistant response after)
            {"role": "user", "content": "One more"},
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {"id": "tc3", "function": {"name": "bash", "arguments": "{}"}},
                ],
            },
            {"role": "tool", "tool_call_id": "tc3", "name": "bash", "content": "done"},
            # No assistant response — end of transcript → incomplete
        ]
        session_dir = _write_transcript(tmp_path / "multi", lines)
        result = diagnose(session_dir)

        assert result["status"] == "broken"
        assert "ordering_violation" in result["failure_modes"]
        assert "incomplete_assistant_turn" in result["failure_modes"]
        assert "missing_tool_results" in result["failure_modes"]
        # Only tc3's turn should be flagged incomplete; tc1's turn is skipped
        # because tc1 is misplaced, per spec's FM3 skip rule.
        assert len(result["incomplete_turns"]) == 1

    def test_no_tool_calls_is_healthy(self, tmp_path):
        """A transcript with no tool calls at all is healthy."""
        lines = [
            {"role": "user", "content": "Hi"},
            {"role": "assistant", "content": "Hello"},
        ]
        session_dir = _write_transcript(tmp_path / "notool", lines)
        result = diagnose(session_dir)
        assert result["status"] == "healthy"

    def test_recommended_action(self, tmp_path):
        """Broken transcripts get recommended_action='repair'; healthy get 'none'."""
        # Healthy
        lines_ok = [
            {"role": "user", "content": "Hi"},
            {"role": "assistant", "content": "Hello"},
        ]
        session_dir_ok = _write_transcript(tmp_path / "ok", lines_ok)
        assert diagnose(session_dir_ok)["recommended_action"] == "none"

        # Broken
        lines_bad = [
            {"role": "user", "content": "Hi"},
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {"id": "tc1", "function": {"name": "bash", "arguments": "{}"}},
                ],
            },
        ]
        session_dir_bad = _write_transcript(tmp_path / "bad", lines_bad)
        assert diagnose(session_dir_bad)["recommended_action"] == "repair"


# ---------------------------------------------------------------------------
# TestRepairTranscript
# ---------------------------------------------------------------------------
class TestRepairTranscript:
    """Tests for repair_transcript — COMPLETE strategy."""

    def test_healthy_transcript_unchanged(self):
        """Healthy transcript returned as-is, minus internal line_num keys."""
        entries = [
            {"role": "user", "content": "Hello", "line_num": 1},
            {"role": "assistant", "content": "Hi there", "line_num": 2},
        ]
        diagnosis = {
            "status": "healthy",
            "failure_modes": [],
            "orphaned_tool_ids": [],
            "misplaced_tool_ids": [],
            "incomplete_turns": [],
            "recommended_action": "none",
        }
        result = repair_transcript(entries, diagnosis)
        assert len(result) == 2
        assert result[0] == {"role": "user", "content": "Hello"}
        assert result[1] == {"role": "assistant", "content": "Hi there"}
        assert "line_num" not in result[0]
        assert "line_num" not in result[1]

    def test_injects_synthetic_tool_results_for_orphans(self):
        """Orphaned tool_calls get 2 synthetic tool results with 'error' in content."""
        entries = [
            {"role": "user", "content": "Do stuff", "line_num": 1},
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {
                        "id": "tc1",
                        "type": "function",
                        "function": {"name": "bash", "arguments": "{}"},
                    },
                    {
                        "id": "tc2",
                        "type": "function",
                        "function": {"name": "grep", "arguments": "{}"},
                    },
                ],
                "line_num": 2,
            },
        ]
        diagnosis = {
            "status": "broken",
            "failure_modes": ["missing_tool_results"],
            "orphaned_tool_ids": ["tc1", "tc2"],
            "misplaced_tool_ids": [],
            "incomplete_turns": [],
            "recommended_action": "repair",
        }
        result = repair_transcript(entries, diagnosis)
        tool_results = [e for e in result if e.get("role") == "tool"]
        assert len(tool_results) == 2
        for tr in tool_results:
            assert "error" in tr["content"]

    def test_injects_synthetic_assistant_response(self):
        """Synthetic assistant response contains 'repaired' or 'interrupted'."""
        entries = [
            {"role": "user", "content": "Do stuff", "line_num": 1},
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {
                        "id": "tc1",
                        "type": "function",
                        "function": {"name": "bash", "arguments": "{}"},
                    },
                ],
                "line_num": 2,
            },
            {"role": "user", "content": "Next question", "line_num": 3},
        ]
        diagnosis = {
            "status": "broken",
            "failure_modes": ["missing_tool_results"],
            "orphaned_tool_ids": ["tc1"],
            "misplaced_tool_ids": [],
            "incomplete_turns": [],
            "recommended_action": "repair",
        }
        result = repair_transcript(entries, diagnosis)
        # Find synthetic assistant messages (without tool_calls)
        synthetic_assistants = [
            e for e in result if e.get("role") == "assistant" and "tool_calls" not in e
        ]
        assert len(synthetic_assistants) >= 1
        content = synthetic_assistants[0].get("content", [])
        if isinstance(content, list):
            text = " ".join(block.get("text", "") for block in content)
        else:
            text = str(content)
        assert "repaired" in text.lower() or "interrupted" in text.lower()

    def test_removes_misplaced_results_and_injects_correct_ones(self):
        """Misplaced tool_result removed; synthetic injected in correct position."""
        entries = [
            {"role": "user", "content": "Hi", "line_num": 1},
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {
                        "id": "tc1",
                        "type": "function",
                        "function": {"name": "bash", "arguments": "{}"},
                    },
                ],
                "line_num": 2,
            },
            {"role": "user", "content": "What happened?", "line_num": 3},
            {
                "role": "tool",
                "tool_call_id": "tc1",
                "name": "bash",
                "content": "original_result",
                "line_num": 4,
            },
            {"role": "assistant", "content": "Done", "line_num": 5},
        ]
        diagnosis = {
            "status": "broken",
            "failure_modes": ["ordering_violation"],
            "orphaned_tool_ids": [],
            "misplaced_tool_ids": ["tc1"],
            "incomplete_turns": [],
            "recommended_action": "repair",
        }
        result = repair_transcript(entries, diagnosis)
        # Original misplaced result must be removed
        all_contents = [e.get("content", "") for e in result]
        assert "original_result" not in all_contents
        # Synthetic tool result in correct position
        tool_results = [e for e in result if e.get("role") == "tool"]
        assert len(tool_results) == 1
        assert "error" in tool_results[0]["content"]
        # Position check: tool result right after assistant with tool_calls
        for i, e in enumerate(result):
            if e.get("role") == "assistant" and "tool_calls" in e:
                assert result[i + 1].get("role") == "tool"
                break

    def test_repaired_transcript_passes_diagnosis(self, tmp_path):
        """Roundtrip: repair broken transcript, then re-diagnose as healthy."""
        broken_lines = [
            {"role": "user", "content": "Do stuff"},
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {
                        "id": "tc1",
                        "type": "function",
                        "function": {"name": "bash", "arguments": "{}"},
                    },
                    {
                        "id": "tc2",
                        "type": "function",
                        "function": {"name": "grep", "arguments": "{}"},
                    },
                ],
            },
        ]
        session_dir = _write_transcript(tmp_path / "broken", broken_lines)
        entries = parse_transcript(session_dir / "transcript.jsonl")
        diag = diagnose(session_dir)
        assert diag["status"] == "broken"

        repaired = repair_transcript(entries, diag)

        # Write repaired transcript and re-diagnose
        repaired_dir = _write_transcript(tmp_path / "repaired", repaired)
        new_diag = diagnose(repaired_dir)
        assert new_diag["status"] == "healthy"


# ---------------------------------------------------------------------------
# TestRewindTranscript
# ---------------------------------------------------------------------------
class TestRewindTranscript:
    """Tests for rewind_transcript — truncates before problematic turn."""

    def test_rewinds_before_first_issue(self):
        """Keeps Turn 1 (healthy), removes Turn 2 with orphaned tool."""
        entries = [
            # Turn 1: healthy
            {"role": "user", "content": "Hello", "line_num": 1},
            {"role": "assistant", "content": "Hi there", "line_num": 2},
            # Turn 2: user + assistant with orphaned tool call
            {"role": "user", "content": "Do stuff", "line_num": 3},
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {
                        "id": "tc1",
                        "type": "function",
                        "function": {"name": "bash", "arguments": "{}"},
                    },
                ],
                "line_num": 4,
            },
            # No tool result for tc1 — orphaned
        ]
        diagnosis = {
            "status": "broken",
            "failure_modes": ["missing_tool_results"],
            "orphaned_tool_ids": ["tc1"],
            "misplaced_tool_ids": [],
            "incomplete_turns": [],
            "recommended_action": "repair",
        }
        result = rewind_transcript(entries, diagnosis)
        # Should keep only Turn 1 (entries before the "Do stuff" user message)
        assert len(result) == 2
        assert result[0] == {"role": "user", "content": "Hello"}
        assert result[1] == {"role": "assistant", "content": "Hi there"}
        # No line_num in output
        for entry in result:
            assert "line_num" not in entry

    def test_rewind_healthy_returns_all(self):
        """Healthy transcript returned in full, stripped of line_num."""
        entries = [
            {"role": "user", "content": "Hello", "line_num": 1},
            {"role": "assistant", "content": "Hi there", "line_num": 2},
        ]
        diagnosis = {
            "status": "healthy",
            "failure_modes": [],
            "orphaned_tool_ids": [],
            "misplaced_tool_ids": [],
            "incomplete_turns": [],
            "recommended_action": "none",
        }
        result = rewind_transcript(entries, diagnosis)
        assert len(result) == 2
        assert result[0] == {"role": "user", "content": "Hello"}
        assert result[1] == {"role": "assistant", "content": "Hi there"}
        for entry in result:
            assert "line_num" not in entry

    def test_rewind_first_turn_broken_returns_empty(self):
        """If the first turn is broken, return empty list."""
        entries = [
            {"role": "user", "content": "Hello", "line_num": 1},
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {
                        "id": "tc1",
                        "type": "function",
                        "function": {"name": "bash", "arguments": "{}"},
                    },
                ],
                "line_num": 2,
            },
            # No tool result — orphaned, and no healthy turn before
        ]
        diagnosis = {
            "status": "broken",
            "failure_modes": ["missing_tool_results"],
            "orphaned_tool_ids": ["tc1"],
            "misplaced_tool_ids": [],
            "incomplete_turns": [],
            "recommended_action": "repair",
        }
        result = rewind_transcript(entries, diagnosis)
        assert result == []


# ---------------------------------------------------------------------------
# TestCLI
# ---------------------------------------------------------------------------


def _run_cli(*args: str) -> subprocess.CompletedProcess:
    """Run session-repair.py as a subprocess with the given arguments."""
    return subprocess.run(
        [sys.executable, str(_script_path), *args],
        capture_output=True,
        text=True,
        timeout=10,
    )


class TestCLI:
    """Subprocess-based CLI tests for session-repair.py."""

    def test_diagnose_healthy(self, tmp_path):
        """--diagnose on a healthy session exits 0 with JSON status=healthy."""
        lines = [
            {"role": "user", "content": "Hi"},
            {"role": "assistant", "content": "Hello"},
        ]
        _write_transcript(tmp_path, lines)
        result = _run_cli(str(tmp_path), "--diagnose")
        assert result.returncode == 0
        data = json.loads(result.stdout)
        assert data["status"] == "healthy"

    def test_diagnose_broken_exits_1(self, tmp_path):
        """--diagnose on a broken session exits 1."""
        lines = [
            {"role": "user", "content": "Hi"},
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {"id": "tc1", "function": {"name": "bash", "arguments": "{}"}},
                ],
            },
        ]
        _write_transcript(tmp_path, lines)
        result = _run_cli(str(tmp_path), "--diagnose")
        assert result.returncode == 1
        data = json.loads(result.stdout)
        assert data["status"] == "broken"

    def test_repair_creates_backup(self, tmp_path):
        """--repair creates a timestamped backup of transcript.jsonl."""
        lines = [
            {"role": "user", "content": "Hi"},
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {"id": "tc1", "function": {"name": "bash", "arguments": "{}"}},
                ],
            },
        ]
        _write_transcript(tmp_path, lines)
        _run_cli(str(tmp_path), "--repair")
        backups = list(tmp_path.glob("transcript.jsonl.bak-pre-repair-*"))
        assert len(backups) == 1

    def test_repair_writes_valid_transcript(self, tmp_path):
        """After --repair, re-diagnosing the session shows healthy."""
        lines = [
            {"role": "user", "content": "Hi"},
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {"id": "tc1", "function": {"name": "bash", "arguments": "{}"}},
                ],
            },
        ]
        _write_transcript(tmp_path, lines)
        repair_result = _run_cli(str(tmp_path), "--repair")
        assert repair_result.returncode == 0
        # Re-diagnose should show healthy
        diag_result = _run_cli(str(tmp_path), "--diagnose")
        assert diag_result.returncode == 0
        data = json.loads(diag_result.stdout)
        assert data["status"] == "healthy"

    def test_rewind_creates_backups(self, tmp_path):
        """--rewind creates backups for both transcript.jsonl and events.jsonl."""
        lines = [
            {"role": "user", "content": "Hello"},
            {"role": "assistant", "content": "Hi there"},
            {"role": "user", "content": "Do stuff"},
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {"id": "tc1", "function": {"name": "bash", "arguments": "{}"}},
                ],
            },
        ]
        _write_transcript(tmp_path, lines)
        # Also create an events.jsonl file
        events_path = tmp_path / "events.jsonl"
        events_path.write_text(
            '{"event": "one"}\n{"event": "two"}\n{"event": "three"}\n{"event": "four"}\n',
            encoding="utf-8",
        )
        _run_cli(str(tmp_path), "--rewind")
        transcript_backups = list(tmp_path.glob("transcript.jsonl.bak-pre-rewind-*"))
        events_backups = list(tmp_path.glob("events.jsonl.bak-pre-rewind-*"))
        assert len(transcript_backups) == 1
        assert len(events_backups) == 1

    def test_invalid_args_exits_2(self):
        """Running with no arguments exits with code 2."""
        result = _run_cli()
        assert result.returncode == 2

    def test_no_action_flag_exits_2(self, tmp_path):
        """Running with session dir but no action flag exits 2."""
        _write_transcript(tmp_path, [{"role": "user", "content": "Hi"}])
        result = _run_cli(str(tmp_path))
        assert result.returncode == 2


# ---------------------------------------------------------------------------
# TestIntegrationRealisticFailures
# ---------------------------------------------------------------------------


def _tc(tc_id: str, name: str) -> dict:
    """Shorthand: build a tool_call dict."""
    return {
        "id": tc_id,
        "type": "function",
        "function": {"name": name, "arguments": "{}"},
    }


def _tool_result(tc_id: str, name: str, content: str = "ok") -> dict:
    """Shorthand: build a tool-role result dict."""
    return {"role": "tool", "tool_call_id": tc_id, "name": name, "content": content}


class TestIntegrationRealisticFailures:
    """Integration tests exercising realistic combined-failure transcripts.

    Each test builds a transcript mimicking real-world corruption patterns,
    runs the full diagnose → repair → verify cycle, and asserts the repaired
    transcript is healthy.
    """

    # -- helpers ----------------------------------------------------------

    def _diagnose_repair_verify(self, tmp_path, lines):
        """Write *lines*, diagnose, repair, re-diagnose. Return all artefacts."""
        session_dir = _write_transcript(tmp_path, lines)
        diag = diagnose(session_dir)
        entries = parse_transcript(session_dir / "transcript.jsonl")
        repaired = repair_transcript(entries, diag)
        repaired_dir = _write_transcript(tmp_path / "repaired", repaired)
        verify = diagnose(repaired_dir)
        return diag, repaired, verify

    # -- test: mode 1 — simple orphans ------------------------------------

    def test_mode1_simple_orphans(self, tmp_path):
        """3 orphaned tool calls (toolu_01, toolu_02, toolu_03), no results.

        Verify diagnose detects all 3, repair produces healthy transcript.
        """
        lines = [
            {"role": "user", "content": "Run three tools please"},
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    _tc("toolu_01", "bash"),
                    _tc("toolu_02", "grep"),
                    _tc("toolu_03", "glob"),
                ],
            },
            # No tool results at all — all 3 are orphaned
        ]

        diag, repaired, verify = self._diagnose_repair_verify(tmp_path, lines)

        # Diagnosis: broken with all 3 orphaned
        assert diag["status"] == "broken"
        assert "missing_tool_results" in diag["failure_modes"]
        assert set(diag["orphaned_tool_ids"]) == {"toolu_01", "toolu_02", "toolu_03"}

        # Repaired transcript is healthy
        assert verify["status"] == "healthy"

    # -- test: mode 2 — user wedged between tool_use and results ----------

    def test_mode2_user_wedged_between(self, tmp_path):
        """User message wedged between tool_use (toolu_10, toolu_11) and
        their late-arriving results. Verify ordering_violation detected,
        repair produces healthy.
        """
        lines = [
            {"role": "user", "content": "Do two things"},
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [_tc("toolu_10", "bash"), _tc("toolu_11", "read_file")],
            },
            # Real user message wedged in before results arrive
            {"role": "user", "content": "What is taking so long?"},
            # Late-arriving results
            _tool_result("toolu_10", "bash", "output from bash"),
            _tool_result("toolu_11", "read_file", "file contents"),
            {"role": "assistant", "content": "Done with both"},
        ]

        diag, repaired, verify = self._diagnose_repair_verify(tmp_path, lines)

        # Diagnosis: ordering violation
        assert diag["status"] == "broken"
        assert "ordering_violation" in diag["failure_modes"]
        assert set(diag["misplaced_tool_ids"]) == {"toolu_10", "toolu_11"}

        # Repaired transcript is healthy
        assert verify["status"] == "healthy"

    # -- test: mode 3 — missing assistant response after tool results -----

    def test_mode3_missing_assistant_response(self, tmp_path):
        """Tool result present (toolu_20) but no assistant response before
        next user message. Verify incomplete_assistant_turn detected, repair
        produces healthy.
        """
        lines = [
            {"role": "user", "content": "Check something"},
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [_tc("toolu_20", "bash")],
            },
            _tool_result("toolu_20", "bash", "checked"),
            # No assistant response — next entry is a real user message
            {"role": "user", "content": "Now do something else"},
            {"role": "assistant", "content": "Sure, what?"},
        ]

        diag, repaired, verify = self._diagnose_repair_verify(tmp_path, lines)

        # Diagnosis: incomplete assistant turn
        assert diag["status"] == "broken"
        assert "incomplete_assistant_turn" in diag["failure_modes"]
        assert len(diag["incomplete_turns"]) == 1
        assert diag["incomplete_turns"][0]["missing"] == "assistant_response"

        # Repaired transcript is healthy
        assert verify["status"] == "healthy"

    # -- test: combined — mimics real 4f63147f session --------------------

    def test_combined_all_three_modes(self, tmp_path):
        """Mimics the real 4f63147f session with all three failure modes.

        Turn 1: healthy baseline (user + assistant text reply).
        Turn 2: ordering violation + incomplete — assistant calls toolu_A1
                 and toolu_A2, a real user message wedges in, then late
                 results arrive (but no closing assistant response).
        Turn 3: orphaned — assistant calls toolu_B1 and toolu_B2 but no
                 results ever arrive.
        """
        lines = [
            # --- Turn 1: healthy ---
            {"role": "user", "content": "Hello"},
            {"role": "assistant", "content": "Hi! How can I help?"},
            # --- Turn 2: ordering violation + incomplete ---
            {"role": "user", "content": "Explore the codebase"},
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    _tc("toolu_A1", "glob"),
                    _tc("toolu_A2", "grep"),
                ],
            },
            # Wedged user message (ordering violation)
            {"role": "user", "content": "Actually, focus on tests"},
            # Late results (misplaced — after the wedged user message)
            _tool_result("toolu_A1", "glob", "file1.py\nfile2.py"),
            _tool_result("toolu_A2", "grep", "match found"),
            # No assistant response after results → incomplete turn
            # (but FM3 skips this turn because toolu_A1/A2 are misplaced)
            # --- Turn 3: orphaned ---
            {"role": "user", "content": "Now run the build"},
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    _tc("toolu_B1", "bash"),
                    _tc("toolu_B2", "read_file"),
                ],
            },
            # No results at all — orphaned
        ]

        diag, repaired, verify = self._diagnose_repair_verify(tmp_path, lines)

        # All three modes detected
        assert diag["status"] == "broken"
        assert "ordering_violation" in diag["failure_modes"]
        assert "missing_tool_results" in diag["failure_modes"]
        assert set(diag["misplaced_tool_ids"]) == {"toolu_A1", "toolu_A2"}
        assert set(diag["orphaned_tool_ids"]) == {"toolu_B1", "toolu_B2"}
        # FM3 (incomplete_assistant_turn) is NOT reported because the turn with
        # missing assistant response also contains misplaced tools, so FM2
        # already covers it and FM3 skips.
        assert "incomplete_assistant_turn" not in diag["failure_modes"], (
            "FM3 should be skipped when FM2 covers the turn"
        )

        # Repaired transcript is healthy
        assert verify["status"] == "healthy"

    # -- test: full CLI roundtrip -----------------------------------------

    def test_full_cli_roundtrip(self, tmp_path):
        """Subprocess-based: diagnose (exit 1) → repair (exit 0) →
        diagnose (exit 0, status=healthy).
        """
        # Build a broken transcript with all 3 modes
        lines = [
            {"role": "user", "content": "Hello"},
            {"role": "assistant", "content": "Hi!"},
            # Ordering violation
            {"role": "user", "content": "Do stuff"},
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [_tc("tc_ov", "bash")],
            },
            {"role": "user", "content": "Interruption"},
            _tool_result("tc_ov", "bash", "late result"),
            # Orphaned
            {"role": "user", "content": "More stuff"},
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [_tc("tc_orph", "grep")],
            },
        ]
        _write_transcript(tmp_path, lines)

        # Step 1: diagnose → exit 1 (broken)
        r1 = _run_cli(str(tmp_path), "--diagnose")
        assert r1.returncode == 1
        d1 = json.loads(r1.stdout)
        assert d1["status"] == "broken"

        # Step 2: repair → exit 0
        r2 = _run_cli(str(tmp_path), "--repair")
        assert r2.returncode == 0

        # Step 3: diagnose again → exit 0, healthy
        r3 = _run_cli(str(tmp_path), "--diagnose")
        assert r3.returncode == 0
        d3 = json.loads(r3.stdout)
        assert d3["status"] == "healthy"
