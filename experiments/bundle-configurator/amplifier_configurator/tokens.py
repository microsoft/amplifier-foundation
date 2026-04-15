"""Token estimation utilities.

Uses Foundation's formula: len(content) // 4.
"""

from __future__ import annotations

from pathlib import Path


def estimate_tokens_for_text(text: str | None) -> int:
    """Estimate token count for a text string.

    Args:
        text: The text to estimate. None or empty returns 0.

    Returns:
        Estimated token count using len(text) // 4.
    """
    if not text:
        return 0
    return len(text) // 4


def estimate_tokens_for_file(path: Path) -> int:
    """Estimate token count for a file on disk.

    Args:
        path: Path to the file to read.

    Returns:
        Estimated token count using len(content) // 4, or 0 if the file
        cannot be read (OSError) or decoded (UnicodeDecodeError).
    """
    try:
        content = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return 0
    return len(content) // 4
