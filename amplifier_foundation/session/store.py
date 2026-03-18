"""Layer Level 2 (JSONL Store) — knows file formats, zero path-convention knowledge.

This module provides:
- Filename constants for standard session files
- Generic JSONL read/write utilities
- Session-specific I/O functions for transcripts, metadata, and backups

It operates purely on Path objects and has no knowledge of where sessions live
on disk or how directories are structured.
"""

from __future__ import annotations

import json
import shutil
from datetime import datetime, timezone
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


# ---------------------------------------------------------------------------
# Session-specific I/O
# ---------------------------------------------------------------------------


def load_transcript(session_dir: Path) -> list[dict[str, Any]]:
    """Load messages from transcript.jsonl in the given session directory.

    Args:
        session_dir: Path to the session directory containing transcript.jsonl.

    Returns:
        List of parsed message dicts.
    """
    return list(read_jsonl(session_dir / TRANSCRIPT_FILENAME))


def load_transcript_with_lines(session_dir: Path) -> list[dict[str, Any]]:
    """Load transcript entries, injecting the 1-based source line number into each.

    Unlike ``load_transcript``, this function preserves original line numbers from
    the file (blank lines are skipped but their positions are counted), and raises
    on malformed JSON instead of silently skipping.

    Args:
        session_dir: Path to the session directory containing transcript.jsonl.

    Returns:
        List of parsed message dicts, each with an injected ``line_num`` key.

    Raises:
        ValueError: If a non-blank line cannot be parsed as JSON, with a message
            that includes the 1-based line number.
    """
    entries: list[dict[str, Any]] = []
    path = session_dir / TRANSCRIPT_FILENAME
    with path.open(encoding="utf-8") as fh:
        for line_num, line in enumerate(fh, start=1):
            stripped = line.strip()
            if not stripped:
                continue
            try:
                entry = json.loads(stripped)
            except json.JSONDecodeError as exc:
                raise ValueError(f"Malformed JSON at line {line_num}: {exc}") from exc
            entry["line_num"] = line_num
            entries.append(entry)
    return entries


def write_transcript(session_dir: Path, entries: list[dict[str, Any]]) -> None:
    """Write entries to transcript.jsonl in the given session directory.

    Args:
        session_dir: Path to the session directory.
        entries: List of message dicts to write.
    """
    write_jsonl(session_dir / TRANSCRIPT_FILENAME, entries)


def load_metadata(session_dir: Path) -> dict[str, Any]:
    """Load metadata.json from the given session directory.

    Args:
        session_dir: Path to the session directory containing metadata.json.

    Returns:
        Parsed metadata dict.
    """
    return json.loads((session_dir / METADATA_FILENAME).read_text(encoding="utf-8"))


def write_metadata(session_dir: Path, metadata: dict[str, Any]) -> None:
    """Write metadata to metadata.json in the given session directory.

    Uses ``indent=2`` for human-readable pretty-printing and ``ensure_ascii=False``
    to preserve Unicode characters literally.

    Args:
        session_dir: Path to the session directory.
        metadata: Metadata dict to write.
    """
    (session_dir / METADATA_FILENAME).write_text(
        json.dumps(metadata, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )


def backup(filepath: Path, label: str) -> Path | None:
    """Create a timestamped backup copy of a file.

    The backup is placed in the same directory as the original with a name of the
    form ``<original-name>.bak-<label>-<YYYYMMDDHHMMSS>``.

    Args:
        filepath: Path to the file to back up.
        label: Label string inserted between ``bak-`` and the timestamp.

    Returns:
        Path to the backup file, or ``None`` if ``filepath`` does not exist.
    """
    if not filepath.exists():
        return None
    timestamp = datetime.now(tz=timezone.utc).strftime("%Y%m%d%H%M%S")
    backup_path = filepath.parent / f"{filepath.name}.bak-{label}-{timestamp}"
    shutil.copy2(filepath, backup_path)
    return backup_path
