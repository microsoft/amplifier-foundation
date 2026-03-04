# Session Storage Deep Knowledge

## CRITICAL: Handling Large Session Files

Session event logs (`events.jsonl`) can be **extremely large** (67MB+) with individual lines containing **100k+ tokens**. This is because each event can include the entire conversation history up to that point.

**Standard tools WILL FAIL. You must use surgical extraction patterns.**

## File Size Danger Zones

| File | Typical Size | Line Size | Safe to Read? |
|------|-------------|-----------|---------------|
| `metadata.json` | <1KB | Small | Yes |
| `transcript.jsonl` | 50-500KB | Medium | Careful with long sessions |
| `events.jsonl` | 10-100MB+ | **HUGE** | Never read full lines |

### Why Lines Are Huge

Each `llm:request` and `llm:response` event contains:
- Full conversation history (all previous messages)
- All loaded context files
- System instructions
- Tool definitions

A single line can easily be 100k-200k tokens.

## Safe Extraction Patterns

### NEVER DO THIS on events.jsonl

```bash
# WILL FAIL - outputs entire lines (100k+ tokens each)
grep "pattern" events.jsonl
cat events.jsonl
read_file events.jsonl

# WILL FAIL - even with head, grep outputs full matching lines first
grep "error" events.jsonl | head -5
```

### ALWAYS DO THIS Instead

**Step 1: Get metadata first (always safe)**
```bash
wc -l events.jsonl                    # How many events?
ls -lh events.jsonl                   # File size?
head -c 500 events.jsonl              # First 500 chars only
```

**Step 2: Extract line numbers only (no content)**
```bash
# Get line numbers where pattern matches, NOT the lines themselves
grep -n "pattern" file.jsonl | cut -d: -f1 | head -10
```

**Step 3: Surgical field extraction with jq**
```bash
# Extract ONLY small fields, never full data
jq -c '{event, ts, lvl}' events.jsonl | head -20

# For LLM events, extract metadata only
jq -c 'select(.event | startswith("llm:")) | {event, ts, model: .data.model, usage: .data.usage}' events.jsonl

# Extract from specific line (get line first, then parse)
sed -n '123p' events.jsonl | jq '{event, ts}'
```

**Step 4: Character-limited extraction**
```bash
# Get first N characters of a specific line
sed -n '123p' events.jsonl | cut -c1-500

# Get specific field with length limit
sed -n '123p' events.jsonl | jq -r '.event'
```

## events.jsonl Structure

Each line is a JSON object:

```json
{
  "ts": "2025-12-30T14:20:27.123+00:00",
  "lvl": "INFO",
  "schema": {"name": "amplifier.log", "ver": "1.0.0"},
  "event": "llm:request",
  "session_id": "uuid-here",
  "data": { /* Event-specific payload - CAN BE HUGE */ }
}
```

### Event Types and Data Sizes

| Event Type | Data Size | Safe Fields |
|------------|-----------|-------------|
| `execution:start` | Small | All |
| `execution:end` | Small | All |
| `session:start` | Small | All (kernel session lifecycle) |
| `session:fork` | Small | All (kernel session forked) |
| `session:resume` | Small | All (kernel session resumed) |
| `prompt:submit` | Medium | `event`, `ts` |
| `llm:request` | **HUGE** | `event`, `ts`, `data.model`, `data.message_count` |
| `llm:response` | **HUGE** | `event`, `ts`, `data.model`, `data.usage`, `data.duration_ms` |
| `llm:request:debug` | Large | `event`, `ts` (content truncated to 180 chars) |
| `llm:request:raw` | **HUGE** | `event`, `ts` only |
| `tool:pre` | Variable | `event`, `ts`, `data.tool_name` |
| `tool:post` | Variable | `event`, `ts`, `data.tool_name`, `data.duration_ms` |
| `task:agent_spawned` | Medium | Most fields safe |
| `task:completed` | Medium | Most fields safe |

### Correlating Parent-Child Sessions

```bash
# Check if session is root or sub-session
head -1 events.jsonl | jq -r '.parent_id // "root"'

# Find delegation events (when this session spawned children)
jq -c 'select(.event == "task:agent_spawned") | {ts, agent: .data.agent}' events.jsonl
```

**Attribution rule**: `parent_id` present = sub-session = "user" is the calling agent.
To find the human user, trace parent_id chain until you reach a root session (no parent_id).

### Three-Level Logging System

Providers emit events at configurable verbosity:

1. **INFO level** (default): Summary only - model, message_count, usage
2. **DEBUG level** (`debug: true`): Truncated payloads (180 chars default)
3. **RAW level** (`debug: true` + `raw_debug: true`): Complete untruncated data

## Extracting Specific Information

### Find errors in a session
```bash
# Get line numbers of errors (safe)
grep -n '"lvl":"ERROR"\|"status":"error"\|"error":' events.jsonl | cut -d: -f1 | head -10

# Extract error summary from specific line
LINE_NUM=123
sed -n "${LINE_NUM}p" events.jsonl | jq -c '{event, ts, error: .data.error[:200]}'
```

### Find missing tool results
```bash
# Count tool:pre vs tool:post events
grep -c '"event":"tool:pre"' events.jsonl
grep -c '"event":"tool:post"' events.jsonl

# If counts don't match, find orphaned tool calls by comparing line numbers
grep -n '"event":"tool:pre"' events.jsonl | cut -d: -f1
grep -n '"event":"tool:post"' events.jsonl | cut -d: -f1
```

### Get token usage summary
```bash
# Extract usage from all llm:response events
jq -c 'select(.event == "llm:response") | .data.usage' events.jsonl
```

### Find specific tool calls
```bash
# Find bash tool executions (line numbers only)
grep -n '"tool_name":"bash"' events.jsonl | cut -d: -f1 | head -5

# Get command from specific tool:pre line (careful - command could be long)
LINE_NUM=123
sed -n "${LINE_NUM}p" events.jsonl | jq -r '.data.input.command[:500]'
```

## Provider-Specific Field Names

| Provider | Messages Field | System Field |
|----------|---------------|--------------|
| Anthropic | `data.params.messages` | `data.params.system` |
| OpenAI | `data.params.input` | `data.params.instructions` |
| Azure OpenAI | Same as OpenAI | Same as OpenAI |

## Session Recovery: Corrupted Transcripts

Sessions may have incomplete `tool_use`/`tool_result` pairs if:
- User interrupted during tool execution
- Tool execution timed out
- Network failure during streaming

### Detection

In `transcript.jsonl`, count assistant messages with tool_calls vs tool role messages:
```bash
grep -c '"tool_calls"' transcript.jsonl
grep -c '"role":"tool"' transcript.jsonl
```

If the first count is higher than the second, there are orphaned tool_use blocks.

### Recovery

Insert a synthetic `tool_result` message after the orphaned `tool_use` in transcript.jsonl:

```json
{"role":"tool","tool_call_id":"THE_ORPHANED_TOOL_ID","content":"{\"error\": \"Tool execution was interrupted by user\", \"interrupted\": true}","timestamp":"ISO_TIMESTAMP"}
```

**Steps:**
1. Find the line with orphaned tool_use: `grep -n "THE_TOOL_ID" transcript.jsonl | cut -d: -f1`
2. Back up the file: `cp transcript.jsonl transcript.jsonl.backup`
3. Insert synthetic result after that line using `head`/`tail` or `sed`
4. Verify the session loads correctly

## Key Principle

**Always extract metadata first, content never (or surgically).**

The pattern is:
1. Get line numbers → `grep -n ... | cut -d: -f1`
2. Count events → `grep -c "pattern"`
3. Extract small fields → `jq -c '{event, ts, small_field}'`
4. If you MUST see content → `cut -c1-500` or `jq '.field[:200]'`

---

## REPLACE vs COMPLETE: Choosing the Right Repair Strategy

When a session has incomplete turns, you have two repair strategies:

| Strategy | What It Does | When to Use |
|----------|--------------|-------------|
| **COMPLETE** | Keep original turn, add synthetic tool_results | Turn has valuable context worth preserving (DEFAULT) |
| **REPLACE** | Remove incomplete turn, insert simple error message | Turn is garbage, not worth preserving |

**Default to COMPLETE** — it preserves context and creates a cleaner state.

### Decision Tree

```
Session has orphaned tool_use blocks?
│
├─ Is the original turn worth preserving?
│  │
│  ├─ YES (has thinking, instructions, partial results)
│  │  └─ Use COMPLETE approach:
│  │     1. Keep the original assistant turn intact
│  │     2. Add synthetic tool_result for EACH orphaned tool_use_id
│  │     3. Check if synthetic assistant response also needed
│  │     4. Result: Well-formed transcript, full context preserved
│  │
│  └─ NO (turn is empty or corrupted beyond use)
│     └─ Use REPLACE approach:
│        1. Remove the incomplete turn entirely
│        2. Insert simple error message
```

### Fast Full-History Orphan Scan

**Always scan the ENTIRE transcript**, not just the last turn:

```bash
# One-liner to detect ALL orphaned tool_calls anywhere in history
comm -23 \
  <(jq -r '.tool_calls[]?.id' transcript.jsonl 2>/dev/null | sort -u) \
  <(jq -r 'select(.role == "tool") | .tool_call_id' transcript.jsonl 2>/dev/null | sort -u)
```

If output is empty → no orphaned tool_calls. If IDs appear → those need synthetic tool_results.

### COMPLETE Workflow

> **Full procedure moved to @foundation:context/agents/session-repair-knowledge.md**
>
> That file covers:
> - 5-step diagnostic framework (transcript parsing → failure-mode detection → severity assessment)
> - Repair procedures (synthetic tool_result insertion, assistant response completion)
> - JSON templates for synthetic error messages
> - Verification checklist (orphan re-scan, turn-structure validation)
> - `scripts/session-repair.py` usage and CLI interface

### Synthetic Error Types

Use appropriate error messages based on what likely failed. **If the actual error cannot be determined, ALWAYS use the unknown error — it is CRITICAL that a result exists for proper operation:**

| Error Type | Message |
|------------|---------|
| **Unknown (fallback)** | `{"error": "unknown_error", "message": "Tool execution failed with unknown error. Please retry."}` |
| Server overload | `{"error": "overloaded_error", "message": "Server overloaded during execution. Please retry."}` |
| Timeout | `{"error": "timeout", "message": "Tool execution exceeded time limit."}` |
| Connection | `{"error": "connection_error", "message": "Failed to connect to service."}` |
| Rate limit | `{"error": "rate_limit", "message": "Rate limit exceeded. Please wait and retry."}` |
