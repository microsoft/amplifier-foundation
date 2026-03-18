"""Tests for amplifier_foundation.session.store — filename constants and JSONL I/O."""

from __future__ import annotations

from pathlib import Path

from amplifier_foundation.session.store import (
    EVENTS_FILENAME,
    METADATA_FILENAME,
    TRANSCRIPT_FILENAME,
    read_jsonl,
    write_jsonl,
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
        """read_jsonl returns an iterator (has __next__)."""
        p = tmp_path / "iter.jsonl"
        p.write_text('{"z": 0}\n', encoding="utf-8")
        it = read_jsonl(p)
        assert hasattr(it, "__next__")


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
