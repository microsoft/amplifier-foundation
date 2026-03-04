"""Tests for scripts/session-repair.py transcript repair tool."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

# The script is stdlib-only and lives outside the package, so import via path manipulation.
import importlib.util

_SCRIPT_PATH = Path(__file__).resolve().parent.parent / "scripts" / "session-repair.py"


def _load_module():
    """Load session-repair.py as a module for testing."""
    spec = importlib.util.spec_from_file_location("session_repair", _SCRIPT_PATH)
    assert spec is not None, f"Could not load spec from {_SCRIPT_PATH}"
    assert spec.loader is not None, "spec.loader is None"
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def sr():
    """The session_repair module, loaded once per test module."""
    return _load_module()


# ---------------------------------------------------------------------------
# Helpers to build transcript fixtures
# ---------------------------------------------------------------------------


def _write_transcript(session_dir: Path, lines: list[dict]) -> Path:
    """Write a list of dicts as transcript.jsonl and return the session dir."""
    session_dir.mkdir(parents=True, exist_ok=True)
    transcript = session_dir / "transcript.jsonl"
    transcript.write_text(
        "\n".join(json.dumps(line) for line in lines) + "\n", encoding="utf-8"
    )
    return session_dir


# ===================================================================
# Task 1: Core data model + transcript parser
# ===================================================================


class TestParseTranscript:
    """Tests for parse_transcript — reads JSONL and builds an indexed list."""

    def test_parses_simple_transcript(self, sr, tmp_path):
        """A 2-turn conversation parses to a list of entries with correct roles."""
        lines = [
            {"role": "user", "content": "Hello"},
            {"role": "assistant", "content": "Hi there"},
        ]
        session_dir = _write_transcript(tmp_path / "sess1", lines)
        entries = sr.parse_transcript(session_dir / "transcript.jsonl")

        assert len(entries) == 2
        assert entries[0]["role"] == "user"
        assert entries[1]["role"] == "assistant"

    def test_entries_have_line_numbers(self, sr, tmp_path):
        """Each parsed entry has a 'line_num' key (1-based)."""
        lines = [
            {"role": "user", "content": "Hello"},
            {"role": "assistant", "content": "Hi"},
            {"role": "user", "content": "Bye"},
        ]
        session_dir = _write_transcript(tmp_path / "sess2", lines)
        entries = sr.parse_transcript(session_dir / "transcript.jsonl")

        assert entries[0]["line_num"] == 1
        assert entries[1]["line_num"] == 2
        assert entries[2]["line_num"] == 3

    def test_empty_transcript(self, sr, tmp_path):
        """An empty transcript returns an empty list."""
        session_dir = tmp_path / "sess3"
        session_dir.mkdir(parents=True)
        (session_dir / "transcript.jsonl").write_text("", encoding="utf-8")
        entries = sr.parse_transcript(session_dir / "transcript.jsonl")
        assert entries == []


class TestBuildToolIndex:
    """Tests for build_tool_index — maps tool_use IDs and tool_result IDs."""

    def test_finds_tool_use_ids(self, sr, tmp_path):
        """Extracts tool_use IDs from assistant messages with tool_calls."""
        lines = [
            {"role": "user", "content": "Do stuff"},
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {
                        "id": "toolu_abc",
                        "function": {"name": "bash", "arguments": "{}"},
                    },
                    {
                        "id": "toolu_def",
                        "function": {"name": "grep", "arguments": "{}"},
                    },
                ],
            },
            {
                "role": "tool",
                "tool_call_id": "toolu_abc",
                "name": "bash",
                "content": "ok",
            },
            {
                "role": "tool",
                "tool_call_id": "toolu_def",
                "name": "grep",
                "content": "ok",
            },
            {"role": "assistant", "content": "Done"},
        ]
        session_dir = _write_transcript(tmp_path / "sess_idx", lines)
        entries = sr.parse_transcript(session_dir / "transcript.jsonl")
        index = sr.build_tool_index(entries)

        assert "toolu_abc" in index["tool_uses"]
        assert "toolu_def" in index["tool_uses"]
        assert index["tool_uses"]["toolu_abc"]["line_num"] == 2
        assert index["tool_uses"]["toolu_abc"]["tool_name"] == "bash"

    def test_finds_tool_result_ids(self, sr, tmp_path):
        """Extracts tool_result IDs from tool role messages."""
        lines = [
            {"role": "user", "content": "Do stuff"},
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {
                        "id": "toolu_abc",
                        "function": {"name": "bash", "arguments": "{}"},
                    },
                ],
            },
            {
                "role": "tool",
                "tool_call_id": "toolu_abc",
                "name": "bash",
                "content": "ok",
            },
            {"role": "assistant", "content": "Done"},
        ]
        session_dir = _write_transcript(tmp_path / "sess_idx2", lines)
        entries = sr.parse_transcript(session_dir / "transcript.jsonl")
        index = sr.build_tool_index(entries)

        assert "toolu_abc" in index["tool_results"]
        assert index["tool_results"]["toolu_abc"]["line_num"] == 3


class TestIsRealUserMessage:
    """Tests for is_real_user_message — distinguishes human input from infra."""

    def test_plain_user_message(self, sr):
        """A normal user message with string content is real."""
        entry = {"role": "user", "content": "Hello world"}
        assert sr.is_real_user_message(entry) is True

    def test_tool_result_is_not_real(self, sr):
        """A role:user message with tool_call_id is NOT a real user message."""
        entry = {"role": "user", "tool_call_id": "toolu_123", "content": "result"}
        assert sr.is_real_user_message(entry) is False

    def test_tool_role_is_not_real(self, sr):
        """A role:tool message is NOT a real user message."""
        entry = {"role": "tool", "tool_call_id": "toolu_123", "content": "result"}
        assert sr.is_real_user_message(entry) is False

    def test_system_reminder_is_not_real(self, sr):
        """A role:user message wrapped in system-reminder tags is NOT real."""
        entry = {
            "role": "user",
            "content": "<system-reminder>Hook output</system-reminder>",
        }
        assert sr.is_real_user_message(entry) is False

    def test_system_reminder_in_list_content(self, sr):
        """A role:user message with list content containing system-reminder is NOT real."""
        entry = {
            "role": "user",
            "content": [
                {
                    "type": "text",
                    "text": "<system-reminder>Hook output</system-reminder>",
                }
            ],
        }
        assert sr.is_real_user_message(entry) is False

    def test_assistant_is_not_real(self, sr):
        """An assistant message is NOT a real user message."""
        entry = {"role": "assistant", "content": "Hi"}
        assert sr.is_real_user_message(entry) is False
