"""Tests for io/files.py atomic write with backup."""

from __future__ import annotations

from pathlib import Path

from amplifier_foundation.io.files import write_with_backup


class TestWriteWithBackup:
    """Tests for write_with_backup function."""

    def test_creates_backup_of_existing_file(self, tmp_path: Path) -> None:
        """Creates backup before overwriting."""
        path = tmp_path / "test.txt"
        path.write_text("original")

        write_with_backup(path, "updated")

        assert path.read_text() == "updated"
        backup = tmp_path / "test.txt.backup"
        assert backup.exists()
        assert backup.read_text() == "original"

    def test_no_backup_for_new_file(self, tmp_path: Path) -> None:
        """No backup created for new file."""
        path = tmp_path / "test.txt"
        write_with_backup(path, "content")

        assert path.read_text() == "content"
        backup = tmp_path / "test.txt.backup"
        assert not backup.exists()

    def test_custom_backup_suffix(self, tmp_path: Path) -> None:
        """Uses custom backup suffix."""
        path = tmp_path / "test.txt"
        path.write_text("original")

        write_with_backup(path, "updated", backup_suffix=".bak")

        backup = tmp_path / "test.txt.bak"
        assert backup.exists()
        assert backup.read_text() == "original"

    def test_creates_parent_directories(self, tmp_path: Path) -> None:
        """Creates parent directories if they don't exist."""
        path = tmp_path / "a" / "b" / "test.txt"
        write_with_backup(path, "nested content")
        assert path.read_text() == "nested content"

    def test_unicode_content(self, tmp_path: Path) -> None:
        """Handles unicode content correctly."""
        path = tmp_path / "test.txt"
        content = "Hello 世界 🌍"
        write_with_backup(path, content)
        assert path.read_text(encoding="utf-8") == content

    def test_binary_mode(self, tmp_path: Path) -> None:
        """Writes binary content correctly."""
        path = tmp_path / "test.bin"
        data = b"\x00\x01\x02\xff"
        write_with_backup(path, data, mode="wb")
        assert path.read_bytes() == data
