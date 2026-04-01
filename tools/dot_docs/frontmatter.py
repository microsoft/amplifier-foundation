"""YAML frontmatter parsing and @mention extraction.

Handles two file formats:

* ``.md`` files with ``---`` delimited YAML frontmatter + markdown body
* ``.yaml`` / ``.yml`` files where the entire file is YAML (body is ``""``)
"""

from __future__ import annotations

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
