"""Frontmatter parsing for markdown files with YAML headers."""

from __future__ import annotations

import re
from typing import Any

import yaml


def parse_frontmatter(text: str) -> tuple[dict[str, Any], str]:
    """Parse YAML frontmatter from markdown text.

    Extracts YAML between --- delimiters at the start of the text.

    A leading UTF-8 BOM (U+FEFF) is tolerated. Editors on Windows (Notepad,
    PowerShell ``Set-Content``, ``Out-File``) write UTF-8 *with* a BOM by
    default; when such a file is decoded as plain ``utf-8`` the BOM survives
    as a leading U+FEFF character. Without stripping it the opening ``---``
    is no longer at position 0, the pattern below does not match, and the
    frontmatter is silently discarded rather than reported as an error.

    Args:
        text: Markdown text with optional YAML frontmatter.

    Returns:
        Tuple of (frontmatter_dict, body_text).
        If no frontmatter, returns ({}, original_text).

    Raises:
        yaml.YAMLError: If frontmatter contains invalid YAML.
    """
    text = text.lstrip("\ufeff")

    # Match --- at start, then content, then ---
    pattern = r"^---\s*\n(.*?)\n---\s*\n?"
    match = re.match(pattern, text, re.DOTALL)

    if not match:
        return {}, text

    frontmatter_str = match.group(1)
    body = text[match.end() :]

    frontmatter = yaml.safe_load(frontmatter_str) or {}

    return frontmatter, body
