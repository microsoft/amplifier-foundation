"""Session repair tool for Amplifier transcript files.

Usage:
    python scripts/session-repair.py <session-dir>

Exit codes:
    0 - Success / session is healthy
    1 - Repair needed or repair failed
    2 - Invalid arguments
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import TypedDict


class IncompleteTurn(TypedDict):
    after_line: int
    missing: str


class DiagnosisResult(TypedDict):
    status: str  # "healthy" | "broken"
    failure_modes: list[str]
    orphaned_tool_ids: list[str]
    misplaced_tool_ids: list[str]
    incomplete_turns: list[IncompleteTurn]
    recommended_action: (
        str  # "none" | "repair" | "rewind" (rewind reserved for future use)
    )


def parse_transcript(transcript_path: Path) -> list[dict]:
    """Read transcript.jsonl and return a list of entries with line numbers.

    Each returned dict is the original JSON object with an added 'line_num' key
    (1-based) indicating its position in the file. Empty lines are skipped.
    """
    entries = []
    with open(transcript_path, encoding="utf-8") as f:
        for line_num, line in enumerate(f, start=1):
            stripped = line.strip()
            if not stripped:
                continue
            try:
                entry = json.loads(stripped)
            except json.JSONDecodeError as e:
                raise ValueError(f"Invalid JSON at line {line_num}") from e
            entry["line_num"] = line_num
            entries.append(entry)
    return entries


def build_tool_index(entries: list[dict]) -> dict:
    """Build an index of tool_use IDs and tool_result IDs from parsed entries.

    Returns:
        {
            'tool_uses': {'<id>': {'line_num': N, 'tool_name': '...', 'entry_index': M}},
            'tool_results': {'<id>': {'line_num': N, 'entry_index': M}},
        }
    """
    tool_uses = {}
    tool_results = {}

    for idx, entry in enumerate(entries):
        # Extract tool_use IDs from assistant messages with tool_calls
        if entry.get("role") == "assistant" and "tool_calls" in entry:
            for tool_call in entry["tool_calls"]:
                call_id = tool_call.get("id", "")
                tool_name = tool_call.get("function", {}).get("name", "")
                tool_uses[call_id] = {
                    "line_num": entry["line_num"],
                    "tool_name": tool_name,
                    "entry_index": idx,
                }

        # Extract tool_result IDs from tool role messages
        elif entry.get("role") == "tool" and "tool_call_id" in entry:
            tool_results[entry["tool_call_id"]] = {
                "line_num": entry["line_num"],
                "entry_index": idx,
            }

    return {"tool_uses": tool_uses, "tool_results": tool_results}


def is_real_user_message(entry: dict) -> bool:
    """Return True if entry represents a genuine human user message.

    A 'real user message' is:
    - role is 'user'
    - no tool_call_id field
    - content is NOT wrapped in <system-reminder> tags

    Content can be a string or a list of content blocks.
    Assistant messages and tool role messages return False.
    """
    if entry.get("role") != "user":
        return False

    if "tool_call_id" in entry:
        return False

    content = entry.get("content", "")

    # Check for system-reminder tags in string content
    if isinstance(content, str):
        stripped = content.strip()
        if stripped.startswith("<system-reminder>"):
            return False

    # Check for system-reminder tags in list content
    elif isinstance(content, list):
        for block in content:
            text = block.get("text", "")
            if isinstance(text, str) and text.strip().startswith("<system-reminder>"):
                return False

    return True


# ---------------------------------------------------------------------------
# Diagnostic engine
# ---------------------------------------------------------------------------


def _has_intervening_disruption(
    entries: list[dict], use_idx: int, result_idx: int
) -> bool:
    """Return True if a real user message or unrelated assistant turn appears
    between entry indices *use_idx* and *result_idx*."""
    for between_idx in range(use_idx + 1, result_idx):
        between = entries[between_idx]
        if is_real_user_message(between):
            return True
        if between.get("role") == "assistant" and "tool_calls" not in between:
            return True
    return False


def diagnose(session_dir: Path | str) -> DiagnosisResult:
    """Analyse a session transcript and return a structured diagnosis.

    Returns a DiagnosisResult with keys:
        status: "healthy" | "broken"
        failure_modes: list of strings
        orphaned_tool_ids: list of tool_use IDs with no matching tool_result
        misplaced_tool_ids: list of tool_use IDs whose results are out of order
        incomplete_turns: list of IncompleteTurn dicts with 'after_line' and 'missing'
        recommended_action: "none" | "repair" ("rewind" reserved for future use)
    """
    session_dir = Path(session_dir)
    transcript_path = session_dir / "transcript.jsonl"
    entries = parse_transcript(transcript_path)
    index = build_tool_index(entries)

    failure_modes: list[str] = []
    orphaned_tool_ids: list[str] = []
    misplaced_tool_ids: list[str] = []
    incomplete_turns: list[IncompleteTurn] = []

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

        if _has_intervening_disruption(entries, use_idx, result_idx):
            misplaced_tool_ids.append(tc_id)
    if misplaced_tool_ids:
        failure_modes.append("ordering_violation")

    # --- Failure mode 3: incomplete assistant turns ---
    # Walk through entries looking for assistant messages with tool_calls.
    # For each, verify that after all its tool_results there is a final
    # assistant text response before the next real user message.
    orphaned_set = set(orphaned_tool_ids)
    misplaced_set = set(misplaced_tool_ids)
    for tc_id, use_info in index["tool_uses"].items():
        # Only check with the first tool_call in each assistant message
        # to avoid duplicate detection for multi-tool calls.
        assistant_idx = use_info["entry_index"]
        assistant_entry = entries[assistant_idx]
        first_tc_id = assistant_entry.get("tool_calls", [{}])[0].get("id")
        if tc_id != first_tc_id:
            continue

        # Skip if any tool_call from this assistant message is orphaned/misplaced
        all_tc_ids = [tc["id"] for tc in assistant_entry.get("tool_calls", [])]
        if any(tid in orphaned_set or tid in misplaced_set for tid in all_tc_ids):
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


# ---------------------------------------------------------------------------
# Repair engine
# ---------------------------------------------------------------------------


SYNTHETIC_TOOL_RESULT_CONTENT = json.dumps(
    {
        "error": "unknown_error",
        "message": "Tool execution was interrupted and no result was captured.",
    }
)

SYNTHETIC_ASSISTANT_RESPONSE: dict = {
    "role": "assistant",
    "content": [
        {
            "type": "text",
            "text": (
                "The previous tool calls were interrupted. "
                "This response was automatically repaired."
            ),
        }
    ],
}


def _make_synthetic_tool_result(tool_call_id: str, tool_name: str) -> dict:
    """Create a synthetic tool result for an orphaned or misplaced tool call."""
    return {
        "role": "tool",
        "tool_call_id": tool_call_id,
        "name": tool_name,
        "content": SYNTHETIC_TOOL_RESULT_CONTENT,
    }


def _make_synthetic_assistant_response() -> dict:
    """Create a fresh synthetic assistant response for an incomplete turn.

    Returns a fresh copy to avoid shared mutable state between injected entries.
    """
    return {
        "role": SYNTHETIC_ASSISTANT_RESPONSE["role"],
        "content": [dict(block) for block in SYNTHETIC_ASSISTANT_RESPONSE["content"]],
    }


def _strip_line_num(entry: dict) -> dict:
    """Return a copy of *entry* without the internal ``line_num`` key."""
    d = entry.copy()
    d.pop("line_num", None)
    return d


def repair_transcript(entries: list[dict], diagnosis: DiagnosisResult) -> list[dict]:
    """Apply the COMPLETE repair strategy to a parsed transcript.

    Returns a new list of entries (without ``line_num`` keys) with:
    - synthetic tool results injected for orphaned/misplaced tool calls
    - synthetic assistant responses where needed
    - misplaced tool results removed
    """
    # 1. Healthy transcripts are returned unchanged (minus line_num).
    if diagnosis["status"] == "healthy":
        return [_strip_line_num(e) for e in entries]

    orphaned_set = set(diagnosis["orphaned_tool_ids"])
    misplaced_set = set(diagnosis["misplaced_tool_ids"])
    broken_set = orphaned_set | misplaced_set
    incomplete_after_lines = {t["after_line"] for t in diagnosis["incomplete_turns"]}

    # 2. Build skip_indices — entry indices of misplaced tool results.
    skip_indices: set[int] = set()
    for idx, entry in enumerate(entries):
        if entry.get("role") == "tool" and entry.get("tool_call_id") in misplaced_set:
            skip_indices.add(idx)

    # 3–6. Walk entries, applying repairs.
    result: list[dict] = []
    for idx, entry in enumerate(entries):
        # 3. Skip misplaced tool results.
        if idx in skip_indices:
            continue

        result.append(_strip_line_num(entry))

        # 4. After assistant messages with tool_calls: inject synthetic
        #    results for orphaned / misplaced tool_call IDs.
        if entry.get("role") == "assistant" and "tool_calls" in entry:
            tool_calls = entry["tool_calls"]
            all_tc_ids = [tc["id"] for tc in tool_calls]
            broken_in_msg = [tc for tc in tool_calls if tc["id"] in broken_set]

            for tc in broken_in_msg:
                tc_id = tc["id"]
                tc_name = tc.get("function", {}).get("name", "")
                result.append(_make_synthetic_tool_result(tc_id, tc_name))

            # 5. If ALL tool_calls from this message were broken AND
            #    the next non-skipped entry is a real user message (or end
            #    of transcript): inject a synthetic assistant response.
            if len(broken_in_msg) == len(all_tc_ids) and all_tc_ids:
                next_entry = None
                for future_idx in range(idx + 1, len(entries)):
                    if future_idx not in skip_indices:
                        next_entry = entries[future_idx]
                        break
                if next_entry is None or is_real_user_message(next_entry):
                    result.append(_make_synthetic_assistant_response())

        # 6. After tool results at incomplete_after_lines: inject synthetic
        #    assistant response.  (entry still has line_num; only the
        #    appended copy is stripped.)
        elif (
            entry.get("role") == "tool"
            and entry.get("line_num") in incomplete_after_lines
        ):
            result.append(_make_synthetic_assistant_response())

    return result


# ---------------------------------------------------------------------------
# Rewind engine
# ---------------------------------------------------------------------------


def rewind_transcript(entries: list[dict], diagnosis: DiagnosisResult) -> list[dict]:
    """Truncate transcript to before the last real user message prior to issues.

    If the transcript is healthy, return all entries stripped of ``line_num``.
    Otherwise, find the earliest issue, walk backwards to find the last real
    user message before it, and return ``entries[:rewind_to_idx]`` stripped.

    Returns an empty list if the first turn itself is broken
    (``rewind_to_idx`` is ``None`` or ``0``).
    """
    if diagnosis["status"] == "healthy":
        return [_strip_line_num(e) for e in entries]

    # 1. Find earliest_issue_idx from orphaned, misplaced, and incomplete turns.
    index = build_tool_index(entries)
    issue_indices: list[int] = []

    # Orphaned tool_ids → entry_index of the tool_use (assistant message)
    for tc_id in diagnosis["orphaned_tool_ids"]:
        if tc_id in index["tool_uses"]:
            issue_indices.append(index["tool_uses"][tc_id]["entry_index"])

    # Misplaced tool_ids → entry_index of the tool_use (assistant message)
    for tc_id in diagnosis["misplaced_tool_ids"]:
        if tc_id in index["tool_uses"]:
            issue_indices.append(index["tool_uses"][tc_id]["entry_index"])

    # Incomplete turns → walk back from after_line to find assistant with tool_calls
    for turn in diagnosis["incomplete_turns"]:
        after_line = turn["after_line"]
        # Find the entry with this line_num, then walk backwards
        for idx in range(len(entries) - 1, -1, -1):
            if entries[idx].get("line_num") == after_line:
                # Walk backwards from here to find the assistant with tool_calls
                for back_idx in range(idx, -1, -1):
                    e = entries[back_idx]
                    if e.get("role") == "assistant" and "tool_calls" in e:
                        issue_indices.append(back_idx)
                        break
                break

    if not issue_indices:
        # Diagnosis says broken but referenced IDs weren't found in entries;
        # safest fallback is to return the full transcript unchanged.
        return [_strip_line_num(e) for e in entries]

    earliest_issue_idx = min(issue_indices)

    # 2. Walk backwards from earliest_issue_idx to find the last real user message.
    rewind_to_idx: int | None = None
    for idx in range(earliest_issue_idx - 1, -1, -1):
        if is_real_user_message(entries[idx]):
            rewind_to_idx = idx
            break

    # 3. If rewind_to_idx is None or 0, return [] (first turn is broken).
    if rewind_to_idx is None or rewind_to_idx == 0:
        return []

    # 4. Return entries[:rewind_to_idx] stripped of line_num.
    return [_strip_line_num(e) for e in entries[:rewind_to_idx]]


# ---------------------------------------------------------------------------
# CLI helpers
# ---------------------------------------------------------------------------


def _backup(filepath: Path, label: str) -> Path | None:
    """Create a timestamped backup of *filepath*.

    Returns the backup path, or ``None`` if *filepath* does not exist.
    The backup is named ``<filepath>.bak-<label>-<YYYYMMDDHHMMSS>``.
    """
    if not filepath.exists():
        return None
    ts = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
    dest = filepath.with_name(f"{filepath.name}.bak-{label}-{ts}")
    shutil.copy2(filepath, dest)
    return dest


def _write_entries(filepath: Path, entries: list[dict]) -> None:
    """Write a list of dicts as JSONL to *filepath*."""
    with open(filepath, "w", encoding="utf-8") as f:
        for entry in entries:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    """CLI entry point for session repair.

    Returns exit code: 0 success, 1 broken/failed, 2 usage error.
    """
    parser = argparse.ArgumentParser(
        description="Diagnose and repair Amplifier session transcripts.",
    )
    parser.add_argument("session_dir", help="Path to the session directory")

    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument("--diagnose", action="store_true", help="Print JSON diagnosis")
    action.add_argument(
        "--repair", action="store_true", help="Repair a broken transcript"
    )
    action.add_argument(
        "--rewind",
        action="store_true",
        help="Rewind transcript to before the issue",
    )

    args = parser.parse_args(argv)
    session_dir = Path(args.session_dir)
    transcript_path = session_dir / "transcript.jsonl"

    if not transcript_path.exists():
        print(f"Error: {transcript_path} not found", file=sys.stderr)
        return 2

    # --diagnose ---------------------------------------------------------
    if args.diagnose:
        diag = diagnose(session_dir)
        print(json.dumps(diag, indent=2))
        return 0 if diag["status"] == "healthy" else 1

    # --repair -----------------------------------------------------------
    if args.repair:
        diag = diagnose(session_dir)
        if diag["status"] == "healthy":
            print("no-repair-needed")
            return 0

        _backup(transcript_path, "pre-repair")
        entries = parse_transcript(transcript_path)
        repaired = repair_transcript(entries, diag)
        _write_entries(transcript_path, repaired)

        # Verify by re-diagnosing
        verification = diagnose(session_dir)
        report = {
            "status": "repaired" if verification["status"] == "healthy" else "failed",
            "original_diagnosis": diag,
            "verification": verification,
            "counts": {
                "original_entries": len(entries),
                "repaired_entries": len(repaired),
            },
        }
        print(json.dumps(report, indent=2))
        return 0 if verification["status"] == "healthy" else 1

    # --rewind -----------------------------------------------------------
    if args.rewind:
        diag = diagnose(session_dir)
        if diag["status"] == "healthy":
            print("no-rewind-needed")
            return 0

        _backup(transcript_path, "pre-rewind")
        events_path = session_dir / "events.jsonl"
        if events_path.exists():
            _backup(events_path, "pre-rewind")

        entries = parse_transcript(transcript_path)
        rewound = rewind_transcript(entries, diag)
        _write_entries(transcript_path, rewound)

        # Truncate events.jsonl proportionally if it exists
        if events_path.exists():
            original_count = len(entries)
            rewound_count = len(rewound)
            with open(events_path, encoding="utf-8") as f:
                event_lines = f.readlines()
            if original_count > 0 and event_lines:
                ratio = rewound_count / original_count
                keep = max(0, int(len(event_lines) * ratio))
                with open(events_path, "w", encoding="utf-8") as f:
                    f.writelines(event_lines[:keep])

        report = {
            "status": "rewound",
            "original_entries": len(entries),
            "rewound_entries": len(rewound),
        }
        print(json.dumps(report, indent=2))
        return 0

    return 2  # pragma: no cover


if __name__ == "__main__":
    sys.exit(main())
