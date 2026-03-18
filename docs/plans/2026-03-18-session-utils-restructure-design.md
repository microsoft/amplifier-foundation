# Session Management Library Restructure Design

## Goal

Restructure `amplifier_foundation/session/` into a clean, modular library with a unified CLI script, so the session-analyst agent (a Haiku-class fast model) can perform session operations via single-command invocations instead of multi-step bash choreography — making the agent more reliable, enabling less-capable models, and consolidating scattered/duplicated code.

## Background

### The Problem

The session-analyst agent currently performs session operations through multi-step bash choreography: locating scripts via `find /`, manually constructing file paths, chaining multiple commands, and parsing unstructured output. This is fragile on a Haiku-class model — manual JSONL editing has proven to break sessions further rather than repair them.

### What Exists Today

Two separate session management systems exist in amplifier-foundation, disconnected from each other:

1. **`scripts/session-repair.py`** — A standalone 582-line script (not importable, not installable). The agent discovers it by searching the entire filesystem. Handles diagnose/repair/rewind.

2. **`amplifier_foundation/session/`** — A proper, installable Python library with `fork.py`, `slice.py`, `events.py`, and `capabilities.py`. Contains sophisticated helpers (`find_orphaned_tool_calls()`, `add_synthetic_tool_results()`, `slice_to_turn()`, `fork_session()`, `get_session_lineage()`, streaming events manipulation). The session-analyst agent does not know this library exists.

Both systems duplicate orphan-detection code with different error messages. There are zero CLI entry points in `pyproject.toml`. The behavior bundle (`behaviors/sessions.yaml`) already composes hooks-logging + hooks-session-naming + session-analyst, but the tooling underneath is fragmented.

### Expert Consultation

Four experts (foundation-expert, amplifier-expert, zen-architect, core-expert) independently confirmed the same layered architecture and converged on keeping everything in foundation. Key findings:

- The kernel (`amplifier-core`) knows nothing about persistence — zero references to `transcript.jsonl`, `events.jsonl`, or any storage path. The kernel provides mechanisms (hooks, `get_messages()`/`set_messages()`, session IDs); everything else is policy.
- The current storage format is a convention of amplifier-app-cli + hooks-logging, not a contract.
- The existing `session/` package is appropriately placed — it is the reference implementation for the sessions behavior, not a generic framework.

## Approach

**Approach C (phased):** Full library restructure with expanded capabilities, executed in three phases so the core restructure lands first, then expanded capabilities layer on, then agent instructions are rewritten.

### Why Not Simpler Approaches

- **Approach A (additive)** — Adding new modules alongside existing ones leaves naming inconsistencies (`slice.py`), duplicated orphan detection, and no consolidation of I/O helpers. Technical debt compounds.
- **Approach B (restructure only)** — Clean boundaries but misses the opportunity to add session discovery, search, and info capabilities that eliminate entire categories of agent bash choreography.

### Key Architectural Decisions

- **Stay in amplifier-foundation** — The session behavior is the standard policy implementation for the Amplifier ecosystem. Foundation is the right layer. No separate repo.
- **`python <script>` invocation, not an installed CLI** — If `amplifier-session` were registered in `pyproject.toml`, it would be installed for every user of amplifier-foundation, including apps that don't use the sessions behavior. Scripts stay in `scripts/` and are invoked by the agent via bash.
- **No premature abstractions** — No `SessionStore` protocol, no pluggable backends. Build for the concrete JSONL/file case. Extract an interface only if a second storage implementation appears.
- **Amplifier ecosystem awareness is fine** — Core kernel concepts (`role`, `tool_calls`, coordinator protocol, `session_id`) are appropriate in foundation. App-specific config choices (directory paths) are defaults, not hardcoded logic.

## Architecture

### Three-Layer Model

```
┌─────────────────────────────────────────────────────────────────┐
│  Level 3: Session Finder                                        │
│  "Where are sessions?"                                          │
│  ~/.amplifier/projects/PROJECT/sessions/SESSION_ID/             │
│  find, resolve partial IDs, search by date/keyword/project      │
├─────────────────────────────────────────────────────────────────┤
│  Level 2: JSONL Store                                           │
│  "How are sessions stored?"                                     │
│  transcript.jsonl, metadata.json, events.jsonl                  │
│  read/write JSONL, backup, directory-as-session-unit            │
├─────────────────────────────────────────────────────────────────┤
│  Level 1: Message Algebra                                       │
│  "What can we do with message lists?"                           │
│  Pure list[dict] → list[dict] operations, zero I/O              │
│  Turn boundaries, orphan detection, diagnosis, repair, rewind   │
└─────────────────────────────────────────────────────────────────┘
```

Each level depends only on the level below it. Level 1 has zero I/O. Level 2 knows file formats. Level 3 knows directory conventions.

### Dependency Flow

```
scripts/amplifier-session.py  (thin CLI wrapper)
    │
    ├── finder.py        (Level 3: resolve session references)
    │     └── store.py   (Level 2: read metadata for search results)
    │
    ├── store.py         (Level 2: load/save transcripts)
    │
    ├── diagnosis.py     (Level 1: diagnose/repair/rewind)
    │     └── messages.py (Level 1: orphan detection, turn ops)
    │
    └── fork.py          (Composite: uses messages + store)
          ├── messages.py
          └── store.py
```

## Components

### `messages.py` — Level 1: Pure Message Operations

**Renamed from `slice.py`.** Mostly a rename plus deduplication, not a rewrite. Existing functions are proven and well-tested (701-line test suite).

**Functions that move unchanged:**

- `get_turn_boundaries(messages) -> list[int]`
- `count_turns(messages) -> int`
- `slice_to_turn(messages, turn, handle_orphaned_tools="complete") -> list[dict]`
- `find_orphaned_tool_calls(messages) -> list[str]`
- `add_synthetic_tool_results(messages, orphaned_ids) -> list[dict]`
- `get_turn_summary(messages, turn) -> dict`
- `_remove_orphaned_tool_calls(messages, orphaned_ids) -> list[dict]`

**Deduplication:** Today both `slice.py` and the repair script independently detect orphaned tool calls with different synthetic content (`slice.py` produces `"forked": True`, repair script produces `"unknown_error"`). After restructure, `messages.py` keeps the orphan detection logic (one implementation). The synthetic content is parameterized — `diagnosis.py` passes repair-specific content, `fork.py` passes fork-specific content.

**New addition:** `is_real_user_message(entry) -> bool` moves here from the repair script. It is a pure message-level operation (checks role, tool_call_id, system-reminder tags) needed by both `diagnosis.py` and other callers.

**Migration:** `slice.py` is renamed to `messages.py`. A compatibility shim in `slice.py` re-exports from `messages.py` with a deprecation warning for a migration period.

### `diagnosis.py` — Level 1: Pure Repair Algorithms

Extracts all pure algorithms from `scripts/session-repair.py` into importable, testable library code. Zero I/O — takes parsed entries in, returns results out.

**Types:**

```python
class IncompleteTurn(TypedDict):
    after_line: int
    missing: str

class DiagnosisResult(TypedDict):
    status: str                         # "healthy" | "broken"
    failure_modes: list[str]
    orphaned_tool_ids: list[str]        # FM1: tool_use IDs with no result
    misplaced_tool_ids: list[str]       # FM2: tool_use IDs with out-of-order results
    incomplete_turns: list[IncompleteTurn]  # FM3: turns missing closing assistant response
    recommended_action: str             # "none" | "repair"
```

**Core functions:**

```python
build_tool_index(entries: list[dict]) -> dict
    # Maps tool_use IDs → {line_num, tool_name, entry_index}
    # Maps tool_result IDs → {line_num, entry_index}

is_real_user_message(entry: dict) -> bool
    # role=="user", no tool_call_id, not <system-reminder>

diagnose_transcript(entries: list[dict]) -> DiagnosisResult
    # Detects all three failure modes, returns structured result

repair_transcript(entries: list[dict], diagnosis: DiagnosisResult) -> list[dict]
    # COMPLETE strategy: inject synthetics, remove misplaced results

rewind_transcript(entries: list[dict], diagnosis: DiagnosisResult) -> list[dict]
    # Truncate to before last real user message prior to earliest issue
```

**What changes from the repair script:**

- Uses `messages.py` for orphan detection instead of reimplementing it (deduplication)
- `diagnose()` renamed to `diagnose_transcript()` to avoid collision with file-level operations
- Synthetic entry constants (`SYNTHETIC_TOOL_RESULT_CONTENT`, `SYNTHETIC_ASSISTANT_RESPONSE`) stay here — they are repair-specific, distinct from fork.py's fork-specific synthetics

**What stays the same:** The actual repair/rewind/diagnosis algorithms. They are battle-tested (1037-line test suite) and proven in production. We are moving them, not rewriting them.

### `store.py` — Level 2: Consolidated JSONL I/O

Consolidates scattered/duplicated file I/O helpers into one module — the single place that knows how to read/write session files.

**Functions:**

```python
# Reading
load_transcript(session_dir: Path) -> list[dict]
load_transcript_with_lines(session_dir: Path) -> list[dict]   # adds line_num for diagnosis
load_metadata(session_dir: Path) -> dict
read_jsonl(path: Path) -> Iterator[dict]                       # generic streaming reader

# Writing
write_transcript(session_dir: Path, entries: list[dict]) -> None
write_metadata(session_dir: Path, metadata: dict) -> None
write_jsonl(path: Path, entries: list[dict]) -> None

# Safety
backup(filepath: Path, label: str) -> Path                     # timestamped .bak file

# Constants (single source of truth)
TRANSCRIPT_FILENAME = "transcript.jsonl"
METADATA_FILENAME = "metadata.json"
EVENTS_FILENAME = "events.jsonl"
```

**Consolidation map:**

| Today (scattered) | Tomorrow (`store.py`) |
|----|-----|
| Repair script's `parse_transcript()` | `load_transcript_with_lines()` |
| Repair script's `_write_entries()` | `write_transcript()` |
| Repair script's `_backup()` | `backup()` |
| fork.py's `_load_transcript()` | `load_transcript()` |
| fork.py's `_write_transcript()` | `write_transcript()` |
| fork.py's `_load_metadata()` | `load_metadata()` |
| events.py's `_read_jsonl()` | `read_jsonl()` |

The three filename constants replace 14+ hardcoded string constructions across `fork.py`. After refactoring, `fork.py` imports from `store.py` instead of maintaining private I/O helpers.

### `finder.py` — Level 3: Session Discovery

The new module that eliminates the agent's multi-step bash choreography for finding sessions. Encodes the `~/.amplifier/projects/` path convention in one place.

**Core functions:**

```python
resolve_session(session_ref: str, sessions_root: str | None = None) -> Path
```

Accepts full path, full session ID, or partial ID. The `sessions_root` defaults to `~/.amplifier/projects` but is overridable (for other apps or test environments). If a partial ID matches multiple sessions, raises an error listing the matches. If no match, raises an error with suggestions.

```python
find_sessions(
    sessions_root: str | None = None,
    project: str | None = None,
    after: str | None = None,       # ISO date or relative ("yesterday", "last week")
    before: str | None = None,
    keyword: str | None = None,     # searches transcript content
    status: str | None = None,      # "healthy" or "broken" (runs diagnosis)
    limit: int = 50,
) -> list[dict]
```

Returns list of `{session_id, path, project, created, bundle, model, turn_count, status}` dicts. Sorted by created (most recent first). The `keyword` search greps `transcript.jsonl` (never `events.jsonl`). The `status` filter calls `diagnosis.diagnose_transcript()` — potentially slow, so it is opt-in.

```python
session_info(session_dir: Path) -> dict
```

Returns metadata + turn count + health status + size info. Reads `metadata.json` and runs diagnosis.

**Design choices:**

- Default `sessions_root` of `~/.amplifier/projects` is the only place the CLI app's path convention lives — it is a default, not hardcoded logic
- All functions accept explicit paths, so they work for any app using the same file format but different locations
- Keyword search is deliberately limited to transcript (safe) and never touches events.jsonl (potentially 100k+ token lines that crash tools)

### `scripts/amplifier-session.py` — Unified CLI Script

A single script with subcommands that replaces all multi-step bash choreography. The agent goes from 4+ bash commands to 1 command per operation.

**Subcommands:**

```
python scripts/amplifier-session.py diagnose <session>
python scripts/amplifier-session.py repair <session>
python scripts/amplifier-session.py rewind <session>
python scripts/amplifier-session.py info <session>
python scripts/amplifier-session.py find [--project X] [--after DATE] [--before DATE] [--keyword TEXT] [--status healthy|broken]
python scripts/amplifier-session.py fork <session> --turn N
```

**The `<session>` argument is flexible:**

- Full path: `/home/user/.amplifier/projects/myproj/sessions/abc123-def456/`
- Session ID: `abc123-def456-...`
- Partial ID: `abc123` (resolved via `finder.py`)

Resolution logic lives in `finder.py` and is called by every subcommand. If a partial ID matches multiple sessions, it prints the matches and exits with an error (does not guess).

**Output contract:** All subcommands produce structured JSON on stdout (parseable by the agent) with human-readable summaries on stderr. The agent parses JSON reliably; a human running it directly still gets useful output.

**The script is thin** — each subcommand is roughly:
1. Resolve session argument via `finder.py`
2. Call the appropriate library function(s) from `diagnosis.py`, `store.py`, `fork.py`, etc.
3. Format and print the result

### `cli.py` — Shared Arg Parsing Helpers

Shared argument parsing utilities used by the unified script. Keeps the script itself focused on orchestration rather than boilerplate.

### Unchanged Components

- **`events.py`** — Refined to import `read_jsonl` from `store.py` instead of maintaining a private `_read_jsonl`. Otherwise unchanged.
- **`capabilities.py`** — Working directory helpers. Unchanged.
- **`fork.py`** — Logic stays; I/O moves to `store.py` imports. Orphan detection delegates to `messages.py`.

## Data Flow

### Repair Flow (Agent Perspective)

```
Agent receives: "repair session abc123"
  │
  ▼
python scripts/amplifier-session.py diagnose abc123
  │
  ├── finder.resolve_session("abc123") → /home/user/.amplifier/projects/myproj/sessions/abc123-.../
  ├── store.load_transcript_with_lines(session_dir) → entries with line numbers
  ├── diagnosis.diagnose_transcript(entries) → DiagnosisResult
  └── stdout: JSON with status, failure modes, recommended action
  │
  ▼
Agent reads JSON, reports diagnosis to user
  │
  ▼
python scripts/amplifier-session.py repair abc123
  │
  ├── finder.resolve_session("abc123") → session_dir
  ├── store.backup(transcript_path, "pre-repair") → .bak file
  ├── store.load_transcript_with_lines(session_dir) → entries
  ├── diagnosis.diagnose_transcript(entries) → diagnosis
  ├── diagnosis.repair_transcript(entries, diagnosis) → repaired entries
  ├── store.write_transcript(session_dir, repaired) → writes file
  └── stdout: JSON with repair summary, backup path
  │
  ▼
python scripts/amplifier-session.py diagnose abc123
  │
  └── stdout: JSON with status: "healthy" (verification)
```

### Session Search Flow

```
Agent receives: "find my recent sessions about authentication"
  │
  ▼
python scripts/amplifier-session.py find --keyword "authentication" --after "last week"
  │
  ├── finder.find_sessions(keyword="authentication", after="last week")
  │     ├── Walk ~/.amplifier/projects/*/sessions/*/
  │     ├── Filter by date from metadata.json
  │     ├── Grep transcript.jsonl for keyword (never events.jsonl)
  │     └── Return sorted list with metadata
  └── stdout: JSON array of matching sessions
```

## Error Handling

### Script-Level

- **Ambiguous session reference:** If a partial ID matches multiple sessions, the script prints all matches to stderr and exits with a non-zero code. The JSON on stdout contains the matches so the agent can present options to the user.
- **Session not found:** Clear error message with the search paths tried. Suggests using `find` subcommand.
- **Transcript parse errors:** Reported in the diagnosis result. The script never crashes on malformed JSONL — it reports what it can and flags unparseable lines.
- **Backup failures:** If backup cannot be created (permissions, disk space), repair/rewind refuse to proceed. Safety first.

### Agent-Level

- **Script not found:** The agent locates the script with `find / -path '*/amplifier-foundation/scripts/amplifier-session.py'`. If not found, reports failure to user rather than attempting manual operations.
- **Script fails:** The agent reports the error output to the user. No manual fallback — if the script fails, STOP. This is an explicit design constraint to prevent the Haiku-class model from attempting manual JSONL surgery.

## Testing Strategy

### Existing Tests (Preserved)

- `slice.py` test suite (701 lines) → updated imports to `messages.py`
- Repair script test suite (1037 lines) → updated to import from `diagnosis.py`
- Fork test suite → updated to verify `store.py` integration

### New Tests

- **`finder.py` tests** — Session resolution with full paths, full IDs, partial IDs, ambiguous IDs, missing sessions. Search with project/date/keyword filters. Uses temp directories with synthetic session structures.
- **`store.py` tests** — Round-trip read/write for transcript, metadata, JSONL. Backup creation and naming. Edge cases: empty files, malformed JSONL lines, missing files.
- **`amplifier-session.py` CLI tests** — Subprocess-based (same pattern as existing `TestCLI` class). Verify each subcommand's JSON output structure, exit codes, and stderr messages.

### Phase Gate

Each implementation phase must leave all existing tests passing before moving to the next phase. No phase ships with broken tests.

## Implementation Phasing

### Phase 1 — Core Restructure

| Step | Action | Risk |
|------|--------|------|
| 1 | Create `store.py` — consolidate all I/O helpers, introduce filename constants | Low |
| 2 | Create `messages.py` — rename from `slice.py`, add `is_real_user_message()`, parameterize synthetic content | Low |
| 3 | Create `diagnosis.py` — extract pure algorithms from repair script, wire to `messages.py` | Medium |
| 4 | Refactor `fork.py` — import from `store.py` and `messages.py` | **High** (most code, most tests) |
| 5 | Update `events.py` — import `read_jsonl` from `store.py` | Low |
| 6 | Update `__init__.py` — export new modules, add compat shim for `slice` imports | Low |
| 7 | Migrate existing tests — update imports, verify all 1700+ lines pass | Medium |

**Risk note:** Step 4 (refactoring `fork.py`) touches the most code with the most existing tests. It is the highest-risk step but also where the biggest deduplication payoff lives.

### Phase 2 — New Capabilities

| Step | Action |
|------|--------|
| 8 | Create `finder.py` — session discovery, ID resolution, search/filter |
| 9 | Create `scripts/amplifier-session.py` — unified script with all subcommands |
| 10 | Write tests for finder and the script |
| 11 | Deprecate `scripts/session-repair.py` (keep briefly for backward compat, then remove) |

### Phase 3 — Agent Rewire

| Step | Action |
|------|--------|
| 12 | Rewrite `session-analyst.md` — replace bash choreography with script invocations |
| 13 | Update `session-repair-knowledge.md` — align with new script subcommands |
| 14 | Clean up `session-storage-knowledge.md` — remove stale manual repair recipe (lines 196-209) |

Phase 3 is documentation-only — no code tests needed.

### Agent Instructions After Rewrite

The repair/search/discovery sections of `session-analyst.md` collapse to:

```markdown
## Session Operations

Use the `amplifier-session.py` script for all session operations.
Find it: SCRIPT="$(find / -path '*/amplifier-foundation/scripts/amplifier-session.py' -type f 2>/dev/null | head -1)"

All commands accept full paths, session IDs, or partial IDs.

### Diagnose
python "$SCRIPT" diagnose <session>

### Repair (always diagnose first)
python "$SCRIPT" diagnose <session>
python "$SCRIPT" repair <session>
python "$SCRIPT" diagnose <session>    # verify

### Rewind (only when user explicitly requests)
python "$SCRIPT" diagnose <session>
python "$SCRIPT" rewind <session>
python "$SCRIPT" diagnose <session>    # verify

### Find sessions
python "$SCRIPT" find --project myproj --after 2025-01-01
python "$SCRIPT" find --keyword "authentication"

### Session info
python "$SCRIPT" info <session>
```

**What stays in agent instructions:** Identity notice, events.jsonl safety warnings, conversation turn model (conceptual), failure modes (conceptual awareness), parent session modification notice, search synthesis guidance, final response contract.

**What gets removed/shortened:** All bash `find` patterns for session discovery, manual detection commands, storage locations section, stale manual repair recipe.

**Net effect:** Agent instructions drop from ~500 lines to ~300 lines, and the actionable parts become single-command invocations.

## Final Module Layout

```
amplifier_foundation/session/
├── __init__.py          # Public API with clear exports
├── messages.py          # Level 1: Pure message operations (renamed from slice.py)
├── diagnosis.py         # Level 1: Pure diagnosis/repair/rewind algorithms
├── store.py             # Level 2: JSONL file I/O (consolidated)
├── events.py            # Level 2: Events.jsonl operations (refined)
├── finder.py            # Level 3: Session discovery and resolution
├── fork.py              # Composite: Fork operations (refactored)
├── capabilities.py      # Orthogonal: Working dir helpers (unchanged)
└── cli.py               # Shared arg parsing helpers for scripts

scripts/
└── amplifier-session.py # Unified CLI script with subcommands
```

## Open Questions

None — all architectural questions were resolved through expert consultation (foundation-expert, amplifier-expert, zen-architect, core-expert).
