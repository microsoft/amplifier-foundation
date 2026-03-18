"""Layer Level 2 (JSONL Store) — knows file formats, zero path-convention knowledge.

This module provides:
- Filename constants for standard session files
- Generic JSONL read/write utilities

It operates purely on Path objects and has no knowledge of where sessions live
on disk or how directories are structured.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Iterator

# ---------------------------------------------------------------------------
# Filename constants
# ---------------------------------------------------------------------------

TRANSCRIPT_FILENAME = "transcript.jsonl"
METADATA_FILENAME = "metadata.json"
EVENTS_FILENAME = "events.jsonl"


# ---------------------------------------------------------------------------
# JSONL I/O
# ---------------------------------------------------------------------------


def read_jsonl(path: Path) -> Iterator[dict[str, Any]]:
    """Yield parsed JSON objects from a JSONL file.

    - Opens the file with UTF-8 encoding.
    - Skips blank lines silently.
    - Skips lines that cannot be parsed as JSON silently.

    Args:
        path: Path to the JSONL file to read.

    Yields:
        Parsed dict objects, one per valid JSON line.
    """
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            stripped = line.strip()
            if not stripped:
                continue
            try:
                yield json.loads(stripped)
            except json.JSONDecodeError:
                continue


def write_jsonl(path: Path, entries: list[dict[str, Any]]) -> None:
    """Write a list of dicts to a JSONL file, one entry per line.

    - Opens the file with UTF-8 encoding in write mode (overwrites if exists).
    - Writes each entry as a JSON string followed by a newline.
    - Uses ``ensure_ascii=False`` to preserve Unicode characters literally.

    Args:
        path: Destination path to write.
        entries: List of dicts to serialise.
    """
    with path.open("w", encoding="utf-8") as fh:
        for entry in entries:
            fh.write(json.dumps(entry, ensure_ascii=False) + "\n")
