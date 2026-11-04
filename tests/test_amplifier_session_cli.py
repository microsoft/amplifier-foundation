"""Tests for scripts/amplifier-session.py unified CLI.

Uses subprocess invocation to test each subcommand end-to-end.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

_script_path = (
    Path(__file__).resolve().parent.parent / "scripts" / "amplifier-session.py"
)
_project_root = Path(__file__).resolve().parent.parent


# ---------------------------------------------------------------------------
# Test infrastructure
# ---------------------------------------------------------------------------


def _run(*args: str) -> subprocess.CompletedProcess:
    """Run the amplifier-session.py CLI with the given arguments."""
    env = {**os.environ, "PYTHONPATH": str(_project_root)}
    return subprocess.run(
        [sys.executable, str(_script_path), *args],
        capture_output=True,
        text=True,
        timeout=15,
        env=env,
    )


def _make_session(
    tmp_path: Path,
    entries: list[dict],
    metadata: dict | None = None,
) -> Path:
    """Create a session at tmp_path/proj1/sessions/test-session-001/.

    Returns the session directory path.
    """
    session_dir = tmp_path / "proj1" / "sessions" / "test-session-001"
    meta = dict(metadata) if metadata is not None else {}
    meta.setdefault("session_id", "test-session-001")
    meta.setdefault("created", "2024-06-01T10:00:00+00:00")
    meta.setdefault("bundle", "test-bundle")
    meta.setdefault("model", "test-model")
    if os.name == "nt":
        from amplifier_foundation.session.store import write_metadata, write_transcript

        # Valid-state fixture: private creation before any payload, even when
        # the message sequence deliberately has orphaned/misordered tool rows.
        write_transcript(session_dir, entries, windows_policy="private")
        write_metadata(session_dir, meta, windows_policy="private")
        return session_dir

    session_dir.mkdir(parents=True, exist_ok=True)
    with open(session_dir / "transcript.jsonl", "w", encoding="utf-8") as f:
        for entry in entries:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    with open(session_dir / "metadata.json", "w", encoding="utf-8") as f:
        json.dump(meta, f, indent=2)

    return session_dir


_HEALTHY_ENTRIES = [
    {"role": "user", "content": "Hello"},
    {"role": "assistant", "content": "Hi there"},
]

# Broken: assistant has tool_call with no matching tool result (orphaned)
_BROKEN_ENTRIES = [
    {"role": "user", "content": "Hello"},
    {"role": "assistant", "content": "Hi"},
    {"role": "user", "content": "Please use a tool"},
    {
        "role": "assistant",
        "content": "Using tool",
        "tool_calls": [
            {
                "id": "tc_broken_001",
                "function": {"name": "some_tool", "arguments": "{}"},
            }
        ],
    },
    {"role": "user", "content": "Next message"},
]


def test_windows_cli_fixture_uses_private_writers_before_payload(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from amplifier_foundation.session import store

    calls = []
    rows = [{"role": "system", "content": "context"},
            {"role": "developer", "content": "instructions"}, *_BROKEN_ENTRIES]

    def transcript(directory, entries, *, windows_policy):
        assert windows_policy == "private"
        assert not directory.exists()  # No inherited-ACL mkdir before private I/O.
        assert entries is rows
        calls.append("transcript")

    def metadata(directory, entries, *, windows_policy):
        assert windows_policy == "private"
        assert not directory.exists()  # The transcript stub intentionally did no I/O.
        assert entries["model"] == "synthetic-model"
        calls.append("metadata")

    monkeypatch.setattr(sys.modules[__name__], "os", SimpleNamespace(name="nt"))
    monkeypatch.setattr(store, "write_transcript", transcript)
    monkeypatch.setattr(store, "write_metadata", metadata)
    directory = _make_session(tmp_path, rows, {"model": "synthetic-model"})
    assert calls == ["transcript", "metadata"]
    assert not directory.exists()


# ---------------------------------------------------------------------------
# TestDiagnoseSubcommand (4 tests)
# ---------------------------------------------------------------------------


class TestDiagnoseSubcommand:
    """Tests for the `diagnose` subcommand."""

    def test_diagnose_healthy_exits_0(self, tmp_path: Path) -> None:
        """Healthy session: exit 0 and JSON status=healthy."""
        session_dir = _make_session(tmp_path, _HEALTHY_ENTRIES)
        result = _run("diagnose", str(session_dir))
        assert result.returncode == 0
        data = json.loads(result.stdout)
        assert data["status"] == "healthy"
        assert data["failure_modes"] == []

    def test_diagnose_broken_exits_1_with_failure_modes(self, tmp_path: Path) -> None:
        """Broken session: exit 1 and failure_modes non-empty."""
        session_dir = _make_session(tmp_path, _BROKEN_ENTRIES)
        result = _run("diagnose", str(session_dir))
        assert result.returncode == 1
        data = json.loads(result.stdout)
        assert data["status"] == "broken"
        assert len(data["failure_modes"]) > 0

    def test_diagnose_by_session_id_with_sessions_root(self, tmp_path: Path) -> None:
        """Can resolve session by full session ID with --sessions-root."""
        _make_session(tmp_path, _HEALTHY_ENTRIES)
        result = _run(
            "diagnose",
            "test-session-001",
            "--sessions-root",
            str(tmp_path),
        )
        assert result.returncode == 0
        data = json.loads(result.stdout)
        assert data["status"] == "healthy"

    def test_diagnose_by_partial_id(self, tmp_path: Path) -> None:
        """Can resolve session by partial session ID with --sessions-root."""
        _make_session(tmp_path, _HEALTHY_ENTRIES)
        result = _run(
            "diagnose",
            "test-session",  # partial prefix
            "--sessions-root",
            str(tmp_path),
        )
        assert result.returncode == 0
        data = json.loads(result.stdout)
        assert data["status"] == "healthy"


# ---------------------------------------------------------------------------
# TestRepairSubcommand (4 tests)
# ---------------------------------------------------------------------------


class TestRepairSubcommand:
    """Tests for the `repair` subcommand."""

    def test_repair_healthy_is_noop(self, tmp_path: Path) -> None:
        """Healthy session: exit 0 and status=no-repair-needed."""
        session_dir = _make_session(tmp_path, _HEALTHY_ENTRIES)
        result = _run("repair", str(session_dir))
        assert result.returncode == 0
        data = json.loads(result.stdout)
        assert data["status"] == "no-repair-needed"

    def test_repair_broken_succeeds_with_repaired(self, tmp_path: Path) -> None:
        """Broken session: exit 0 and status=repaired."""
        session_dir = _make_session(tmp_path, _BROKEN_ENTRIES)
        result = _run("repair", str(session_dir))
        assert result.returncode == 0
        data = json.loads(result.stdout)
        assert data["status"] == "repaired"

    def test_repair_creates_backup_file(self, tmp_path: Path) -> None:
        """Repair creates a .bak-pre-repair backup file in the session dir."""
        session_dir = _make_session(tmp_path, _BROKEN_ENTRIES)
        result = _run("repair", str(session_dir))
        assert result.returncode == 0, result.stdout + result.stderr
        backup_files = list(session_dir.glob("transcript.jsonl.bak-pre-repair-*"))
        assert len(backup_files) == 1

    def test_repair_then_diagnose_returns_healthy(self, tmp_path: Path) -> None:
        """After repair, re-diagnosing returns healthy (exit 0)."""
        session_dir = _make_session(tmp_path, _BROKEN_ENTRIES)
        repair_result = _run("repair", str(session_dir))
        assert repair_result.returncode == 0

        diag_result = _run("diagnose", str(session_dir))
        assert diag_result.returncode == 0
        data = json.loads(diag_result.stdout)
        assert data["status"] == "healthy"


# ---------------------------------------------------------------------------
# TestRewindSubcommand (3 tests)
# ---------------------------------------------------------------------------


class TestRewindSubcommand:
    """Tests for the `rewind` subcommand."""

    def test_rewind_healthy_is_noop(self, tmp_path: Path) -> None:
        """Healthy session: exit 0 and status=no-rewind-needed."""
        session_dir = _make_session(tmp_path, _HEALTHY_ENTRIES)
        result = _run("rewind", str(session_dir))
        assert result.returncode == 0
        data = json.loads(result.stdout)
        assert data["status"] == "no-rewind-needed"

    def test_rewind_broken_succeeds_with_rewound_entries_less_than_original(
        self,
        tmp_path: Path,
    ) -> None:
        """Broken session: rewound_entries < original_entries."""
        session_dir = _make_session(tmp_path, _BROKEN_ENTRIES)
        result = _run("rewind", str(session_dir))
        assert result.returncode == 0
        data = json.loads(result.stdout)
        assert data["rewound_entries"] < data["original_entries"]

    def test_rewind_creates_backup(self, tmp_path: Path) -> None:
        """Rewind creates a .bak-pre-rewind backup file in the session dir."""
        session_dir = _make_session(tmp_path, _BROKEN_ENTRIES)
        result = _run("rewind", str(session_dir))
        assert result.returncode == 0, result.stdout + result.stderr
        backup_files = list(session_dir.glob("transcript.jsonl.bak-pre-rewind-*"))
        assert len(backup_files) == 1


# ---------------------------------------------------------------------------
# TestInfoSubcommand (1 test)
# ---------------------------------------------------------------------------


class TestInfoSubcommand:
    """Tests for the `info` subcommand."""

    def test_info_returns_metadata_with_required_fields(self, tmp_path: Path) -> None:
        """Returns JSON with session_id, turn_count, and status."""
        session_dir = _make_session(tmp_path, _HEALTHY_ENTRIES)
        result = _run("info", str(session_dir))
        assert result.returncode == 0
        data = json.loads(result.stdout)
        assert "session_id" in data
        assert "turn_count" in data
        assert "status" in data
        assert data["session_id"] == "test-session-001"
        assert data["turn_count"] == 1  # one user message in _HEALTHY_ENTRIES
        assert data["status"] == "healthy"


# ---------------------------------------------------------------------------
# TestFindSubcommand (3 tests)
# ---------------------------------------------------------------------------


class TestFindSubcommand:
    """Tests for the `find` subcommand."""

    def test_find_all_with_sessions_root(self, tmp_path: Path) -> None:
        """find --sessions-root returns all sessions as a JSON array."""
        _make_session(tmp_path, _HEALTHY_ENTRIES)
        result = _run("find", "--sessions-root", str(tmp_path))
        assert result.returncode == 0
        data = json.loads(result.stdout)
        assert isinstance(data, list)
        assert len(data) >= 1
        # Verify expected fields present
        assert "session_id" in data[0]
        assert "project" in data[0]

    def test_find_with_project_filter(self, tmp_path: Path) -> None:
        """find --project filters to sessions in that project."""
        # Create sessions in two projects
        _make_session(tmp_path, _HEALTHY_ENTRIES)
        # Create second session in proj2
        proj2_dir = tmp_path / "proj2" / "sessions" / "test-session-002"
        meta = {"session_id": "test-session-002", "created": "2024-06-01T10:00:00+00:00"}
        if os.name == "nt":
            from amplifier_foundation.session.store import write_metadata, write_transcript

            write_transcript(proj2_dir, _HEALTHY_ENTRIES, windows_policy="private")
            write_metadata(proj2_dir, meta, windows_policy="private")
        else:
            proj2_dir.mkdir(parents=True, exist_ok=True)
            with open(proj2_dir / "transcript.jsonl", "w", encoding="utf-8") as f:
                for entry in _HEALTHY_ENTRIES:
                    f.write(json.dumps(entry) + "\n")
            with open(proj2_dir / "metadata.json", "w", encoding="utf-8") as f:
                json.dump(meta, f)

        result = _run(
            "find",
            "--sessions-root",
            str(tmp_path),
            "--project",
            "proj1",
        )
        assert result.returncode == 0
        data = json.loads(result.stdout)
        assert isinstance(data, list)
        session_ids = [s["session_id"] for s in data]
        assert "test-session-001" in session_ids
        assert "test-session-002" not in session_ids

    def test_find_with_status_filter(self, tmp_path: Path) -> None:
        """find --status healthy returns only healthy sessions."""
        _make_session(tmp_path, _HEALTHY_ENTRIES)

        result = _run(
            "find",
            "--sessions-root",
            str(tmp_path),
            "--status",
            "healthy",
        )
        assert result.returncode == 0
        data = json.loads(result.stdout)
        assert isinstance(data, list)
        assert len(data) >= 1
        assert data[0]["session_id"] == "test-session-001"


def test_find_project_filter_windows_fixture_actual_body(tmp_path, monkeypatch):
    """Exercise both Windows creation branches and the real CLI filter on POSIX.

    Only private creation is modeled; this is not native ACL qualification.
    Legacy writes retain their real missing-directory precondition.
    """
    from types import SimpleNamespace
    from amplifier_foundation.session import store

    transcript, metadata = store.write_transcript, store.write_metadata
    calls = []

    def private_transcript(directory, entries, *, windows_policy="legacy"):
        calls.append((directory, "transcript", windows_policy))
        if windows_policy == "private":
            assert not directory.exists()  # No ordinary mkdir before private creation.
            directory.mkdir(parents=True)
        # Real legacy writer deliberately cannot create missing directories:
        # an omitted selection in the actual body below reproduces the failure.
        transcript(directory, entries)

    def private_metadata(directory, value, *, windows_policy="legacy"):
        calls.append((directory, "metadata", windows_policy))
        assert windows_policy == "private"
        metadata(directory, value)

    monkeypatch.setattr(sys.modules[__name__], "os", SimpleNamespace(
        name="nt", environ=os.environ,
    ))
    monkeypatch.setattr(store, "write_transcript", private_transcript)
    monkeypatch.setattr(store, "write_metadata", private_metadata)
    # Do not reconstruct the fixture or replace _run: execute the maintained
    # test's actual Windows branch and all its original filtering assertions.
    TestFindSubcommand().test_find_with_project_filter(tmp_path)
    first = tmp_path / "proj1" / "sessions" / "test-session-001"
    second = tmp_path / "proj2" / "sessions" / "test-session-002"
    assert calls == [
        (first, "transcript", "private"), (first, "metadata", "private"),
        (second, "transcript", "private"), (second, "metadata", "private"),
    ]
    result = _run("find", "--sessions-root", str(tmp_path))
    assert result.returncode == 0, result.stdout + result.stderr
    assert {entry["session_id"] for entry in json.loads(result.stdout)} == {
        "test-session-001", "test-session-002",
    }


# ---------------------------------------------------------------------------
# TestCLIEdgeCases (2 tests)
# ---------------------------------------------------------------------------


class TestCLIEdgeCases:
    """Edge case tests for the amplifier-session.py CLI."""

    @pytest.mark.skipif(os.name != "nt", reason="requires actual Windows private ACL validation")
    @pytest.mark.parametrize("action", ["repair", "rewind"])
    def test_native_cli_legacy_default_is_distinct_from_explicit_private_refusal(self, tmp_path, action):
        from amplifier_foundation.session import _windows_ownership as win
        from amplifier_foundation.session.finder import resolve_session
        from amplifier_foundation.session.shared_state import SharedStateError
        from amplifier_foundation.session.store import write_transcript

        # A private control proves the same messages/layout can be processed.
        control = _make_session(tmp_path / "private-control", _BROKEN_ENTRIES)
        transcript_bytes = (control / "transcript.jsonl").read_bytes()
        metadata_bytes = (control / "metadata.json").read_bytes()
        positive = _run(action, str(control))
        assert positive.returncode == 0, positive.stdout + positive.stderr
        assert json.loads(positive.stdout)["status"] == (
            "repaired" if action == "repair" else "rewound"
        )
        assert _run("diagnose", str(control)).returncode == 0

        # Same payload, but deliberately ordinary inherited-ACL creation.
        # metadata.json is required for CLI recognition, not an ACL endorsement.
        directory = tmp_path / "unsafe-session"
        directory.mkdir()
        path = directory / "transcript.jsonl"
        path.write_bytes(transcript_bytes)
        metadata_path = directory / "metadata.json"
        metadata_path.write_bytes(metadata_bytes)
        assert resolve_session(str(directory)) == directory
        diagnosis = _run("diagnose", str(directory))
        assert diagnosis.returncode == 1, diagnosis.stdout + diagnosis.stderr
        assert json.loads(diagnosis.stdout)["status"] == "broken"

        # The explicit strict route still refuses without changing any bytes.
        with pytest.raises(SharedStateError):
            with win._pinned(win._API(), directory, root=directory):
                pytest.fail("inherited-ACL fixture unexpectedly passed private validation")
        with pytest.raises(SharedStateError):
            write_transcript(directory, _HEALTHY_ENTRIES, windows_policy="private")
        assert path.read_bytes() == transcript_bytes
        assert metadata_path.read_bytes() == metadata_bytes
        # Unchanged CLI entry points select legacy before I/O, not as a fallback
        # after the strict refusal above (that call completed independently).
        result = _run(action, str(directory))
        assert result.returncode == 0, result.stdout + result.stderr
        assert json.loads(result.stdout)["status"] == ("repaired" if action == "repair" else "rewound")
        assert metadata_path.read_bytes() == metadata_bytes
        assert not list(directory.glob(".history-*"))
        assert not (directory / "transcript.jsonl.backup").exists()
        assert _run("diagnose", str(directory)).returncode == 0

    def test_no_subcommand_exits_2(self) -> None:
        """Running with no subcommand exits with code 2."""
        result = _run()
        assert result.returncode == 2

    def test_nonexistent_session_exits_1_with_error(self, tmp_path: Path) -> None:
        """Attempting to diagnose a nonexistent session exits 1 with error JSON."""
        result = _run(
            "diagnose",
            "nonexistent-session-xyz-12345",
            "--sessions-root",
            str(tmp_path),
        )
        assert result.returncode == 1
        data = json.loads(result.stdout)
        assert "error" in data
