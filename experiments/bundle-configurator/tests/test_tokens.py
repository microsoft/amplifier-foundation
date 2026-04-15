"""Tests for tokens.py — token estimation utilities."""

from __future__ import annotations

from pathlib import Path


from amplifier_configurator.tokens import (
    estimate_tokens_for_file,
    estimate_tokens_for_text,
)


# ---------------------------------------------------------------------------
# estimate_tokens_for_text
# ---------------------------------------------------------------------------


def test_estimate_tokens_for_text_basic() -> None:
    """400 chars should equal 100 tokens (len // 4)."""
    text = "A" * 400
    assert estimate_tokens_for_text(text) == 100


def test_estimate_tokens_for_text_empty_string() -> None:
    """Empty string should return 0."""
    assert estimate_tokens_for_text("") == 0


def test_estimate_tokens_for_text_short() -> None:
    """'abc' is 3 chars, 3 // 4 == 0."""
    assert estimate_tokens_for_text("abc") == 0


def test_estimate_tokens_for_text_none() -> None:
    """None should return 0."""
    assert estimate_tokens_for_text(None) == 0


# ---------------------------------------------------------------------------
# estimate_tokens_for_file
# ---------------------------------------------------------------------------


def test_estimate_tokens_for_file_basic(tmp_path: Path) -> None:
    """A file with 400 chars should return 100 tokens."""
    f = tmp_path / "sample.txt"
    f.write_text("A" * 400, encoding="utf-8")
    assert estimate_tokens_for_file(f) == 100


def test_estimate_tokens_for_file_missing(tmp_path: Path) -> None:
    """Missing file should return 0 (OSError caught)."""
    missing = tmp_path / "does_not_exist.txt"
    assert estimate_tokens_for_file(missing) == 0


def test_estimate_tokens_for_file_empty(tmp_path: Path) -> None:
    """Empty file should return 0."""
    f = tmp_path / "empty.txt"
    f.write_text("", encoding="utf-8")
    assert estimate_tokens_for_file(f) == 0


def test_estimate_tokens_for_file_binary(tmp_path: Path) -> None:
    """Binary file that can't be decoded as UTF-8 should return 0."""
    f = tmp_path / "binary.bin"
    f.write_bytes(bytes(range(256)))  # includes non-UTF-8 byte sequences
    assert estimate_tokens_for_file(f) == 0
