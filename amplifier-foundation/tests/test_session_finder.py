"""Tests for amplifier_foundation.session.finder — session discovery and resolution."""

from __future__ import annotations

from pathlib import Path

import pytest

from amplifier_foundation.session.store import write_metadata, write_transcript
from amplifier_foundation.session.finder import (
    find_sessions,
    resolve_session,
    session_info,
)


# ---------------------------------------------------------------------------
# Test helpers
# ---------------------------------------------------------------------------


def _make_session(
    sessions_root: Path,
    project: str,
    session_id: str,
    *,
    messages: list[dict] | None = None,
    metadata: dict | None = None,
) -> Path:
    """Create a minimal session directory for testing.

    Creates the session at sessions_root/<project>/sessions/<session_id>/
    with a transcript.jsonl and metadata.json.
    """
    session_dir = sessions_root / project / "sessions" / session_id
    session_dir.mkdir(parents=True, exist_ok=True)

    msgs = (
        messages
        if messages is not None
        else [
            {"role": "user", "content": "Hello"},
            {"role": "assistant", "content": "Hi there"},
        ]
    )
    write_transcript(session_dir, msgs)

    meta = dict(metadata) if metadata is not None else {}
    meta.setdefault("session_id", session_id)
    meta.setdefault("created", "2024-06-01T10:00:00+00:00")
    meta.setdefault("bundle", "test-bundle")
    meta.setdefault("model", "test-model")
    write_metadata(session_dir, meta)

    return session_dir


# ---------------------------------------------------------------------------
# TestResolveSession (6 tests)
# ---------------------------------------------------------------------------


class TestResolveSession:
    """Tests for resolve_session — accepts full path, full ID, or partial ID."""

    def test_resolve_full_path(self, tmp_path: Path):
        """Passing an absolute path to a valid session dir returns that path."""
        session_dir = _make_session(tmp_path, "proj-a", "session-abc123")
        result = resolve_session(str(session_dir), sessions_root=tmp_path)
        assert result == session_dir

    def test_resolve_full_id(self, tmp_path: Path):
        """Passing an exact session ID finds the session dir across projects."""
        session_dir = _make_session(tmp_path, "proj-a", "session-abc123")
        result = resolve_session("session-abc123", sessions_root=tmp_path)
        assert result == session_dir

    def test_resolve_partial_id(self, tmp_path: Path):
        """Passing a prefix of a session ID resolves to that session."""
        session_dir = _make_session(tmp_path, "proj-a", "session-abc123fullname")
        result = resolve_session("session-abc", sessions_root=tmp_path)
        assert result == session_dir

    def test_ambiguous_partial_raises_value_error(self, tmp_path: Path):
        """Partial ID that matches multiple sessions raises ValueError."""
        _make_session(tmp_path, "proj-a", "session-abc-first")
        _make_session(tmp_path, "proj-b", "session-abc-second")
        with pytest.raises(ValueError, match="[Aa]mbiguous"):
            resolve_session("session-abc", sessions_root=tmp_path)

    def test_no_match_raises_file_not_found(self, tmp_path: Path):
        """Non-existent session ID raises FileNotFoundError."""
        _make_session(tmp_path, "proj-a", "session-abc123")
        with pytest.raises(FileNotFoundError):
            resolve_session("nonexistent-id", sessions_root=tmp_path)

    def test_project_filter_disambiguation(self, tmp_path: Path):
        """Project filter resolves ambiguous partial ID by restricting scope."""
        session_a = _make_session(tmp_path, "proj-a", "session-xyz-one")
        _make_session(tmp_path, "proj-b", "session-xyz-two")
        # Without filter: ambiguous
        with pytest.raises(ValueError):
            resolve_session("session-xyz", sessions_root=tmp_path)
        # With filter: unambiguous
        result = resolve_session(
            "session-xyz", sessions_root=tmp_path, project="proj-a"
        )
        assert result == session_a


# ---------------------------------------------------------------------------
# TestFindSessions (7 tests)
# ---------------------------------------------------------------------------


class TestFindSessions:
    """Tests for find_sessions — list and filter sessions."""

    def test_find_all(self, tmp_path: Path):
        """find_sessions with no filters returns all sessions."""
        _make_session(tmp_path, "proj-a", "sess-001")
        _make_session(tmp_path, "proj-a", "sess-002")
        _make_session(tmp_path, "proj-b", "sess-003")
        results = find_sessions(sessions_root=tmp_path)
        assert len(results) == 3

    def test_filter_by_project(self, tmp_path: Path):
        """project filter restricts results to a single project."""
        _make_session(tmp_path, "proj-a", "sess-001")
        _make_session(tmp_path, "proj-a", "sess-002")
        _make_session(tmp_path, "proj-b", "sess-003")
        results = find_sessions(sessions_root=tmp_path, project="proj-a")
        assert len(results) == 2
        assert all(r["project"] == "proj-a" for r in results)

    def test_filter_by_after_date(self, tmp_path: Path):
        """after filter excludes sessions created before the given date."""
        _make_session(
            tmp_path,
            "proj-a",
            "old-sess",
            metadata={"created": "2023-01-01T00:00:00+00:00"},
        )
        _make_session(
            tmp_path,
            "proj-a",
            "new-sess",
            metadata={"created": "2025-01-01T00:00:00+00:00"},
        )
        results = find_sessions(sessions_root=tmp_path, after="2024-01-01")
        assert len(results) == 1
        assert results[0]["session_id"] == "new-sess"

    def test_filter_by_before_date(self, tmp_path: Path):
        """before filter excludes sessions created after the given date."""
        _make_session(
            tmp_path,
            "proj-a",
            "old-sess",
            metadata={"created": "2023-01-01T00:00:00+00:00"},
        )
        _make_session(
            tmp_path,
            "proj-a",
            "new-sess",
            metadata={"created": "2025-01-01T00:00:00+00:00"},
        )
        results = find_sessions(sessions_root=tmp_path, before="2024-01-01")
        assert len(results) == 1
        assert results[0]["session_id"] == "old-sess"

    def test_filter_by_keyword(self, tmp_path: Path):
        """keyword filter only returns sessions with keyword in transcript."""
        _make_session(
            tmp_path,
            "proj-a",
            "match-sess",
            messages=[
                {"role": "user", "content": "Tell me about elephants"},
                {"role": "assistant", "content": "Elephants are large mammals"},
            ],
        )
        _make_session(
            tmp_path,
            "proj-a",
            "no-match-sess",
            messages=[
                {"role": "user", "content": "Hello"},
                {"role": "assistant", "content": "Hi"},
            ],
        )
        results = find_sessions(sessions_root=tmp_path, keyword="elephant")
        assert len(results) == 1
        assert results[0]["session_id"] == "match-sess"

    def test_respects_limit(self, tmp_path: Path):
        """limit parameter caps the number of returned results."""
        for i in range(10):
            _make_session(tmp_path, "proj-a", f"sess-{i:03d}")
        results = find_sessions(sessions_root=tmp_path, limit=3)
        assert len(results) == 3

    def test_returns_expected_fields(self, tmp_path: Path):
        """Each result dict has session_id, path, project, created, bundle, model."""
        _make_session(
            tmp_path,
            "proj-a",
            "sess-full",
            metadata={
                "session_id": "sess-full",
                "created": "2024-06-01T10:00:00+00:00",
                "bundle": "my-bundle",
                "model": "my-model",
            },
        )
        results = find_sessions(sessions_root=tmp_path)
        assert len(results) == 1
        r = results[0]
        assert r["session_id"] == "sess-full"
        assert isinstance(r["path"], Path)
        assert r["project"] == "proj-a"
        assert r["created"] == "2024-06-01T10:00:00+00:00"
        assert r["bundle"] == "my-bundle"
        assert r["model"] == "my-model"

    def test_empty_root_returns_empty(self, tmp_path: Path):
        """sessions_root with no project dirs returns empty list."""
        empty_root = tmp_path / "empty"
        empty_root.mkdir()
        results = find_sessions(sessions_root=empty_root)
        assert results == []


# ---------------------------------------------------------------------------
# TestSessionInfo (2 tests)
# ---------------------------------------------------------------------------


class TestSessionInfo:
    """Tests for session_info — metadata + turn_count + health status."""

    def test_basic_info_with_turn_count_and_healthy_status(self, tmp_path: Path):
        """session_info returns correct fields for a healthy session."""
        session_dir = _make_session(
            tmp_path,
            "proj-a",
            "healthy-sess",
            messages=[
                {"role": "user", "content": "Turn 1"},
                {"role": "assistant", "content": "Response 1"},
                {"role": "user", "content": "Turn 2"},
                {"role": "assistant", "content": "Response 2"},
            ],
            metadata={
                "session_id": "healthy-sess",
                "created": "2024-06-01T10:00:00+00:00",
                "bundle": "test-bundle",
                "model": "gpt-4",
            },
        )
        info = session_info(session_dir)
        assert info["session_id"] == "healthy-sess"
        assert info["path"] == session_dir
        assert info["project"] == "proj-a"
        assert info["created"] == "2024-06-01T10:00:00+00:00"
        assert info["bundle"] == "test-bundle"
        assert info["model"] == "gpt-4"
        assert info["turn_count"] == 2
        assert info["status"] == "healthy"
        assert info["failure_modes"] == []

    def test_broken_session_with_failure_modes(self, tmp_path: Path):
        """session_info returns failure_modes for a session with orphaned tool calls."""
        session_dir = _make_session(
            tmp_path,
            "proj-a",
            "broken-sess",
            messages=[
                {"role": "user", "content": "Do something"},
                {
                    "role": "assistant",
                    "content": "Using a tool",
                    "tool_calls": [
                        {"id": "call_xyz", "function": {"name": "my_tool"}},
                    ],
                },
                # Missing tool result — orphaned tool call
            ],
        )
        info = session_info(session_dir)
        assert info["status"] == "broken"
        assert "missing_tool_results" in info["failure_modes"]
