"""Tests for amplifier_foundation.session.store — filename constants and JSONL I/O."""

from __future__ import annotations

import os
import re
from pathlib import Path

import pytest

from amplifier_foundation.session.store import (
    EVENTS_FILENAME,
    METADATA_FILENAME,
    TRANSCRIPT_FILENAME,
    backup,
    load_metadata,
    load_transcript,
    load_transcript_with_lines,
    read_jsonl,
    write_jsonl,
    write_metadata,
    write_transcript,
)


# =============================================================================
# TestConstants
# =============================================================================


class TestConstants:
    """Verify module-level filename constants have the correct values."""

    def test_transcript_filename(self):
        assert TRANSCRIPT_FILENAME == "transcript.jsonl"

    def test_metadata_filename(self):
        assert METADATA_FILENAME == "metadata.json"

    def test_events_filename(self):
        assert EVENTS_FILENAME == "events.jsonl"


# =============================================================================
# TestReadJsonl
# =============================================================================


class TestReadJsonl:
    """Verify read_jsonl behaviour across valid data, edge cases, and errors."""

    def test_reads_valid_jsonl(self, tmp_path: Path):
        """Reads a file with 3 valid JSON lines and returns all 3 entries."""
        p = tmp_path / "data.jsonl"
        p.write_text(
            '{"a": 1}\n{"b": 2}\n{"c": 3}\n',
            encoding="utf-8",
        )
        result = list(read_jsonl(p))
        assert result == [{"a": 1}, {"b": 2}, {"c": 3}]

    def test_skips_blank_lines(self, tmp_path: Path):
        """Blank lines in a JSONL file are silently skipped."""
        p = tmp_path / "blanks.jsonl"
        p.write_text(
            '{"x": 1}\n\n{"y": 2}\n\n',
            encoding="utf-8",
        )
        result = list(read_jsonl(p))
        assert result == [{"x": 1}, {"y": 2}]

    def test_skips_malformed_lines(self, tmp_path: Path):
        """Lines that are not valid JSON are silently skipped."""
        p = tmp_path / "malformed.jsonl"
        p.write_text(
            '{"good": true}\nNOT JSON\n{"also": "good"}\n',
            encoding="utf-8",
        )
        result = list(read_jsonl(p))
        assert result == [{"good": True}, {"also": "good"}]

    def test_handles_empty_file(self, tmp_path: Path):
        """An empty file produces an empty iterator without raising."""
        p = tmp_path / "empty.jsonl"
        p.write_text("", encoding="utf-8")
        result = list(read_jsonl(p))
        assert result == []

    def test_returns_iterator(self, tmp_path: Path):
        """read_jsonl returns an iterator (has both __iter__ and __next__)."""
        p = tmp_path / "iter.jsonl"
        p.write_text('{"z": 0}\n', encoding="utf-8")
        it = read_jsonl(p)
        assert hasattr(it, "__iter__") and hasattr(it, "__next__")


# =============================================================================
# TestWriteJsonl
# =============================================================================


class TestWriteJsonl:
    """Verify write_jsonl produces correct JSONL output."""

    def test_writes_and_roundtrips(self, tmp_path: Path):
        """Entries written by write_jsonl can be read back by read_jsonl."""
        p = tmp_path / "roundtrip.jsonl"
        entries = [{"id": 1, "val": "a"}, {"id": 2, "val": "b"}, {"id": 3, "val": "c"}]
        write_jsonl(p, entries)
        result = list(read_jsonl(p))
        assert result == entries

    def test_writes_empty_list(self, tmp_path: Path):
        """Writing an empty list produces an empty file."""
        p = tmp_path / "empty_out.jsonl"
        write_jsonl(p, [])
        assert p.read_text(encoding="utf-8") == ""

    def test_preserves_unicode(self, tmp_path: Path):
        """Unicode characters (including non-ASCII) are preserved without escaping."""
        p = tmp_path / "unicode.jsonl"
        entries = [{"msg": "héllo wörld 日本語"}]
        write_jsonl(p, entries)
        raw = p.read_text(encoding="utf-8")
        # ensure_ascii=False means the chars appear literally, not as \uXXXX
        assert "héllo wörld 日本語" in raw
        # and round-trip is faithful
        result = list(read_jsonl(p))
        assert result == entries


# =============================================================================
# TestLoadTranscript
# =============================================================================


class TestLoadTranscript:
    """Verify load_transcript reads messages from transcript.jsonl."""

    def test_loads_messages(self, tmp_path: Path):
        """Loads 2 messages from transcript.jsonl into a list of dicts."""
        session_dir = tmp_path / "session"
        session_dir.mkdir()
        transcript = session_dir / "transcript.jsonl"
        transcript.write_text(
            '{"role": "user", "content": "hello"}\n{"role": "assistant", "content": "hi"}\n',
            encoding="utf-8",
        )
        result = load_transcript(session_dir)
        assert result == [
            {"role": "user", "content": "hello"},
            {"role": "assistant", "content": "hi"},
        ]

    def test_loads_empty_transcript(self, tmp_path: Path):
        """Loads an empty transcript.jsonl and returns an empty list."""
        session_dir = tmp_path / "session"
        session_dir.mkdir()
        transcript = session_dir / "transcript.jsonl"
        transcript.write_text("", encoding="utf-8")
        result = load_transcript(session_dir)
        assert result == []


# =============================================================================
# TestLoadTranscriptWithLines
# =============================================================================


class TestLoadTranscriptWithLines:
    """Verify load_transcript_with_lines injects 1-based line numbers."""

    def test_adds_line_num_to_each_entry(self, tmp_path: Path):
        """Each entry gets a line_num key matching its 1-based line position."""
        session_dir = tmp_path / "session"
        session_dir.mkdir()
        transcript = session_dir / "transcript.jsonl"
        transcript.write_text(
            '{"role": "user", "content": "hello"}\n{"role": "assistant", "content": "hi"}\n',
            encoding="utf-8",
        )
        result = load_transcript_with_lines(session_dir)
        assert result[0]["line_num"] == 1
        assert result[1]["line_num"] == 2

    def test_blank_lines_preserve_original_line_numbers(self, tmp_path: Path):
        """Blank lines are skipped; entries get the line numbers of their actual lines."""
        session_dir = tmp_path / "session"
        session_dir.mkdir()
        transcript = session_dir / "transcript.jsonl"
        # Line 1: entry, Line 2: blank, Line 3: entry
        transcript.write_text(
            '{"role": "user", "content": "hello"}\n\n{"role": "assistant", "content": "hi"}\n',
            encoding="utf-8",
        )
        result = load_transcript_with_lines(session_dir)
        assert len(result) == 2
        assert result[0]["line_num"] == 1
        assert result[1]["line_num"] == 3

    def test_malformed_json_raises_value_error_with_line_number(self, tmp_path: Path):
        """Malformed JSON on line 2 raises ValueError mentioning 'line 2'."""
        session_dir = tmp_path / "session"
        session_dir.mkdir()
        transcript = session_dir / "transcript.jsonl"
        transcript.write_text(
            '{"role": "user", "content": "hello"}\nNOT JSON\n{"role": "assistant", "content": "hi"}\n',
            encoding="utf-8",
        )
        with pytest.raises(ValueError, match="line 2"):
            load_transcript_with_lines(session_dir)


# =============================================================================
# TestLoadMetadata
# =============================================================================


class TestLoadMetadata:
    """Verify load_metadata reads metadata.json into a dict."""

    def test_loads_metadata_fields(self, tmp_path: Path):
        """Loads metadata.json with session_id, bundle, and model fields."""
        session_dir = tmp_path / "session"
        session_dir.mkdir()
        metadata = session_dir / "metadata.json"
        metadata.write_text(
            '{"session_id": "abc123", "bundle": "my-bundle", "model": "gpt-4"}',
            encoding="utf-8",
        )
        result = load_metadata(session_dir)
        assert result == {
            "session_id": "abc123",
            "bundle": "my-bundle",
            "model": "gpt-4",
        }


# =============================================================================
# TestWriteTranscript
# =============================================================================


class TestWriteTranscript:
    """Verify write_transcript persists entries to transcript.jsonl."""

    def test_roundtrips_write_then_load(self, tmp_path: Path):
        """Entries written by write_transcript can be loaded back by load_transcript."""
        session_dir = tmp_path / "session"
        if os.name != "nt":
            session_dir.mkdir()
        entries = [
            {"role": "user", "content": "hi"},
            {"role": "assistant", "content": "hello"},
        ]
        write_transcript(session_dir, entries)
        result = load_transcript(session_dir)
        assert result == entries
        if os.name == "nt":
            from amplifier_foundation.session.history import SessionHistoryStore

            assert SessionHistoryStore(session_dir).load_messages() == entries

    def test_windows_write_transcript_routes_to_private_history_preserving_roles(self, tmp_path, monkeypatch):
        from types import SimpleNamespace
        from amplifier_foundation.session import _windows_history, store

        calls = []

        def publish(path, content, **kwargs):
            calls.append((path, content, kwargs))

        monkeypatch.setattr(store, "os", SimpleNamespace(name="nt"))
        monkeypatch.setattr(_windows_history, "write_prepared", publish)
        monkeypatch.setattr(store, "write_jsonl", lambda *a: pytest.fail("generic JSONL writer"))
        entries = [{"role": "system", "content": "context"},
                   {"role": "developer", "content": "instructions"},
                   {"role": "user", "content": "question"}]
        write_transcript(tmp_path / "missing", entries)
        import json

        assert len(calls) == 1
        path, content, kwargs = calls[0]
        assert path == tmp_path / "missing" / "transcript.jsonl"
        assert [json.loads(line) for line in content.splitlines()] == entries
        assert kwargs == {"preserve_backup": True}
        assert not (tmp_path / "missing").exists()  # Adapter mock performed no I/O.

    @pytest.mark.parametrize("preserve_backup", [False, True])
    def test_windows_unsafe_intent_is_refused_before_primary_or_backup_changes(self, tmp_path, monkeypatch, preserve_backup):
        from contextlib import contextmanager
        from amplifier_foundation.session import _windows_history as backend
        from amplifier_foundation.session.shared_state import SharedStateError

        primary = tmp_path / "transcript.jsonl"
        backup = tmp_path / "transcript.jsonl.backup"
        pending = tmp_path / "transcript.jsonl.append-pending"
        primary.write_bytes(b"corrupt primary\n")
        backup.write_bytes(b'{"role":"user","content":"good backup"}\n')
        pending.write_bytes(b"unsafe intent")
        before = {path.name: path.read_bytes() for path in tmp_path.iterdir()}
        calls = []

        @contextmanager
        def unsafe(path, **kwargs):
            assert path == pending and kwargs == {"remove": True}
            calls.append("intent-security")
            raise SharedStateError("unsafe intent ACL")
            yield  # pragma: no cover

        def replaced(path, chunks):
            calls.append("replace")
            path.write_bytes(b"".join(chunks))

        def copied(source, target):
            calls.append("copy")
            target.write_bytes(source.read_bytes())

        monkeypatch.setattr(backend, "_file", unsafe)
        monkeypatch.setattr(backend, "stamp", lambda path: object())
        monkeypatch.setattr(backend, "copy_backup", copied)
        monkeypatch.setattr(backend, "_replace", replaced)
        with pytest.raises(SharedStateError, match="unsafe intent"):
            backend.write_prepared(primary, '{"role":"user","content":"repaired"}\n', preserve_backup)
        assert before == {path.name: path.read_bytes() for path in tmp_path.iterdir()}
        assert calls == ["intent-security"]

    @pytest.mark.parametrize("phase", ["missing", "success", "replace-failure", "remove-failure"])
    def test_windows_repair_keeps_valid_intent_handle_until_successful_replace(self, tmp_path, monkeypatch, phase):
        import ctypes as C
        from contextlib import contextmanager
        from types import SimpleNamespace
        from amplifier_foundation.session import _windows_history as backend, store

        primary = tmp_path / "transcript.jsonl"
        pending = tmp_path / "transcript.jsonl.append-pending"
        backup = tmp_path / "transcript.jsonl.backup"
        primary.write_bytes(b"corrupt primary")
        backup.write_bytes(b'{"role":"user","content":"good backup"}\n')
        if phase != "missing":
            pending.write_bytes(b"private intent")
        before_backup = backup.read_bytes()
        calls = []
        handle = (1 << 40) if C.sizeof(C.c_void_p) == 8 else 123

        class API:
            def SetFileInformationByHandle(self, actual, info_class, flags, size):
                assert actual == handle and info_class == 21
                assert C.cast(flags, C.POINTER(backend.win.DWORD))[0] == 3
                assert size == 4 and calls[-1] == "replace"
                calls.append("remove")
                if phase == "remove-failure":
                    raise OSError("synthetic intent removal failure")
                pending.unlink()
                return True

            ok = staticmethod(backend.win._API.ok)

        @contextmanager
        def validated(path, **kwargs):
            assert path == pending and kwargs == {"remove": True}
            calls.append("intent-security")
            if phase == "missing":
                raise FileNotFoundError(path)
            try:
                yield API(), handle
            finally:
                calls.append("close-intent")

        def replace(path, chunks):
            assert calls[-1] == "intent-security"
            calls.append("replace")
            if phase == "replace-failure":
                raise OSError("synthetic primary replacement failure")
            path.write_bytes(b"".join(chunks))

        monkeypatch.setattr(store, "os", SimpleNamespace(name="nt"))
        monkeypatch.setattr(backend, "_file", validated)
        monkeypatch.setattr(backend, "_replace", replace)
        monkeypatch.setattr(backend, "copy_backup", lambda *a: pytest.fail("corrupt primary rotated"))
        if phase.endswith("failure"):
            with pytest.raises(OSError, match="synthetic"):
                write_transcript(tmp_path, [{"role": "user", "content": "repaired"}])
        else:
            write_transcript(tmp_path, [{"role": "user", "content": "repaired"}])
        assert backup.read_bytes() == before_backup
        if phase == "replace-failure":
            assert primary.read_bytes() == b"corrupt primary"
            assert pending.exists() and "remove" not in calls
        else:
            assert load_transcript(tmp_path) == [{"role": "user", "content": "repaired"}]
            assert pending.exists() == (phase == "remove-failure")
        if phase != "missing":
            assert calls[-1] == "close-intent"
        if phase in {"success", "remove-failure"}:
            assert calls == ["intent-security", "replace", "remove", "close-intent"]

    @pytest.mark.skipif(os.name != "nt", reason="requires actual Windows local NTFS")
    @pytest.mark.parametrize("unsafe", ["foreign-acl", "hardlink", "junction"])
    def test_native_repair_unsafe_intent_preserves_primary_and_good_backup(self, tmp_path, unsafe):
        import ctypes as C
        import subprocess
        from amplifier_foundation.session import _windows_history as backend
        from amplifier_foundation.session.history import SessionHistoryStore
        from amplifier_foundation.session.shared_state import SharedStateError

        directory = tmp_path / "private-repair"
        store = SessionHistoryStore(directory)
        store.save([{"role": "user", "content": "good backup"}], {})
        store.save([{"role": "user", "content": "newer"}], {})
        pending = directory / "transcript.jsonl.append-pending"
        backend.write_pending(pending)
        if unsafe == "hardlink":
            os.link(pending, tmp_path / "intent-alias")
        elif unsafe == "foreign-acl":
            api = backend._API()
            win = backend.win
            descriptor = win.PVOID()
            api.ok(api.ConvertStringSecurityDescriptorToSecurityDescriptorW(
                f"O:{api.sid}D:P(A;;FA;;;{api.sid})(A;;GR;;;WD)",
                1, C.byref(descriptor), None,
            ))
            setter = api.security.SetFileSecurityW
            setter.argtypes, setter.restype = [C.c_wchar_p, win.DWORD, win.PVOID], win.BOOL
            try:
                api.ok(setter(str(pending), 0x80000005, descriptor))
            finally:
                api.LocalFree(descriptor)
        else:
            pending.unlink()
            actual = tmp_path / "intent-target"
            actual.mkdir()
            result = subprocess.run(["cmd.exe", "/d", "/c", "mklink", "/J", str(pending), str(actual)],
                                    capture_output=True, text=True, timeout=10)
            assert result.returncode == 0, result.stderr
        store.transcript_path.write_bytes(b"corrupt primary\n")
        backup = directory / "transcript.jsonl.backup"
        before = store.transcript_path.read_bytes(), backup.read_bytes()
        # Opening a directory reparse point as a file may fail in CreateFileW
        # before handle validation. Both are fail-closed, before publication.
        with pytest.raises((SharedStateError, OSError) if unsafe == "junction" else SharedStateError):
            write_transcript(directory, [{"role": "user", "content": "must not publish"}])
        assert before == (store.transcript_path.read_bytes(), backup.read_bytes())
        assert pending.exists()
        assert not list(directory.glob(".history-*"))

    @pytest.mark.skipif(os.name != "nt", reason="requires actual Windows local NTFS")
    def test_native_write_transcript_preserves_roles_and_good_backup_during_repair(self, tmp_path):
        from amplifier_foundation.session.history import SessionHistoryStore
        from amplifier_foundation.session import _windows_history

        directory = tmp_path / "private-writer"
        entries = [{"role": "system", "content": "synthetic context"},
                   {"role": "developer", "content": "synthetic instructions"},
                   {"role": "user", "content": "synthetic question"}]
        write_transcript(directory, entries)
        history = SessionHistoryStore(directory)
        assert history.load_messages() == entries
        assert not (directory / "transcript.jsonl.backup").exists()
        # A prior runtime checkpoint established this good backup. The explicit
        # repair writer must not rotate the damaged primary over it.
        history.save_messages(entries + [{"role": "assistant", "content": "old"}], preserve_system=True)
        backup = directory / "transcript.jsonl.backup"
        before = backup.read_bytes()
        history.transcript_path.write_bytes(b"corrupt primary\n")
        _windows_history.write_pending(directory / "transcript.jsonl.append-pending")
        write_transcript(directory, entries + [{"role": "assistant", "content": "answer"}])
        assert history.load_messages() == entries + [{"role": "assistant", "content": "answer"}]
        assert backup.read_bytes() == before
        assert not (directory / "transcript.jsonl.append-pending").exists()

    @pytest.mark.skipif(os.name != "nt", reason="requires actual Windows local NTFS")
    def test_native_write_transcript_refuses_unsafe_existing_directory(self, tmp_path):
        from amplifier_foundation.session.shared_state import SharedStateError

        directory = tmp_path / "unsafe-legacy"
        directory.mkdir()  # Deliberately NOT a valid-state private fixture.
        path = directory / "transcript.jsonl"
        path.write_bytes(b'{"role":"user","content":"preserve"}\n')
        before = path.read_bytes()
        with pytest.raises(SharedStateError):
            write_transcript(directory, [{"role": "user", "content": "must not replace"}])
        assert path.read_bytes() == before
        assert not (directory / "transcript.jsonl.backup").exists()


# =============================================================================
# TestWriteMetadata
# =============================================================================


class TestWriteMetadata:
    """Verify write_metadata persists metadata to metadata.json."""

    def test_roundtrips_write_then_load(self, tmp_path: Path):
        """Metadata written by write_metadata can be loaded back by load_metadata."""
        session_dir = tmp_path / "session"
        if os.name != "nt":
            session_dir.mkdir()
        metadata = {
            "session_id": "xyz789",
            "bundle": "test-bundle",
            "model": "claude-3",
        }
        write_metadata(session_dir, metadata)
        result = load_metadata(session_dir)
        assert result == metadata
        if os.name == "nt":
            from amplifier_foundation.session.history import SessionHistoryStore

            assert SessionHistoryStore(session_dir).load_metadata() == metadata

    def test_output_is_pretty_printed(self, tmp_path: Path):
        """write_metadata uses indent=2 so the output contains newlines."""
        session_dir = tmp_path / "session"
        if os.name != "nt":
            session_dir.mkdir()
        metadata = {"session_id": "xyz789", "bundle": "test-bundle"}
        write_metadata(session_dir, metadata)
        raw = (session_dir / "metadata.json").read_text(encoding="utf-8")
        assert "\n" in raw

    @pytest.mark.skipif(os.name != "nt", reason="requires actual Windows local NTFS")
    def test_native_write_metadata_refuses_unsafe_existing_directory(self, tmp_path):
        from amplifier_foundation.session.shared_state import SharedStateError

        directory = tmp_path / "unsafe-legacy"
        directory.mkdir()  # Unsafe inherited ACL must not be repaired to pass CI.
        with pytest.raises(SharedStateError):
            write_metadata(directory, {"must": "not publish"})
        assert not (directory / "metadata.json").exists()


# =============================================================================
# TestBackup
# =============================================================================


class TestBackup:
    """Verify backup creates a timestamped copy of a file."""

    def test_creates_backup_with_label_and_original_content(self, tmp_path: Path):
        """backup() creates a file with 'bak-pre-repair-' in the name and the original content."""
        original = tmp_path / "transcript.jsonl"
        original.write_text('{"role": "user", "content": "hello"}\n', encoding="utf-8")
        result = backup(original, "pre-repair")
        assert result is not None
        assert "bak-pre-repair-" in result.name
        assert (
            result.read_text(encoding="utf-8")
            == '{"role": "user", "content": "hello"}\n'
        )

    def test_returns_none_for_missing_file(self, tmp_path: Path):
        """backup() returns None when the target file does not exist."""
        missing = tmp_path / "nonexistent.jsonl"
        result = backup(missing, "pre-repair")
        assert result is None

    def test_backup_name_format(self, tmp_path: Path):
        """Backup file is named '<original>.bak-pre-rewind-YYYYMMDDHHMMSS' (14-digit timestamp)."""
        original = tmp_path / "transcript.jsonl"
        original.write_text('{"role": "user"}\n', encoding="utf-8")
        result = backup(original, "pre-rewind")
        assert result is not None
        # Name should match: transcript.jsonl.bak-pre-rewind-YYYYMMDDHHMMSS
        pattern = r"^transcript\.jsonl\.bak-pre-rewind-\d{14}$"
        assert re.match(pattern, result.name), f"Unexpected backup name: {result.name}"
