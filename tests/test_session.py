"""Tests for session fork, slice, and events utilities."""

from __future__ import annotations

import json
import os

import pytest

from amplifier_foundation.session import (
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
    is_real_user_message,
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
    if os.name != "nt":
        session_dir.mkdir(parents=True)

    messages = [
        {"role": "user", "content": "Turn 1", "timestamp": "2026-01-05T10:00:00Z"},
        {
            "role": "assistant",
            "content": "Response 1",
            "timestamp": "2026-01-05T10:00:05Z",
        },
        {"role": "user", "content": "Turn 2", "timestamp": "2026-01-05T10:01:00Z"},
        {
            "role": "assistant",
            "content": "Response 2",
            "timestamp": "2026-01-05T10:01:10Z",
        },
        {"role": "user", "content": "Turn 3", "timestamp": "2026-01-05T10:02:00Z"},
        {
            "role": "assistant",
            "content": "Response 3",
            "timestamp": "2026-01-05T10:02:15Z",
        },
    ]

    # Write transcript
    if os.name == "nt":
        from amplifier_foundation.session.store import write_transcript

        write_transcript(session_dir, messages)
    else:
        with open(session_dir / "transcript.jsonl", "w") as f:
            for msg in messages:
                f.write(json.dumps(msg) + "\n")

    # Write metadata
    metadata = {
        "session_id": "parent_session_123",
        "bundle": "foundation",
        "model": "claude-sonnet-4-5",
        "created": "2026-01-05T10:00:00Z",
        "turn_count": 3,
    }
    if os.name == "nt":
        from amplifier_foundation.session.store import write_metadata

        write_metadata(session_dir, metadata)
    else:
        (session_dir / "metadata.json").write_text(json.dumps(metadata, indent=2))

    # Write events
    events = [
        {
            "event": "session:start",
            "ts": "2026-01-05T10:00:00Z",
            "session_id": "parent_session_123",
        },
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

    def test_adds_synthetic_result_after_assistant(self):
        """Synthetic result is inserted immediately after the assistant that made the call."""
        messages = [
            {"role": "user", "content": "Do something"},
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {
                        "id": "call_123",
                        "function": {"name": "do_thing", "arguments": "{}"},
                    },
                ],
            },
        ]
        result = add_synthetic_tool_results(messages, ["call_123"])
        assert len(result) == 3
        assert result[0]["role"] == "user"
        assert result[1]["role"] == "assistant"
        assert result[2]["role"] == "tool"
        assert result[2]["tool_call_id"] == "call_123"
        assert "forked" in result[2]["content"]

    def test_tool_name_preserved_in_synthetic(self):
        """The tool name from the assistant message is included in the synthetic result."""
        messages = [
            {"role": "user", "content": "List files"},
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {
                        "id": "call_123",
                        "function": {"name": "list_files", "arguments": "{}"},
                    },
                ],
            },
        ]
        result = add_synthetic_tool_results(messages, ["call_123"])
        synthetic = next(m for m in result if m.get("role") == "tool")
        assert synthetic.get("name") == "list_files"

    def test_multiple_orphans_after_assistant(self):
        """All synthetic results for one assistant message are inserted after it."""
        messages = [
            {"role": "user", "content": "Do stuff"},
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {"id": "a", "function": {"name": "fn_a", "arguments": "{}"}},
                    {"id": "b", "function": {"name": "fn_b", "arguments": "{}"}},
                    {"id": "c", "function": {"name": "fn_c", "arguments": "{}"}},
                ],
            },
        ]
        result = add_synthetic_tool_results(messages, ["a", "b", "c"])
        # user + assistant + 3 tool synthetics = 5
        assert len(result) == 5
        tool_ids = {m["tool_call_id"] for m in result if m.get("role") == "tool"}
        assert tool_ids == {"a", "b", "c"}
        # All synthetics appear after the assistant (index 1)
        roles = [m["role"] for m in result]
        assert roles == ["user", "assistant", "tool", "tool", "tool"]

    def test_synthetics_inserted_before_subsequent_user_message(self):
        """Synthetic results go BETWEEN an assistant message and the next user message.

        This is the core regression test: when messages end with
        [assistant+tool_calls, user_message], synthetics must go BETWEEN them,
        not after the user message.
        """
        messages = [
            {"role": "user", "content": "Do something"},
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {
                        "id": "call_orphan",
                        "function": {"name": "do_thing", "arguments": "{}"},
                    },
                ],
            },
            {"role": "user", "content": "Next question"},
        ]
        result = add_synthetic_tool_results(messages, ["call_orphan"])

        roles = [m["role"] for m in result]

        # Synthetic tool result must appear BEFORE the next user message
        tool_idx = roles.index("tool")
        last_user_idx = max(i for i, r in enumerate(roles) if r == "user")
        assert tool_idx < last_user_idx, (
            "synthetic tool result must precede subsequent user message"
        )

        # FM3: a synthetic assistant response closes the interrupted turn
        # Structure: user, assistant(tool_calls), tool(synthetic), assistant(synthetic), user
        assert roles[0] == "user"
        assert roles[1] == "assistant"  # original with tool_calls
        assert roles[2] == "tool"  # synthetic tool result
        assert roles[3] == "assistant"  # FM3 synthetic assistant response
        assert roles[4] == "user"  # original next user message

    def test_fm3_synthetic_assistant_only_when_user_follows(self):
        """FM3 synthetic assistant response is NOT inserted when no user message follows."""
        messages = [
            {"role": "user", "content": "Do something"},
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {
                        "id": "call_orphan",
                        "function": {"name": "do_thing", "arguments": "{}"},
                    },
                ],
            },
            # No user message after — this is a slice at the end of the conversation
        ]
        result = add_synthetic_tool_results(messages, ["call_orphan"])

        # Only user, assistant, tool synthetic — no extra FM3 assistant
        roles = [m["role"] for m in result]
        assert roles == ["user", "assistant", "tool"]

    def test_anthropic_format_tool_use(self):
        """Synthetic results are inserted correctly for Anthropic content-block format."""
        messages = [
            {"role": "user", "content": "Do something"},
            {
                "role": "assistant",
                "content": [
                    {"type": "text", "text": "Let me help."},
                    {
                        "type": "tool_use",
                        "id": "tool_abc",
                        "name": "search",
                        "input": {},
                    },
                ],
            },
        ]
        result = add_synthetic_tool_results(messages, ["tool_abc"])
        assert len(result) == 3
        assert result[2]["role"] == "tool"
        assert result[2]["tool_call_id"] == "tool_abc"
        assert result[2].get("name") == "search"
        assert "forked" in result[2]["content"]


class TestSliceToTurnWithOrphanedTools:
    def test_complete_orphaned_tools(self, messages_with_orphaned_tool):
        # Fork at turn 2 which has orphaned tool
        sliced = slice_to_turn(
            messages_with_orphaned_tool, 2, handle_orphaned_tools="complete"
        )
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
    @pytest.fixture
    def windows_fork_io(self, sample_session, monkeypatch):
        """Exercise the Windows fork control path with synthetic portable I/O."""
        import importlib
        from contextlib import contextmanager
        from types import SimpleNamespace
        from amplifier_foundation.session import history, _windows_history as backend
        from amplifier_foundation.session import _windows_ownership as win

        fork_module = importlib.import_module("amplifier_foundation.session.fork")
        messages = [{"role": "user", "content": "first"},
                    {"role": "assistant", "content": "answer"},
                    {"role": "user", "content": "second"}]
        barrier = None
        reservations = []

        class History:
            def __init__(self, directory):
                self.directory = directory

            def load(self, **kwargs):
                if barrier is not None:
                    barrier.wait(timeout=5)
                return SimpleNamespace(messages=messages, metadata={"session_id": sample_session.name})

            def save(self, rows, metadata, **kwargs):
                self.directory.mkdir(exist_ok=True)
                (self.directory / "transcript.jsonl").write_text(json.dumps(rows), encoding="utf-8")
                (self.directory / "metadata.json").write_text(json.dumps(metadata), encoding="utf-8")

        @contextmanager
        def reserved(directory):
            # Portable stand-in only: the real native helper is tested separately.
            directory.mkdir()  # Atomic create-only, not exists-then-create.
            reservations.append(directory)
            yield

        def synchronize(value):
            nonlocal barrier
            barrier = value

        monkeypatch.setattr(fork_module, "os", SimpleNamespace(name="nt"))
        monkeypatch.setattr(win, "_absolute", lambda path: path)
        monkeypatch.setattr(history, "SessionHistoryStore", History)
        monkeypatch.setattr(backend, "reserve_new_session", reserved, raising=False)
        return SimpleNamespace(source=sample_session, reservations=reservations, synchronize=synchronize)

    @pytest.mark.parametrize("self_fork", [False, True])
    def test_windows_fork_rejects_existing_private_destination_and_self(self, windows_fork_io, self_fork):
        source = windows_fork_io.source
        destination = source if self_fork else source.parent / "existing-private"
        if not self_fork:
            destination.mkdir()
            (destination / "transcript.jsonl").write_bytes(b"preserve transcript")
            (destination / "metadata.json").write_bytes(b"preserve metadata")
        before = {path.name: path.read_bytes() for path in destination.iterdir()}
        with pytest.raises(FileExistsError):
            fork_session(source, new_session_id=destination.name, turn=1, include_events=False)
        assert before == {path.name: path.read_bytes() for path in destination.iterdir()}
        assert not windows_fork_io.reservations

    def test_windows_fork_same_id_contenders_have_one_winner(self, windows_fork_io):
        from concurrent.futures import ThreadPoolExecutor
        from threading import Barrier

        windows_fork_io.synchronize(Barrier(2))

        def fork():
            try:
                return fork_session(windows_fork_io.source, new_session_id="one-fork",
                                    turn=1, include_events=False)
            except FileExistsError:
                return None

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _: fork(), range(2)))
        assert sum(result is not None for result in results) == 1
        assert windows_fork_io.reservations == [windows_fork_io.source.parent / "one-fork"]

    def test_windows_fork_routes_source_and_destination_through_private_history(self, sample_session, monkeypatch):
        import importlib
        from contextlib import contextmanager
        from types import SimpleNamespace
        from amplifier_foundation.session import history, _windows_ownership as win
        from amplifier_foundation.session import _windows_history as backend

        fork_module = importlib.import_module("amplifier_foundation.session.fork")
        calls = []
        messages = [{"role": "system", "content": "synthetic instructions"},
                    {"role": "user", "content": "synthetic question"},
                    {"role": "assistant", "content": "synthetic answer"}]

        class History:
            def __init__(self, directory):
                self.directory = directory

            def load(self, **kwargs):
                assert self.directory == sample_session
                assert kwargs == {"include_events": False}
                calls.append("private-source")
                return SimpleNamespace(messages=messages, metadata={"session_id": "parent_session_123"})

            def save(self, rows, metadata, **kwargs):
                assert not self.directory.exists()  # No inherited-ACL mkdir/write first.
                assert rows == messages
                assert metadata["parent_id"] == "parent_session_123"
                assert kwargs == {"preserve_system": True}
                assert calls[-1] == "reserved"
                calls.append("private-destination")

        @contextmanager
        def reserved(directory):
            assert not directory.exists()
            calls.append("reserved")
            yield
            calls.append("unpinned")

        monkeypatch.setattr(fork_module, "os", SimpleNamespace(name="nt"))
        monkeypatch.setattr(win, "_absolute", lambda path: path)
        monkeypatch.setattr(backend, "reserve_new_session", reserved)
        monkeypatch.setattr(history, "SessionHistoryStore", History)
        for name in ("write_jsonl", "write_metadata", "load_transcript", "load_metadata"):
            monkeypatch.setattr(fork_module, name, lambda *a: pytest.fail("legacy history I/O"))
        result = fork_session(sample_session, include_events=False)
        assert calls == ["private-source", "reserved", "private-destination", "unpinned"]
        assert not result.session_dir.exists()

    @pytest.mark.skipif(os.name != "nt", reason="requires actual Windows local NTFS")
    def test_native_fork_creates_private_canonical_history(self, sample_session, tmp_path):
        from amplifier_foundation.session.history import SessionHistoryStore

        source = SessionHistoryStore(sample_session)
        rows = [{"role": "system", "content": "synthetic context"},
                {"role": "developer", "content": "synthetic instructions"},
                *source.load_messages()]
        source.save_messages(rows, preserve_system=True)
        result = fork_session(sample_session, target_dir=tmp_path / "new-target",
                              turn=2, include_events=False)
        saved = SessionHistoryStore(result.session_dir).load(include_events=False)
        assert saved.messages == rows[:6]
        assert saved.metadata["parent_id"] == "parent_session_123"
        assert saved.metadata["forked_from_turn"] == 2
        assert not (result.session_dir / "events.jsonl").exists()

    @pytest.mark.skipif(os.name != "nt", reason="requires actual Windows local NTFS")
    def test_native_fork_refuses_unsafe_legacy_source(self, tmp_path):
        from amplifier_foundation.session.shared_state import SharedStateError

        source = tmp_path / "legacy"
        source.mkdir()
        (source / "transcript.jsonl").write_bytes(b'{"role":"user","content":"legacy"}\n')
        with pytest.raises(SharedStateError):
            fork_session(source, target_dir=tmp_path / "destination", include_events=False)
        assert not (tmp_path / "destination").exists()

    @pytest.mark.skipif(os.name != "nt", reason="requires actual Windows local NTFS")
    def test_native_fork_refuses_unsafe_existing_destination(self, sample_session):
        destination = sample_session.parent / "unsafe-fork"
        destination.mkdir()
        path = destination / "transcript.jsonl"
        path.write_bytes(b'{"role":"user","content":"preserve"}\n')
        before = path.read_bytes()
        with pytest.raises(FileExistsError):
            fork_session(sample_session, new_session_id="unsafe-fork", include_events=False)
        assert path.read_bytes() == before
        assert not (destination / "metadata.json").exists()

    @pytest.mark.skipif(os.name != "nt", reason="requires actual Windows local NTFS")
    @pytest.mark.parametrize("kind", ["private", "empty-private", "self"])
    def test_native_fork_existing_private_and_self_are_create_only(self, sample_session, kind):
        from amplifier_foundation.session.history import SessionHistoryStore
        from amplifier_foundation.session import _windows_history as backend

        destination = sample_session if kind == "self" else sample_session.parent / "existing-fork"
        if kind == "private":
            SessionHistoryStore(destination).save([{"role": "user", "content": "keep"}], {"keep": True})
        elif kind == "empty-private":
            with backend.reserve_new_session(destination):
                pass
        before = {path.name: path.read_bytes() for path in destination.iterdir()}
        with pytest.raises(FileExistsError):
            fork_session(sample_session, new_session_id=destination.name, turn=1, include_events=False)
        assert before == {path.name: path.read_bytes() for path in destination.iterdir()}

    @pytest.mark.skipif(os.name != "nt", reason="requires actual Windows local NTFS")
    def test_native_fork_two_same_id_contenders_have_one_winner(self, sample_session, monkeypatch):
        from concurrent.futures import ThreadPoolExecutor
        from contextlib import contextmanager
        from threading import Barrier
        from amplifier_foundation.session import _windows_history as backend
        from amplifier_foundation.session.history import SessionHistoryStore

        original = backend.reserve_new_session
        barrier = Barrier(2)

        @contextmanager
        def synchronized(directory):
            barrier.wait(timeout=10)
            with original(directory):
                yield

        monkeypatch.setattr(backend, "reserve_new_session", synchronized)

        def fork(_):
            try:
                return fork_session(sample_session, new_session_id="one-fork", turn=1, include_events=False)
            except FileExistsError:
                return None

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(fork, range(2)))
        assert sum(result is not None for result in results) == 1
        saved = SessionHistoryStore(sample_session.parent / "one-fork").load(include_events=False)
        assert len(saved.messages) == 2 and saved.metadata["parent_id"] == sample_session.name
        assert not list((sample_session.parent / "one-fork").glob("*.backup"))

    @pytest.mark.skipif(os.name != "nt", reason="requires actual Windows local NTFS")
    def test_native_fork_pins_namespace_through_save_and_events(self, sample_session, monkeypatch):
        import importlib
        from amplifier_foundation.session.history import SessionHistoryStore

        fork_module = importlib.import_module("amplifier_foundation.session.fork")
        original_save = SessionHistoryStore.save
        original_events = fork_module.slice_events_for_fork
        destination = sample_session.parent / "pinned-fork"
        observed = []

        def pinned():
            for directory in (destination, destination.parent):
                with pytest.raises(OSError):
                    directory.rename(directory.with_name(directory.name + "-moved"))

        def save(store, *args, **kwargs):
            pinned()
            observed.append("save")
            return original_save(store, *args, **kwargs)

        def events(*args, **kwargs):
            pinned()
            observed.append("events")
            return original_events(*args, **kwargs)

        monkeypatch.setattr(SessionHistoryStore, "save", save)
        monkeypatch.setattr(fork_module, "slice_events_for_fork", events)
        fork_session(sample_session, new_session_id=destination.name, turn=1)
        assert observed == ["save", "events"]
        # Pins were released after all publication, not leaked.
        moved = destination.with_name("completed-fork")
        destination.rename(moved)
        assert (moved / "metadata.json").exists()

    @pytest.mark.skipif(os.name != "nt", reason="requires actual Windows local NTFS")
    def test_native_fork_failure_retains_partial_new_directory_and_refuses_retry(self, sample_session, monkeypatch):
        from amplifier_foundation.session.history import SessionHistoryStore

        original = SessionHistoryStore._write_prepared
        destination = sample_session.parent / "incomplete-fork"
        source_before = {path.name: path.read_bytes() for path in sample_session.iterdir()}

        def fail_metadata(path, content, preserve_backup):
            if path == destination / "metadata.json":
                raise OSError("synthetic metadata publication failure")
            return original(path, content, preserve_backup)

        monkeypatch.setattr(SessionHistoryStore, "_write_prepared", staticmethod(fail_metadata))
        with pytest.raises(OSError, match="synthetic metadata") as caught:
            fork_session(sample_session, new_session_id=destination.name, turn=1, include_events=False)
        assert any("did not complete" in note and "retained" in note for note in caught.value.__notes__)
        assert len(SessionHistoryStore(destination).load_messages()) == 2
        assert not (destination / "metadata.json").exists()
        partial = (destination / "transcript.jsonl").read_bytes()
        with pytest.raises(FileExistsError):
            fork_session(sample_session, new_session_id=destination.name, turn=1, include_events=False)
        assert (destination / "transcript.jsonl").read_bytes() == partial
        assert source_before == {path.name: path.read_bytes() for path in sample_session.iterdir()}

    @pytest.mark.skipif(os.name != "nt", reason="requires actual Windows local NTFS")
    @pytest.mark.parametrize("location", ["source", "target"])
    def test_native_fork_rejects_reparse_before_resolving_it(self, sample_session, tmp_path, location):
        import subprocess
        from amplifier_foundation.session.shared_state import SharedStateError

        alias = tmp_path / "alias"
        actual = sample_session if location == "source" else tmp_path / "actual-target"
        actual.mkdir(exist_ok=True)
        result = subprocess.run(["cmd.exe", "/d", "/c", "mklink", "/J", str(alias), str(actual)],
                                capture_output=True, text=True, timeout=10)
        assert result.returncode == 0, result.stderr
        with pytest.raises(SharedStateError, match="reparse"):
            fork_session(alias if location == "source" else sample_session,
                         target_dir=alias if location == "target" else tmp_path / "destination",
                         include_events=False, new_session_id="must-not-publish")
        assert not (actual / "must-not-publish").exists()

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
        assert metadata["bundle"] == "foundation"
        assert metadata["model"] == "claude-sonnet-4-5"

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


class TestIsRealUserMessage:
    def test_user_message_is_real(self):
        assert is_real_user_message({"role": "user", "content": "Hello"}) is True

    def test_assistant_message_is_not_real(self):
        assert is_real_user_message({"role": "assistant", "content": "Hi"}) is False

    def test_tool_role_is_not_real(self):
        assert is_real_user_message({"role": "tool", "content": "result"}) is False

    def test_tool_call_id_is_not_real(self):
        assert (
            is_real_user_message(
                {"role": "user", "content": "result", "tool_call_id": "abc123"}
            )
            is False
        )

    def test_system_reminder_string_is_not_real(self):
        assert (
            is_real_user_message(
                {
                    "role": "user",
                    "content": "<system-reminder>some context</system-reminder>",
                }
            )
            is False
        )

    def test_system_reminder_in_list_content_is_not_real(self):
        assert (
            is_real_user_message(
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "text",
                            "text": "<system-reminder>ctx</system-reminder>",
                        }
                    ],
                }
            )
            is False
        )
