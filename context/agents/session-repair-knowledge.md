# Session Repair Deep Knowledge

Reference documentation for the session repair script and the failure modes it handles. This file describes WHAT breaks and HOW THE SCRIPT fixes it — not manual procedures.

**Repair-first default:** ALWAYS attempt REPAIR before REWIND. Repair preserves maximum context. Rewind only when the user explicitly requests it.

**Script-only rule:** ALL diagnosis, repair, and rewind operations MUST use `scripts/session-repair.py`. Never attempt manual JSONL editing.

---

## Three Failure Modes

These are the structural problems that cause sessions to fail on resume (typically with provider errors like `"tool_use ids found without tool_result blocks"`). A transcript can have **multiple failure modes simultaneously**.

### FM1: Missing Tool Results

Assistant issued `tool_calls` but matching `tool_result` entries are absent from the transcript. The provider will reject the session on resume because every `tool_use` ID must have a paired result.

**Script action:** Injects synthetic `tool_result` entries with error content immediately after the assistant message containing the orphaned `tool_calls`.

### FM2: Ordering Violations

A `tool_result` exists but a real user message or different assistant turn appears between the `tool_use` and its result. The results arrived "late" after the conversation moved on, putting them in an invalid position.

**Key distinction:** A "real user message" is `role: "user"` with no `tool_call_id` field and content not wrapped in `<system-reminder>` tags. System-injected messages and tool results are NOT real user messages.

**Script action:** Removes misplaced results and injects synthetic results in the correct position (immediately after the assistant message).

### FM3: Incomplete Assistant Turns

Tool results are present and correctly ordered, but there is no final assistant text response before the next real user message. The assistant's turn started (tool calls dispatched, results received) but never completed.

**Script action:** Injects a synthetic assistant response to close the incomplete turn.

---

## Repair-First Default

| Strategy | Action | When |
|----------|--------|------|
| **REPAIR** (default) | Inject synthetic entries to complete broken turns | Always, unless user explicitly requests rewind |
| **REWIND** (explicit only) | Truncate to before last real user message prior to issues | Only when user says "rewind" or "truncate" |

**Rationale for repair-first:**
- Preserves maximum conversation context (the assistant's thinking, tool call intentions, partial results)
- User picks up where things went wrong and steers forward
- Rewind loses all context after the truncation point — it's the nuclear option
- Repair is safe: the script always creates timestamped backups before modification

---

## Programmatic Repair Script

**Location:** `scripts/session-repair.py` (in the amplifier-foundation repo, stdlib-only Python, no dependencies)

### Usage

```bash
# Diagnose only (read-only, safe)
python scripts/session-repair.py --diagnose /path/to/session/

# Repair with COMPLETE strategy (creates timestamped backup first)
python scripts/session-repair.py --repair /path/to/session/

# Rewind (truncate — creates timestamped backups of both transcript and events)
python scripts/session-repair.py --rewind /path/to/session/
```

### Exit Codes

| Code | Meaning |
|------|---------|
| 0 | Success (healthy on `--diagnose`, repaired on `--repair`, rewound on `--rewind`) |
| 1 | Repair needed (`--diagnose`) or repair failed (`--repair`) |
| 2 | Invalid arguments or missing transcript file |

### `--diagnose` JSON Output Format

```json
{
  "status": "broken",
  "failure_modes": ["missing_tool_results", "ordering_violation", "incomplete_assistant_turn"],
  "orphaned_tool_ids": ["toolu_abc123"],
  "misplaced_tool_ids": ["toolu_def456"],
  "incomplete_turns": [{"after_line": 42, "missing": "assistant_response"}],
  "recommended_action": "repair"
}
```

Fields:
- `status`: `"healthy"` or `"broken"`
- `failure_modes`: list of `"missing_tool_results"`, `"ordering_violation"`, `"incomplete_assistant_turn"`
- `orphaned_tool_ids`: tool_use IDs with no matching tool_result
- `misplaced_tool_ids`: tool_use IDs whose results are in the wrong position
- `incomplete_turns`: list of `{"after_line": N, "missing": "assistant_response"}` entries
- `recommended_action`: `"none"` (healthy) or `"repair"` (broken)

### Verification

After any repair or rewind, run `--diagnose` again to confirm success:

```bash
python scripts/session-repair.py --diagnose /path/to/session/
# Exit code 0 + {"status": "healthy"} = all checks pass
# Exit code 1 + {"status": "broken"} = issues remain
```
