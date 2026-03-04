# Session Repair Deep Knowledge

This context file contains everything needed to diagnose and repair broken Amplifier session transcripts. It covers three failure modes, repair procedures with exact JSON templates, and a verification checklist.

**Repair-first default:** ALWAYS attempt REPAIR before REWIND. Repair preserves maximum context. Rewind only when the user explicitly requests it.

---

## Diagnostic Framework

When a session fails to resume (typically with provider errors like `"tool_use ids found without tool_result blocks"`), follow this 5-step diagnostic procedure.

### Step 1: Build the tool index

Find every `tool_use` ID and every `tool_result` ID in the transcript, with their line numbers.

```bash
# All tool_use IDs
jq -r '.tool_calls[]?.id // empty' transcript.jsonl

# All tool_result IDs
jq -r 'select(.role == "tool") | .tool_call_id' transcript.jsonl
```

Or use the programmatic script:
```bash
python scripts/session-repair.py /path/to/session --diagnose
```

### Step 2: Check for orphans (Failure Mode 1)

A `tool_use` ID with **no matching `tool_result` anywhere** in the transcript.

```bash
comm -23 \
  <(jq -r '.tool_calls[]?.id' transcript.jsonl 2>/dev/null | sort -u) \
  <(jq -r 'select(.role == "tool") | .tool_call_id' transcript.jsonl 2>/dev/null | sort -u)
```

If this outputs IDs, those are orphaned tool_calls needing synthetic results.

### Step 3: Check for ordering violations (Failure Mode 2)

A `tool_result` **exists** but a real user message or a different assistant turn appears between the `tool_use` and its result. This means the results arrived "late" after the conversation moved on.

**Key distinction:** A "real user message" is `role: "user"` AND no `tool_call_id` field AND content not wrapped in `<system-reminder>` tags. System-injected messages and tool results are NOT real user messages.

Detection requires checking the **ordering** of entries, not just existence:
1. For each `tool_use` ID that HAS a matching `tool_result`, check the entries between them
2. If any real user message or non-tool-calling assistant message appears between them, it is an ordering violation

### Step 4: Check for incomplete assistant turns (Failure Mode 3)

Tool results are present and in the correct position, but there is **no final assistant text response** before the next real user message. This means the assistant's turn started (tool calls dispatched, results received) but never completed.

Detection:
1. For each assistant message with `tool_calls`, find the last matching `tool_result`
2. Check the entry immediately after that last result
3. If it's a real user message (or end of transcript), the turn is incomplete

### Step 5: Classify

A transcript can have **multiple failure modes simultaneously**. The real `4f63147f` case had all three: some tool results were in the wrong position, some were missing entirely, and an assistant response was missing.

---

## Repair Procedures

### Repairing Missing Tool Results (Failure Mode 1)

**Action:** Inject a synthetic `tool_result` entry immediately after the assistant message containing the `tool_use`.

**Synthetic tool_result format:**
```json
{
  "role": "tool",
  "tool_call_id": "<matching_tool_use_id>",
  "name": "<tool_name>",
  "content": "{\"error\": \"unknown_error\", \"message\": \"Tool execution was interrupted and no result was captured.\"}"
}
```

**Important details:**
- `tool_call_id` MUST exactly match the `id` from the `tool_call`
- `name` MUST match the tool name from the `tool_call`'s `function.name`
- `content` MUST be a JSON-encoded STRING (double-escaped quotes), not a nested object
- Insert immediately after the assistant message, before any following entries

### Repairing Misplaced Tool Results (Failure Mode 2)

**Action:** Remove the late-arriving results from their wrong position. Inject synthetic results in the correct position (immediately after the assistant message with `tool_use`s).

Steps:
1. Identify the misplaced `tool_result` entries (those with a real user message between their `tool_use` and themselves)
2. Remove them from their current positions
3. Inject synthetic `tool_result` entries immediately after the assistant message, using the same format as Failure Mode 1
4. If all tool_calls from that assistant message were misplaced and the next entry is a real user message, also inject a synthetic assistant response

The real results' content is lost (they were in an invalid position), but the session becomes structurally valid.

### Repairing Incomplete Assistant Turns (Failure Mode 3)

**Action:** Inject a synthetic assistant message to close the turn. Insert after the last `tool_result` of the incomplete turn, before the next real user message.

**Synthetic assistant response format:**
```json
{
  "role": "assistant",
  "content": [
    {
      "type": "text",
      "text": "The previous tool calls were interrupted. This response was automatically repaired."
    }
  ]
}
```

Note the `content` field is a **list** of content blocks (not a plain string). This matches the provider-expected format for assistant messages with structured content.

---

## Repair-First Default

| Strategy | Action | When |
|----------|--------|------|
| **REPAIR** (default) | Inject synthetic entries to complete broken turns | Always, unless user explicitly requests rewind |
| **REWIND** (explicit only) | Truncate to before last real user message prior to issues | Only when user says "rewind" or "truncate" |

**Rationale for repair-first:**
- Preserves maximum conversation context (the assistant's thinking, tool call intentions, partial results)
- User picks up where things went wrong and steers forward
- Rewind loses all context after the truncation point -- it's the nuclear option
- Repair is safe: backups are always created before modification

---

## Verification Checklist

After any repair, ALL five checks must pass:

1. [ ] All JSONL lines parse as valid JSON
2. [ ] Every `tool_use` ID has a matching `tool_result` (no orphans)
3. [ ] No `tool_result`s appear with a real user message between them and their `tool_use` (no ordering violations)
4. [ ] No real user messages interrupt an assistant turn (between `tool_use`s and their results)
5. [ ] Every assistant turn that dispatched tools has a final assistant text response before the next real user message (complete turns)

**Quick verification command:**
```bash
python scripts/session-repair.py /path/to/session --diagnose
# Exit code 0 + {"status": "healthy", ...} = all checks pass
# Exit code 1 + {"status": "broken", ...} = issues remain
```

**Manual verification:**
```bash
# Check 1: Valid JSONL
python3 -c "
import json, sys
for i, line in enumerate(open('transcript.jsonl'), 1):
    try: json.loads(line)
    except json.JSONDecodeError: print(f'Invalid JSON at line {i}'); sys.exit(1)
print('All lines valid')
"

# Check 2: No orphaned tool_calls
comm -23 \
  <(jq -r '.tool_calls[]?.id' transcript.jsonl 2>/dev/null | sort -u) \
  <(jq -r 'select(.role == "tool") | .tool_call_id' transcript.jsonl 2>/dev/null | sort -u)
# Should output nothing

# Check 3-5: Use the script for comprehensive structural verification
python scripts/session-repair.py /path/to/session --diagnose | jq .
```

---

## Programmatic Repair Script

**Location:** `scripts/session-repair.py` (stdlib-only Python, no dependencies)

### Usage

```bash
# Diagnose only (read-only, safe)
python scripts/session-repair.py /path/to/session --diagnose

# Repair with COMPLETE strategy (creates backup first)
python scripts/session-repair.py /path/to/session --repair

# Rewind (truncate -- creates backups of both transcript and events)
python scripts/session-repair.py /path/to/session --rewind
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

### When to Fall Back to Manual Repair

Use manual repair (following the procedures in this document) when:
- The script encounters an edge case it doesn't handle
- The script fails with an unexpected error
- The transcript has an unusual structure the script wasn't designed for
- You need to make selective repairs (e.g., repair some issues but not others)

The script always creates timestamped backups (`transcript.jsonl.bak-pre-repair-YYYYMMDDHHMMSS`) before modification, so it's safe to try the script first and fall back to manual if needed.
