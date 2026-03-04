#!/usr/bin/env python3
"""Session transcript repair tool.

Diagnoses and repairs broken Amplifier session transcripts that have:
  1. Missing tool results (orphaned tool_use blocks)
  2. Misplaced tool results (ordering violations)
  3. Incomplete assistant turns (missing final response)

Usage:
    python scripts/session-repair.py <session_dir> --diagnose
    python scripts/session-repair.py <session_dir> --repair
    python scripts/session-repair.py <session_dir> --rewind

Exit codes:
    0 = success (or healthy on --diagnose)
    1 = repair needed (--diagnose) or repair failed
    2 = invalid arguments
"""

from __future__ import annotations

import json
from pathlib import Path


# ─────────────────────────────────────────────────────────────────────
# Core data model
# ─────────────────────────────────────────────────────────────────────


def parse_transcript(transcript_path: Path) -> list[dict]:
    """Read transcript.jsonl and return a list of entries with line numbers.

    Each returned dict is the original JSON object with an added ``line_num``
    key (1-based) indicating its position in the file.
    """
    transcript_path = Path(transcript_path)
    entries: list[dict] = []
    with open(transcript_path) as f:
        for i, raw_line in enumerate(f, start=1):
            raw_line = raw_line.strip()
            if not raw_line:
                continue
            entry = json.loads(raw_line)
            entry["line_num"] = i
            entries.append(entry)
    return entries


def build_tool_index(entries: list[dict]) -> dict:
    """Build an index of tool_use IDs and tool_result IDs from parsed entries.

    Returns::

        {
            "tool_uses": {
                "<id>": {"line_num": N, "tool_name": "...", "entry_index": M},
                ...
            },
            "tool_results": {
                "<id>": {"line_num": N, "entry_index": M},
                ...
            },
        }
    """
    tool_uses: dict[str, dict] = {}
    tool_results: dict[str, dict] = {}

    for idx, entry in enumerate(entries):
        # Assistant messages may contain tool_calls
        if entry.get("role") == "assistant" and "tool_calls" in entry:
            for tc in entry["tool_calls"]:
                tc_id = tc.get("id", "")
                tool_name = tc.get("function", {}).get("name", "unknown")
                tool_uses[tc_id] = {
                    "line_num": entry["line_num"],
                    "tool_name": tool_name,
                    "entry_index": idx,
                }

        # Tool role messages are results
        if entry.get("role") == "tool" and "tool_call_id" in entry:
            tool_results[entry["tool_call_id"]] = {
                "line_num": entry["line_num"],
                "entry_index": idx,
            }

    return {"tool_uses": tool_uses, "tool_results": tool_results}


def is_real_user_message(entry: dict) -> bool:
    """Return True if *entry* represents a genuine human user message.

    A "real user message" is:
    - ``role`` is ``"user"``
    - No ``tool_call_id`` field (that would make it a tool result)
    - Content is NOT wrapped in ``<system-reminder>`` tags
    """
    if entry.get("role") != "user":
        return False
    if "tool_call_id" in entry:
        return False

    content = entry.get("content", "")

    # Content can be a string or a list of content blocks
    if isinstance(content, list):
        # Check all text blocks
        for block in content:
            if isinstance(block, dict):
                text = block.get("text", "")
                if isinstance(text, str) and text.strip().startswith(
                    "<system-reminder>"
                ):
                    return False
    elif isinstance(content, str):
        if content.strip().startswith("<system-reminder>"):
            return False

    return True
