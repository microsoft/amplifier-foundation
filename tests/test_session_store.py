"""Tests for amplifier_foundation.session.store — filename constants and JSONL I/O."""

from __future__ import annotations

import re
from pathlib import Path

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
        import pytest

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
        assert result == {"session_id": "abc123", "bundle": "my-bundle", "model": "gpt-4"}


# =============================================================================
# TestWriteTranscript
# =============================================================================


class TestWriteTranscript:
    """Verify write_transcript persists entries to transcript.jsonl."""

    def test_roundtrips_write_then_load(self, tmp_path: Path):
        """Entries written by write_transcript can be loaded back by load_transcript."""
        session_dir = tmp_path / "session"
        session_dir.mkdir()
        entries = [{"role": "user", "content": "hi"}, {"role": "assistant", "content": "hello"}]
        write_transcript(session_dir, entries)
        result = load_transcript(session_dir)
        assert result == entries


# =============================================================================
# TestWriteMetadata
# =============================================================================


class TestWriteMetadata:
    """Verify write_metadata persists metadata to metadata.json."""

    def test_roundtrips_write_then_load(self, tmp_path: Path):
        """Metadata written by write_metadata can be loaded back by load_metadata."""
        session_dir = tmp_path / "session"
        session_dir.mkdir()
        metadata = {"session_id": "xyz789", "bundle": "test-bundle", "model": "claude-3"}
        write_metadata(session_dir, metadata)
        result = load_metadata(session_dir)
        assert result == metadata

    def test_output_is_pretty_printed(self, tmp_path: Path):
        """write_metadata uses indent=2 so the output contains newlines."""
        session_dir = tmp_path / "session"
        session_dir.mkdir()
        metadata = {"session_id": "xyz789", "bundle": "test-bundle"}
        write_metadata(session_dir, metadata)
        raw = (session_dir / "metadata.json").read_text(encoding="utf-8")
        assert "\n" in raw


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
        assert result.read_text(encoding="utf-8") == '{"role": "user", "content": "hello"}\n'

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
