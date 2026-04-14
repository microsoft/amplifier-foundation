"""@mention extraction from text."""

from __future__ import annotations

import re


def parse_mentions(text: str) -> list[str]:
    """Extract @mentions from text, excluding code blocks.

    Finds patterns like:
    - @bundle:context-name
    - @path/to/file
    - @./relative/path

    Excludes mentions inside:
    - Inline code (`...`)
    - Fenced code blocks (```...```)

    Args:
        text: Text to extract mentions from.

    Returns:
        List of unique mentions (including @ prefix).
    """
    # Remove code blocks first
    text_without_code = _remove_code_blocks(text)

    # Find @mentions
    # Pattern: @ followed by word chars, colons, slashes, dots, hyphens, tildes
    # But not email addresses (no @ followed by domain pattern)
    pattern = r"@(?![a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,})([a-zA-Z0-9_:./\~-]+)"

    matches = re.findall(pattern, text_without_code)

    # Return unique mentions with @ prefix, preserving order
    seen: set[str] = set()
    result: list[str] = []
    for match in matches:
        mention = f"@{match}"
        if mention not in seen:
            seen.add(mention)
            result.append(mention)

    return result


def _remove_code_blocks(text: str) -> str:
    """Remove code blocks from text.

    Removes:
    - Fenced code blocks (```...```) - only when ``` is at line start
    - Inline code (`...`) - but not nested backticks like (```)

    Note: Code fences must start at the beginning of a line per CommonMark spec.
    Inline mentions of ``` (like "wrap in code fences (```)") are NOT treated as fences.
    """
    # Remove fenced code blocks - ``` must be at start of line (or start of text)
    # This prevents inline mentions like "(```)" from being treated as fence starts
    text = re.sub(r"(?:^|\n)```[^\n]*\n.*?(?:^|\n)```", "\n", text, flags=re.DOTALL | re.MULTILINE)

    # Remove inline code - single backticks with content
    # Use negative lookbehind/lookahead to avoid matching backticks that are
    # adjacent to other backticks (like in "wrap in code fences (```)")
    # (?<!`) = not preceded by backtick, (?!`) = not followed by backtick
    text = re.sub(r"(?<!`)`(?!`)[^`]+(?<!`)`(?!`)", "", text)

    return text
