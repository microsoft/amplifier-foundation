"""Tests for session fork, slice, and events utilities."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from amplifier_foundation.session import (
    ForkResult,
    add_synthetic_tool_results,
    count_events,
    count_turns,
    find_orphaned_tool_calls,
    fork_session,
    fork_session_in_memory,
    get_event_summary,
    get_fork_preview,
    get_last_timestamp_for_turn,
    get_session_lineage,
    get_turn_boundaries,
    get_turn_summary,
    list_session_forks,
    slice_events_for_fork,
    slice_events_to_timestamp,
    slice_to_turn,
)


# =============================================================================
# Test fixtures
# =============================================================================


@pytest.fixture
def simple_messages():
    """Simple 3-turn conversation."""
    return [
        {"role": "user", "content": "Turn 1 question"},
        {"role": "assistant", "content": "Turn 1 answer"},
        {"role": "user", "content": "Turn 2 question"},
        {"role": "assistant", "content": "Turn 2 answer"},
        {"role": "user", "content": "Turn 3 question"},
        {"role": "assistant", "content": "Turn 3 answer"},
    ]


@pytest.fixture
def messages_with_tools():
    """Conversation with tool calls."""
    return [
        {"role": "user", "content": "List files"},
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {"id": "call_1", "function": {"name": "ls", "arguments": "{}"}},
                {"id": "call_2", "function": {"name": "pwd", "arguments": "{}"}},
            ],
        },
        {"role": "tool", "tool_call_id": "call_1", "content": "file1.txt\nfile2.txt"},
        {"role": "tool", "tool_call_id": "call_2", "content": "/home/user"},
        {"role": "assistant", "content": "Found 2 files in /home/user"},
        {"role": "user", "content": "Show file1.txt"},
        {"role": "assistant", "content": "Contents of file1.txt..."},
    ]


@pytest.fixture
def messages_with_orphaned_tool():
    """Conversation with orphaned tool call at the end."""
    return [
        {"role": "user", "content": "List files"},
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {"id": "call_1", "function": {"name": "ls", "arguments": "{}"}},
            ],
        },
        {"role": "tool", "tool_call_id": "call_1", "content": "file1.txt"},
        {"role": "assistant", "content": "Found file1.txt"},
        {"role": "user", "content": "Read file1.txt"},
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {"id": "call_orphan", "function": {"name": "read", "arguments": "{}"}},
            ],
        },
        # No tool result for call_orphan - it's orphaned
    ]


@pytest.fixture
def sample_session(tmp_path):
    """Create a sample session directory with transcript and metadata."""
    session_dir = tmp_path / "sessions" / "parent_session_123"
    session_dir.mkdir(parents=True)

    messages = [
        {"role": "user", "content": "Turn 1", "timestamp": "2026-01-05T10:00:00Z"},
        {"role": "assistant", "content": "Response 1", "timestamp": "2026-01-05T10:00:05Z"},
        {"role": "user", "content": "Turn 2", "timestamp": "2026-01-05T10:01:00Z"},
        {"role": "assistant", "content": "Response 2", "timestamp": "2026-01-05T10:01:10Z"},
        {"role": "user", "content": "Turn 3", "timestamp": "2026-01-05T10:02:00Z"},
        {"role": "assistant", "content": "Response 3", "timestamp": "2026-01-05T10:02:15Z"},
    ]

    # Write transcript
    with open(session_dir / "transcript.jsonl", "w") as f:
        for msg in messages:
            f.write(json.dumps(msg) + "\n")

    # Write metadata
    metadata = {
        "session_id": "parent_session_123",
        "profile": "bundle:foundation",
        "model": "claude-sonnet-4-20250514",
        "created": "2026-01-05T10:00:00Z",
        "turn_count": 3,
    }
    (session_dir / "metadata.json").write_text(json.dumps(metadata, indent=2))

    # Write events
    events = [
        {"event": "session:start", "ts": "2026-01-05T10:00:00Z", "session_id": "parent_session_123"},
        {"event": "prompt:submit", "ts": "2026-01-05T10:00:00Z"},
        {"event": "llm:request", "ts": "2026-01-05T10:00:01Z"},
        {"event": "llm:response", "ts": "2026-01-05T10:00:05Z"},
        {"event": "prompt:complete", "ts": "2026-01-05T10:00:05Z"},
        {"event": "prompt:submit", "ts": "2026-01-05T10:01:00Z"},
        {"event": "llm:request", "ts": "2026-01-05T10:01:01Z"},
        {"event": "llm:response", "ts": "2026-01-05T10:01:10Z"},
        {"event": "prompt:complete", "ts": "2026-01-05T10:01:10Z"},
        {"event": "prompt:submit", "ts": "2026-01-05T10:02:00Z"},
        {"event": "llm:request", "ts": "2026-01-05T10:02:01Z"},
        {"event": "llm:response", "ts": "2026-01-05T10:02:15Z"},
        {"event": "prompt:complete", "ts": "2026-01-05T10:02:15Z"},
    ]
    with open(session_dir / "events.jsonl", "w") as f:
        for event in events:
            f.write(json.dumps(event) + "\n")

    return session_dir


# =============================================================================
# Tests for slice.py
# =============================================================================


class TestGetTurnBoundaries:
    def test_empty_messages(self):
        assert get_turn_boundaries([]) == []

    def test_single_turn(self):
        messages = [
            {"role": "user", "content": "Hello"},
            {"role": "assistant", "content": "Hi there"},
        ]
        assert get_turn_boundaries(messages) == [0]

    def test_multiple_turns(self, simple_messages):
        assert get_turn_boundaries(simple_messages) == [0, 2, 4]

    def test_only_user_messages(self):
        messages = [
            {"role": "user", "content": "Q1"},
            {"role": "user", "content": "Q2"},
            {"role": "user", "content": "Q3"},
        ]
        assert get_turn_boundaries(messages) == [0, 1, 2]


class TestCountTurns:
    def test_empty(self):
        assert count_turns([]) == 0

    def test_simple(self, simple_messages):
        assert count_turns(simple_messages) == 3

    def test_with_tools(self, messages_with_tools):
        assert count_turns(messages_with_tools) == 2


class TestSliceToTurn:
    def test_slice_to_turn_1(self, simple_messages):
        sliced = slice_to_turn(simple_messages, 1)
        assert len(sliced) == 2
        assert sliced[0]["content"] == "Turn 1 question"
        assert sliced[1]["content"] == "Turn 1 answer"

    def test_slice_to_turn_2(self, simple_messages):
        sliced = slice_to_turn(simple_messages, 2)
        assert len(sliced) == 4
        assert sliced[2]["content"] == "Turn 2 question"
        assert sliced[3]["content"] == "Turn 2 answer"

    def test_slice_to_last_turn(self, simple_messages):
        sliced = slice_to_turn(simple_messages, 3)
        assert len(sliced) == 6
        assert sliced == simple_messages

    def test_invalid_turn_zero(self, simple_messages):
        with pytest.raises(ValueError, match="Turn must be >= 1"):
            slice_to_turn(simple_messages, 0)

    def test_invalid_turn_exceeds_max(self, simple_messages):
        with pytest.raises(ValueError, match="exceeds max turns"):
            slice_to_turn(simple_messages, 10)

    def test_with_tool_messages(self, messages_with_tools):
        # Turn 1 includes user, assistant with tool_calls, 2 tool results, final assistant
        sliced = slice_to_turn(messages_with_tools, 1)
        assert len(sliced) == 5
        assert sliced[0]["role"] == "user"
        assert sliced[4]["role"] == "assistant"

    def test_empty_messages(self):
        with pytest.raises(ValueError, match="No user messages"):
            slice_to_turn([], 1)


class TestFindOrphanedToolCalls:
    def test_no_tool_calls(self, simple_messages):
        assert find_orphaned_tool_calls(simple_messages) == []

    def test_paired_tool_calls(self, messages_with_tools):
        # All tool calls have results
        assert find_orphaned_tool_calls(messages_with_tools) == []

    def test_orphaned_tool_call(self, messages_with_orphaned_tool):
        orphaned = find_orphaned_tool_calls(messages_with_orphaned_tool)
        assert orphaned == ["call_orphan"]

    def test_multiple_orphaned(self):
        messages = [
            {"role": "user", "content": "Do stuff"},
            {
                "role": "assistant",
                "tool_calls": [
                    {"id": "a", "function": {}},
                    {"id": "b", "function": {}},
                    {"id": "c", "function": {}},
                ],
            },
            {"role": "tool", "tool_call_id": "a", "content": "result a"},
            # b and c are orphaned
        ]
        orphaned = find_orphaned_tool_calls(messages)
        assert set(orphaned) == {"b", "c"}


class TestAddSyntheticToolResults:
    def test_no_orphans(self, simple_messages):
        result = add_synthetic_tool_results(simple_messages, [])
        assert result == simple_messages

    def test_adds_synthetic_result(self):
        messages = [{"role": "user", "content": "Hi"}]
        result = add_synthetic_tool_results(messages, ["call_123"])
        assert len(result) == 2
        assert result[1]["role"] == "tool"
        assert result[1]["tool_call_id"] == "call_123"
        assert "forked" in result[1]["content"]

    def test_multiple_orphans(self):
        messages = [{"role": "user", "content": "Hi"}]
        result = add_synthetic_tool_results(messages, ["a", "b", "c"])
        assert len(result) == 4
        tool_ids = {m["tool_call_id"] for m in result if m.get("role") == "tool"}
        assert tool_ids == {"a", "b", "c"}


class TestSliceToTurnWithOrphanedTools:
    def test_complete_orphaned_tools(self, messages_with_orphaned_tool):
        # Fork at turn 2 which has orphaned tool
        sliced = slice_to_turn(messages_with_orphaned_tool, 2, handle_orphaned_tools="complete")
        # Should have added synthetic result
        tool_results = [m for m in sliced if m.get("role") == "tool"]
        assert any("forked" in m.get("content", "") for m in tool_results)

    def test_error_on_orphaned_tools(self, messages_with_orphaned_tool):
        with pytest.raises(ValueError, match="Orphaned tool calls"):
            slice_to_turn(messages_with_orphaned_tool, 2, handle_orphaned_tools="error")


class TestGetTurnSummary:
    def test_basic_summary(self, simple_messages):
        summary = get_turn_summary(simple_messages, 1)
        assert summary["turn"] == 1
        assert "Turn 1 question" in summary["user_content"]
        assert "Turn 1 answer" in summary["assistant_content"]
        assert summary["message_count"] == 2
        assert summary["tool_count"] == 0

    def test_with_tools(self, messages_with_tools):
        summary = get_turn_summary(messages_with_tools, 1)
        assert summary["tool_count"] == 2

    def test_invalid_turn(self, simple_messages):
        with pytest.raises(ValueError):
            get_turn_summary(simple_messages, 10)


# =============================================================================
# Tests for fork.py
# =============================================================================


class TestForkSession:
    def test_fork_at_turn_1(self, sample_session, tmp_path):
        result = fork_session(sample_session, turn=1)

        assert result.forked_from_turn == 1
        assert result.parent_id == "parent_session_123"
        assert result.message_count == 2
        assert result.session_dir.exists()
        assert result.session_dir != sample_session

        # Verify transcript
        transcript = (result.session_dir / "transcript.jsonl").read_text()
        assert "Turn 1" in transcript
        assert "Turn 2" not in transcript

    def test_fork_at_turn_2(self, sample_session):
        result = fork_session(sample_session, turn=2)

        assert result.forked_from_turn == 2
        assert result.message_count == 4

        transcript = (result.session_dir / "transcript.jsonl").read_text()
        assert "Turn 2" in transcript
        assert "Turn 3" not in transcript

    def test_fork_default_is_latest(self, sample_session):
        result = fork_session(sample_session)

        assert result.forked_from_turn == 3
        assert result.message_count == 6

    def test_fork_preserves_lineage(self, sample_session):
        result = fork_session(sample_session, turn=2)

        metadata = json.loads((result.session_dir / "metadata.json").read_text())
        assert metadata["parent_id"] == "parent_session_123"
        assert metadata["forked_from_turn"] == 2
        assert "forked_at" in metadata

    def test_fork_preserves_parent_metadata(self, sample_session):
        result = fork_session(sample_session, turn=1)

        metadata = json.loads((result.session_dir / "metadata.json").read_text())
        assert metadata["profile"] == "bundle:foundation"
        assert metadata["model"] == "claude-sonnet-4-20250514"

    def test_fork_with_custom_id(self, sample_session):
        result = fork_session(sample_session, turn=1, new_session_id="my-custom-fork")

        assert result.session_id == "my-custom-fork"
        assert result.session_dir.name == "my-custom-fork"

    def test_fork_with_custom_target_dir(self, sample_session, tmp_path):
        target = tmp_path / "custom_forks"
        result = fork_session(sample_session, turn=1, target_dir=target)

        assert result.session_dir.parent == target

    def test_fork_includes_events(self, sample_session):
        result = fork_session(sample_session, turn=2, include_events=True)

        events_path = result.session_dir / "events.jsonl"
        assert events_path.exists()
        assert result.events_count > 0

    def test_fork_nonexistent_session(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            fork_session(tmp_path / "nonexistent")

    def test_fork_invalid_turn(self, sample_session):
        with pytest.raises(ValueError, match="out of range"):
            fork_session(sample_session, turn=10)


class TestForkSessionInMemory:
    def test_basic_fork(self, simple_messages):
        result = fork_session_in_memory(simple_messages, turn=1, parent_id="parent_1")

        assert result.forked_from_turn == 1
        assert result.parent_id == "parent_1"
        assert result.message_count == 2
        assert result.messages is not None
        assert result.session_dir is None

    def test_fork_all_turns(self, simple_messages):
        result = fork_session_in_memory(simple_messages)

        assert result.forked_from_turn == 3
        assert result.message_count == 6
        assert result.messages == simple_messages

    def test_handles_orphaned_tools(self, messages_with_orphaned_tool):
        result = fork_session_in_memory(messages_with_orphaned_tool, turn=2)

        # Should have added synthetic result
        assert any(
            "forked" in m.get("content", "")
            for m in result.messages
            if m.get("role") == "tool"
        )

    def test_empty_messages(self):
        result = fork_session_in_memory([])

        assert result.forked_from_turn == 0
        assert result.message_count == 0
        assert result.messages == []


class TestGetForkPreview:
    def test_basic_preview(self, sample_session):
        preview = get_fork_preview(sample_session, turn=2)

        assert preview["parent_id"] == "parent_session_123"
        assert preview["turn"] == 2
        assert preview["max_turns"] == 3
        assert preview["message_count"] == 4
        assert not preview["has_orphaned_tools"]

    def test_preview_invalid_turn(self, sample_session):
        with pytest.raises(ValueError):
            get_fork_preview(sample_session, turn=10)


class TestListSessionForks:
    def test_no_forks(self, sample_session):
        forks = list_session_forks(sample_session)
        assert forks == []

    def test_finds_forks(self, sample_session):
        # Create a fork
        result = fork_session(sample_session, turn=2)

        forks = list_session_forks(sample_session)
        assert len(forks) == 1
        assert forks[0]["session_id"] == result.session_id
        assert forks[0]["forked_from_turn"] == 2


class TestGetSessionLineage:
    def test_root_session(self, sample_session):
        lineage = get_session_lineage(sample_session)

        assert lineage["session_id"] == "parent_session_123"
        assert lineage["parent_id"] is None
        assert lineage["ancestors"] == []
        assert lineage["depth"] == 0

    def test_forked_session(self, sample_session):
        # Create a fork
        fork1 = fork_session(sample_session, turn=2)

        lineage = get_session_lineage(fork1.session_dir)

        assert lineage["parent_id"] == "parent_session_123"
        assert "parent_session_123" in lineage["ancestors"]
        assert lineage["depth"] == 1

    def test_deeply_nested_forks(self, sample_session):
        # Create fork chain: parent -> fork1 -> fork2
        fork1 = fork_session(sample_session, turn=2)
        fork2 = fork_session(fork1.session_dir, turn=2)

        lineage = get_session_lineage(fork2.session_dir)

        assert lineage["depth"] == 2
        assert len(lineage["ancestors"]) == 2


# =============================================================================
# Tests for events.py
# =============================================================================


class TestSliceEventsToTimestamp:
    def test_basic_slice(self, sample_session, tmp_path):
        events_path = sample_session / "events.jsonl"
        output_path = tmp_path / "sliced_events.jsonl"

        count = slice_events_to_timestamp(
            events_path,
            "2026-01-05T10:01:00Z",
            output_path,
        )

        assert count > 0
        assert output_path.exists()

        # Verify no events after cutoff
        with open(output_path) as f:
            for line in f:
                event = json.loads(line)
                assert event["ts"] <= "2026-01-05T10:01:00Z"


class TestGetLastTimestampForTurn:
    def test_gets_timestamp(self, sample_session):
        transcript_path = sample_session / "transcript.jsonl"
        ts = get_last_timestamp_for_turn(transcript_path, 2)

        assert ts == "2026-01-05T10:01:10Z"


class TestSliceEventsForFork:
    def test_slices_events(self, sample_session, tmp_path):
        output_path = tmp_path / "forked_events.jsonl"

        count = slice_events_for_fork(
            sample_session / "events.jsonl",
            sample_session / "transcript.jsonl",
            2,
            output_path,
        )

        assert count > 0
        assert output_path.exists()


class TestCountEvents:
    def test_counts_events(self, sample_session):
        count = count_events(sample_session / "events.jsonl")
        assert count == 13  # From fixture

    def test_nonexistent_file(self, tmp_path):
        count = count_events(tmp_path / "nonexistent.jsonl")
        assert count == 0


class TestGetEventSummary:
    def test_gets_summary(self, sample_session):
        summary = get_event_summary(sample_session / "events.jsonl")

        assert summary["total_events"] == 13
        assert "session:start" in summary["event_types"]
        assert "prompt:submit" in summary["event_types"]
        assert summary["first_timestamp"] is not None
        assert summary["last_timestamp"] is not None
