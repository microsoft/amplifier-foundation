"""YAML frontmatter parsing and @mention extraction.

Handles two file formats:

* ``.md`` files with ``---`` delimited YAML frontmatter + markdown body
* ``.yaml`` / ``.yml`` files where the entire file is YAML (body is ``""``)
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import yaml


def parse_frontmatter(path: Path) -> tuple[dict[str, Any], str]:
    """Parse YAML frontmatter from a ``.md`` or ``.yaml`` file.

    For ``.yaml`` / ``.yml`` files the entire content is parsed as YAML and
    the body is an empty string.

    For ``.md`` files the ``---`` delimited frontmatter is parsed and
    everything after the closing ``---`` is returned as the body.

    Returns:
        ``(frontmatter_dict, body_str)``
    """
    content = path.read_text(encoding="utf-8")

    if path.suffix in (".yaml", ".yml"):
        data = yaml.safe_load(content) or {}
        return data, ""

    # Markdown with frontmatter
    if content.startswith("---"):
        parts = content.split("---", 2)
        if len(parts) >= 3:
            data = yaml.safe_load(parts[1]) or {}
            return data, parts[2]
        if len(parts) == 2:
            data = yaml.safe_load(parts[1]) or {}
            return data, ""

    return {}, content


# Mention regex — matches @namespace:path
_MENTION_RE = re.compile(r"@([a-zA-Z0-9_][a-zA-Z0-9_.-]*):([a-zA-Z0-9_./-]+)")


def extract_mentions(text: str) -> list[str]:
    """Extract @namespace:path mentions from text.

    Code blocks (fenced and inline) are stripped before scanning.
    Duplicates are removed, preserving first-seen order.
    """
    cleaned = _strip_code(text)
    seen: set[str] = set()
    mentions: list[str] = []
    for match in _MENTION_RE.finditer(cleaned):
        mention = f"@{match.group(1)}:{match.group(2)}"
        if mention not in seen:
            seen.add(mention)
            mentions.append(mention)
    return mentions


# Delegation regex — namespace:agent-name (no slashes or dots = not a file path)
# Negative lookahead (?![/.]) prevents matching when the agent part is actually
# a path segment (e.g. "foundation:context/file.md" → matches "foundation:context"
# which is then rejected because "/" follows the match).
_DELEGATION_RE = re.compile(r"\b([a-z][a-z0-9-]*):([a-z][a-z0-9-]+)\b(?![/.])")


def extract_delegation_targets(text: str) -> list[str]:
    """Extract namespace:agent-name delegation patterns from text.

    Filters out URL prefixes (http, https, git) and file-path patterns
    where the match is immediately followed by ``/`` or ``.``.
    Code blocks (fenced and inline) are stripped before scanning.
    Duplicates are removed, preserving first-seen order.
    """
    cleaned = _strip_code(text)
    seen: set[str] = set()
    targets: list[str] = []
    for match in _DELEGATION_RE.finditer(cleaned):
        namespace = match.group(1)
        if namespace in ("http", "https", "git"):
            continue
        full = match.group(0)
        if full not in seen:
            seen.add(full)
            targets.append(full)
    return targets


def resolve_local_mention(mention: str, repo_root: Path) -> Path | None:
    """Resolve an @namespace:path mention to a local file.

    Tries the path as-is under repo_root, then with .md, .yaml,
    and .yml suffixes appended.

    Returns:
        Resolved absolute Path, or None if no file found.
    """
    if not mention.startswith("@"):
        return None

    bare = mention[1:]  # strip leading @
    if ":" not in bare:
        return None

    _namespace, rel_path = bare.split(":", 1)

    candidates = [
        repo_root / rel_path,
        repo_root / f"{rel_path}.md",
        repo_root / f"{rel_path}.yaml",
        repo_root / f"{rel_path}.yml",
    ]
    for candidate in candidates:
        if candidate.exists():
            return candidate.resolve()
    return None


def _strip_code(text: str) -> str:
    """Remove fenced and inline code blocks from text."""
    cleaned = re.sub(r"```[\s\S]*?```", "", text)
    cleaned = re.sub(r"`[^`]+`", "", cleaned)
    return cleaned
