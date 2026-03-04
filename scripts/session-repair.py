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
    with open(transcript_path, encoding="utf-8") as f:
        for i, raw_line in enumerate(f, start=1):
            raw_line = raw_line.strip()
            # Skip blank lines; line_num preserves original file position
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
                # Malformed tool_calls without id are indexed under ""
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


# ─────────────────────────────────────────────────────────────────────
# Diagnostic engine
# ─────────────────────────────────────────────────────────────────────


def diagnose(session_dir: Path) -> dict:
    """Analyse a session transcript and return a structured diagnosis.

    Returns a dict with keys:
        status: "healthy" | "broken"
        failure_modes: list of strings
        orphaned_tool_ids: list of tool_use IDs with no matching tool_result
        misplaced_tool_ids: list of tool_use IDs whose results are out of order
        incomplete_turns: list of dicts describing incomplete assistant turns
        recommended_action: "none" | "repair"
    """
    session_dir = Path(session_dir)
    transcript_path = session_dir / "transcript.jsonl"
    entries = parse_transcript(transcript_path)
    index = build_tool_index(entries)

    failure_modes: list[str] = []
    orphaned_tool_ids: list[str] = []
    misplaced_tool_ids: list[str] = []
    incomplete_turns: list[dict] = []

    # --- Failure mode 1: missing tool results ---
    for tc_id in index["tool_uses"]:
        if tc_id not in index["tool_results"]:
            orphaned_tool_ids.append(tc_id)
    if orphaned_tool_ids:
        failure_modes.append("missing_tool_results")

    # --- Failure mode 2: ordering violations ---
    # A tool_result is misplaced if a real user message or a different assistant
    # turn appears between the tool_use and its result.
    for tc_id, use_info in index["tool_uses"].items():
        if tc_id not in index["tool_results"]:
            continue  # already caught as orphan
        result_info = index["tool_results"][tc_id]
        use_idx = use_info["entry_index"]
        result_idx = result_info["entry_index"]

        # Check entries between the tool_use's assistant message and the result
        for between_idx in range(use_idx + 1, result_idx):
            between = entries[between_idx]
            if is_real_user_message(between):
                misplaced_tool_ids.append(tc_id)
                break
            if (
                between.get("role") == "assistant"
                and between.get("tool_calls") is None
                and between_idx != use_idx
            ):
                # A non-tool-calling assistant message in between = different turn
                misplaced_tool_ids.append(tc_id)
                break
    if misplaced_tool_ids:
        if "ordering_violation" not in failure_modes:
            failure_modes.append("ordering_violation")

    # --- Failure mode 3: incomplete assistant turns ---
    # Walk through entries looking for assistant messages with tool_calls.
    # For each, verify that after all its tool_results there is a final
    # assistant text response before the next real user message.
    for tc_id, use_info in index["tool_uses"].items():
        # Only check with the first tool_call in each assistant message
        # to avoid duplicate detection for multi-tool calls.
        assistant_idx = use_info["entry_index"]
        assistant_entry = entries[assistant_idx]
        first_tc_id = assistant_entry.get("tool_calls", [{}])[0].get("id")
        if tc_id != first_tc_id:
            continue

        # Skip if any tool_call from this assistant message is orphaned
        all_tc_ids = [tc["id"] for tc in assistant_entry.get("tool_calls", [])]
        if any(tid in orphaned_tool_ids for tid in all_tc_ids):
            continue

        # Find the last tool_result for this assistant message's tool_calls
        last_result_idx = assistant_idx
        for tid in all_tc_ids:
            if tid in index["tool_results"]:
                res_idx = index["tool_results"][tid]["entry_index"]
                if res_idx > last_result_idx:
                    last_result_idx = res_idx

        if last_result_idx == assistant_idx:
            continue  # No results found (shouldn't happen if we skipped orphans)

        # Check what comes after the last tool result
        next_idx = last_result_idx + 1
        if next_idx >= len(entries):
            # End of transcript — incomplete if we're missing the closing response
            incomplete_turns.append(
                {
                    "after_line": entries[last_result_idx]["line_num"],
                    "missing": "assistant_response",
                }
            )
            continue

        next_entry = entries[next_idx]
        if is_real_user_message(next_entry):
            # Real user message immediately after tool results = incomplete turn
            incomplete_turns.append(
                {
                    "after_line": entries[last_result_idx]["line_num"],
                    "missing": "assistant_response",
                }
            )

    if incomplete_turns:
        if "incomplete_assistant_turn" not in failure_modes:
            failure_modes.append("incomplete_assistant_turn")

    status = "broken" if failure_modes else "healthy"
    recommended = "repair" if failure_modes else "none"

    return {
        "status": status,
        "failure_modes": failure_modes,
        "orphaned_tool_ids": orphaned_tool_ids,
        "misplaced_tool_ids": misplaced_tool_ids,
        "incomplete_turns": incomplete_turns,
        "recommended_action": recommended,
    }
