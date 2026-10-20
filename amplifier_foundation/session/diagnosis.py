"""Layer Level 1, pure, zero I/O.

Extract repair algorithms from session transcripts.

This module provides pure functions for diagnosing, repairing, and rewinding
conversation transcripts. All functions are side-effect free and perform no
file I/O. Entries must already be parsed and annotated with ``line_num`` keys
(1-based) before being passed to these functions.
"""

from __future__ import annotations

import json
from typing import NotRequired, TypedDict

from .messages import is_real_user_message


# ---------------------------------------------------------------------------
# Types
# ---------------------------------------------------------------------------


class IncompleteTurn(TypedDict):
    after_line: int
    after_index: int
    missing: str


class DiagnosisResult(TypedDict):
    status: str  # "healthy" | "broken"
    failure_modes: list[str]
    orphaned_tool_ids: list[str]
    misplaced_tool_ids: list[str]
    incomplete_turns: list[IncompleteTurn]
    unresolved_tool_ids: NotRequired[list[str]]
    recommended_action: (
        str  # "none" | "repair" | "rewind" (rewind reserved for future use)
    )


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

SYNTHETIC_TOOL_RESULT_CONTENT: str = json.dumps(
    {
        "error": "unknown_error",
        "message": (
            "No result was recorded; actual execution outcome is unknown. "
            "Verify independent evidence before repeating an action."
        ),
    }
)

SYNTHETIC_ASSISTANT_RESPONSE: dict = {
    "role": "assistant",
    "content": [
        {
            "type": "text",
            "text": (
                "Derived repair notice: this transcript was automatically repaired. "
                "This notice does not establish any tool's execution outcome."
            ),
        }
    ],
}


# ---------------------------------------------------------------------------
# Index builder
# ---------------------------------------------------------------------------


def build_tool_index(entries: list[dict]) -> dict:
    """Index top-level compatibility calls and canonical tool_call blocks.

    The legacy ``tool_uses`` / ``tool_results`` keys retain their entry_index,
    line_num and tool_name fields. Repeated IDs retain the FIRST occurrence,
    not a last-wins association. Internal batches and unresolved IDs let the
    diagnosis/repair engines reject unsafe associations without changing messages.
    An empty unresolved ID denotes a call/result with no usable ID.
    """
    tool_uses: dict[str, dict] = {}
    tool_results: dict[str, dict] = {}
    call_batches: dict[int, list[str]] = {}
    unresolved: set[str] = set()

    for idx, entry in enumerate(entries):
        content = entry.get("content")
        blocks = content if isinstance(content, list) else []
        if entry.get("role") == "assistant":
            top_calls = entry.get("tool_calls") or []
            block_calls = [
                b
                for b in blocks
                if isinstance(b, dict) and b.get("type") == "tool_call"
            ]
            signatures: dict[str, tuple] = {}
            for calls in (top_calls, block_calls):
                seen: set[str] = set()
                for call in calls:
                    call_id = call.get("id") or call.get("tool_call_id") or ""
                    function = call.get("function") or {}
                    name = (
                        call.get("tool") or call.get("name") or function.get("name", "")
                    )
                    arguments = call.get(
                        "input", call.get("arguments", function.get("arguments", {}))
                    )
                    if isinstance(arguments, str):
                        try:
                            arguments = json.loads(arguments)
                        except ValueError:
                            pass
                    try:
                        # Python equality conflates True/1 and 1/1.0. Preserve
                        # JSON types when comparing mirrored call declarations.
                        normalized = json.dumps(
                            arguments,
                            sort_keys=True,
                            separators=(",", ":"),
                            allow_nan=False,
                        )
                    except (TypeError, ValueError):
                        unresolved.add(call_id)
                        normalized = None
                    signature = (name, normalized)
                    if not call_id or call_id in seen:
                        unresolved.add(call_id)
                    seen.add(call_id)
                    if call_id in signatures:
                        # One equivalent mirror in each representation is one call.
                        if signatures[call_id] != signature:
                            unresolved.add(call_id)
                        continue
                    signatures[call_id] = signature
                    if call_id in tool_uses:
                        unresolved.add(call_id)
                    tool_uses.setdefault(
                        call_id,
                        {
                            "line_num": entry.get("line_num"),
                            "tool_name": name,
                            "entry_index": idx,
                        },
                    )
            if signatures:
                call_batches[idx] = list(signatures)

        elif entry.get("role") == "tool":
            result_blocks = [
                b
                for b in blocks
                if isinstance(b, dict) and b.get("type") == "tool_result"
            ]
            top_id = entry.get("tool_call_id")
            block_ids = [b.get("tool_call_id") or "" for b in result_blocks]
            result_ids = list(dict.fromkeys(([top_id] if top_id else []) + block_ids))
            if not result_ids:
                result_ids = [""]
            if len(result_blocks) > 1 or len(result_ids) > 1 or "" in result_ids:
                # Never split a multi-result message or choose between conflicting IDs.
                unresolved.update(result_ids)
            for result_id in result_ids:
                if result_id in tool_results:
                    unresolved.add(result_id)
                tool_results.setdefault(
                    result_id,
                    {
                        "line_num": entry.get("line_num"),
                        "entry_index": idx,
                    },
                )

    for result_id, result_info in tool_results.items():
        use_info = tool_uses.get(result_id)
        if use_info is None or result_info["entry_index"] <= use_info["entry_index"]:
            unresolved.add(result_id)
        elif any(
            use_info["entry_index"] < other_idx < result_info["entry_index"]
            for other_idx in call_batches
        ):
            # Crossing an unrelated call batch is not a safe automatic association.
            unresolved.add(result_id)

    # A parallel batch is indivisible for repair, including its unsafe siblings.
    for ids in call_batches.values():
        if unresolved.intersection(ids):
            unresolved.update(ids)

    return {
        "tool_uses": tool_uses,
        "tool_results": tool_results,
        "_call_batches": call_batches,
        "unresolved_tool_ids": sorted(unresolved),
    }


# ---------------------------------------------------------------------------
# Diagnostic engine
# ---------------------------------------------------------------------------


def _has_intervening_disruption(
    entries: list[dict], use_idx: int, result_idx: int
) -> bool:
    """Return True if a real user message or unrelated assistant turn appears
    between entry indices *use_idx* and *result_idx*.
    """
    for between_idx in range(use_idx + 1, result_idx):
        between = entries[between_idx]
        if is_real_user_message(between):
            return True
        if between.get("role") == "assistant":
            return True
    return False


def diagnose_transcript(entries: list[dict]) -> DiagnosisResult:
    """Analyse a list of transcript entries and return a structured diagnosis.

    Returns a DiagnosisResult with keys:
        status: "healthy" | "broken"
        failure_modes: list of strings
        orphaned_tool_ids: list of tool_use IDs with no matching tool_result
        misplaced_tool_ids: list of tool_use IDs whose results are out of order
        incomplete_turns: list of IncompleteTurn dicts with 'after_line' and 'missing'
        unresolved_tool_ids: optional IDs whose evidence cannot be safely associated
        recommended_action: "none" | "repair" ("rewind" reserved for future use)
    """
    index = build_tool_index(entries)

    failure_modes: list[str] = []
    orphaned_tool_ids: list[str] = []
    misplaced_tool_ids: list[str] = []
    incomplete_turns: list[IncompleteTurn] = []
    unresolved = set(index["unresolved_tool_ids"])

    # --- Failure mode 1: missing tool results ---
    for tc_id in index["tool_uses"]:
        if tc_id in unresolved:
            continue
        if tc_id not in index["tool_results"]:
            orphaned_tool_ids.append(tc_id)
    if orphaned_tool_ids:
        failure_modes.append("missing_tool_results")

    # --- Failure mode 2: ordering violations ---
    # A tool_result is misplaced if a real user message or a different assistant
    # turn appears between the tool_use and its result.
    for tc_id, use_info in index["tool_uses"].items():
        if tc_id in unresolved:
            continue
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
    # Walk through indexed assistant call batches, including canonical blocks.
    # For each, verify that after all its tool_results there is a final
    # assistant text response before the next real user message.
    orphaned_set = set(orphaned_tool_ids)
    misplaced_set = set(misplaced_tool_ids)
    for assistant_idx, all_tc_ids in index["_call_batches"].items():
        if unresolved.intersection(all_tc_ids):
            continue

        # Skip if ALL tool_calls from this message are orphaned/misplaced
        # (batch repair handles those). For partial orphans, continue
        # to FM3 detection so the incomplete turn is caught in one pass.
        if all(tid in orphaned_set or tid in misplaced_set for tid in all_tc_ids):
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
                    "after_line": entries[last_result_idx].get("line_num"),
                    "after_index": last_result_idx,
                    "missing": "assistant_response",
                }
            )
            continue

        next_entry = entries[next_idx]
        if is_real_user_message(next_entry):
            # Real user message immediately after tool results = incomplete turn
            incomplete_turns.append(
                {
                    "after_line": entries[last_result_idx].get("line_num"),
                    "after_index": last_result_idx,
                    "missing": "assistant_response",
                }
            )

    if incomplete_turns:
        failure_modes.append("incomplete_assistant_turn")
    if unresolved:
        failure_modes.append("unresolved_tool_association")

    status = "broken" if failure_modes else "healthy"
    recommended = "repair" if failure_modes else "none"

    diagnosis: DiagnosisResult = {
        "status": status,
        "failure_modes": failure_modes,
        "orphaned_tool_ids": orphaned_tool_ids,
        "misplaced_tool_ids": misplaced_tool_ids,
        "incomplete_turns": incomplete_turns,
        "recommended_action": recommended,
    }
    if unresolved:
        diagnosis["unresolved_tool_ids"] = sorted(unresolved)
    return diagnosis


# ---------------------------------------------------------------------------
# Repair engine helpers
# ---------------------------------------------------------------------------


def _make_synthetic_tool_result(tool_call_id: str, tool_name: str) -> dict:
    """Create an explicitly unknown placeholder for an actually missing result."""
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


# ---------------------------------------------------------------------------
# Repair engine
# ---------------------------------------------------------------------------


def repair_transcript(entries: list[dict], diagnosis: DiagnosisResult) -> list[dict]:
    """Repair safe tool batches without discarding or rewriting original evidence.

    Return a new list without internal ``line_num`` keys. Affected batches gather
    whole original result messages next to their declaring call, then fill only
    genuinely missing results. Non-result messages keep their relative order;
    relocation is NOT chronology-preserving. Unsafe associations remain broken.
    """
    # 1. Healthy transcripts are returned unchanged (minus line_num).
    if diagnosis["status"] == "healthy":
        return [_strip_line_num(e) for e in entries]

    orphaned_set = set(diagnosis["orphaned_tool_ids"])
    misplaced_set = set(diagnosis["misplaced_tool_ids"])
    broken_set = orphaned_set | misplaced_set
    index = build_tool_index(entries)
    unresolved = set(index["unresolved_tool_ids"]) | set(
        diagnosis.get("unresolved_tool_ids", [])
    )
    # Use entry indices (stable) for matching, with line_num fallback for
    # diagnosis results produced before after_index was added.
    incomplete_after_indices = {
        t["after_index"] for t in diagnosis["incomplete_turns"] if "after_index" in t
    }
    incomplete_after_lines = {
        t["after_line"] for t in diagnosis["incomplete_turns"] if "after_index" not in t
    }

    # 2. Pre-gather WHOLE batches before emitting or skipping any original.
    batches: dict[int, list[dict]] = {}
    skip_indices: set[int] = set()
    closing_result_indices: set[int] = set()
    for call_idx, ids in index["_call_batches"].items():
        if unresolved.intersection(ids):
            continue
        existing_indices = sorted(
            index["tool_results"][tid]["entry_index"]
            for tid in ids
            if tid in index["tool_results"]
        )
        if not broken_set.intersection(ids):
            if existing_indices:
                closing_result_indices.add(existing_indices[-1])
            continue
        batch = [_strip_line_num(entries[i]) for i in existing_indices]
        for tid in ids:
            if tid not in index["tool_results"]:
                batch.append(
                    _make_synthetic_tool_result(
                        tid, index["tool_uses"][tid]["tool_name"]
                    )
                )
        batches[call_idx] = batch
        # Skip only originals actually re-emitted as part of this batch.
        skip_indices.update(existing_indices)

    # 3. Emit messages and close only AFTER the whole result batch.
    result: list[dict] = []
    for idx, entry in enumerate(entries):
        if idx in skip_indices:
            continue

        result.append(_strip_line_num(entry))

        close_here = False
        if idx in batches:
            result.extend(batches[idx])
            close_here = True
        # Unchanged batches close at their actual last result. Retain the
        # line_num fallback for diagnoses produced before after_index existed.
        elif idx in closing_result_indices and (
            idx in incomplete_after_indices
            or (
                incomplete_after_lines
                and entry.get("line_num") in incomplete_after_lines
            )
        ):
            close_here = True
        if close_here:
            next_entry = next(
                (
                    entries[i]
                    for i in range(idx + 1, len(entries))
                    if i not in skip_indices
                ),
                None,
            )
            if next_entry is None or is_real_user_message(next_entry):
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

    for tc_id in diagnosis.get("unresolved_tool_ids", []):
        for key in ("tool_uses", "tool_results"):
            if tc_id in index[key]:
                issue_indices.append(index[key][tc_id]["entry_index"])

    # Incomplete turns → walk back to find the indexed assistant call batch.
    for turn in diagnosis["incomplete_turns"]:
        after_index = turn.get("after_index")
        if after_index is not None:
            # Use entry index directly (stable, no line_num dependency)
            for back_idx in range(after_index, -1, -1):
                if back_idx in index["_call_batches"]:
                    issue_indices.append(back_idx)
                    break
        else:
            # Fallback to line_num for old diagnosis results
            after_line = turn["after_line"]
            for idx in range(len(entries) - 1, -1, -1):
                if entries[idx].get("line_num") == after_line:
                    for back_idx in range(idx, -1, -1):
                        if back_idx in index["_call_batches"]:
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
