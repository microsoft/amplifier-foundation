# tool-delegate

Delegate tasks to specialized agents with enhanced context control.

## Purpose

The `delegate` tool enables AI agents to spawn sub-sessions for complex subtasks. It provides fine-grained control over context inheritance and supports seamless session resumption.

## Key Features

### Two-Parameter Context System

Control context inheritance with two orthogonal parameters:

- **`context_depth`** - HOW MUCH context to pass:
  - `none` - Clean slate, no parent context
  - `recent` - Last N turns (configurable via `context_turns`)
  - `all` - Full conversation history

- **`context_scope`** - WHICH content to include:
  - `conversation` - Only user/assistant text (strips all tool content)
  - `agents` - Includes delegate/task tool results
  - `full` - Includes ALL tool results

### Session Resume

Resume sessions using the full `session_id` returned by previous delegate calls:

```
session_id: "abc123-def456-..._foundation:explorer"
```

#### Routing survives the resume

A delegation pinned to a model stays pinned on every leg. The resume path
sends the app layer's `session.resume` capability two optional kwargs
alongside `sub_session_id` / `instruction`:

| kwarg | value |
|---|---|
| `provider_preferences` | the preferences this call resolved to |
| `model_role` | the raw role string the caller asked for |

Precedence: what the caller states on the resume call wins; otherwise the
routing recorded when this tool spawned that sub-session is reused. So
`delegate(agent=X, model_role="reasoning")` followed by
`delegate(session_id=..., instruction=...)` resumes under `reasoning`,
instead of silently falling back to settings priority (the measured
"resume wipes the role" defect).

Both kwargs are **optional on the capability**. An app layer whose resume
capability still takes only `(sub_session_id, instruction)` keeps working
unchanged — the kwargs are withheld and a warning naming them is logged, so
the downgrade is never silent. `delegate:agent_resumed` also carries
`model_role` / `provider_preferences` now, matching `delegate:agent_spawned`
so the two legs can be compared in telemetry.

### Optional delegation limits

**No model-call cap or wall-clock deadline is enabled by default.** Leave
`settings.max_llm_calls` and `settings.timeout` absent or `null` for normal
delegation. Do not assign an arbitrary per-call budget just because a task
looks small; use a cap only when the user requests one.

The enforcement code remains available for explicit opt-in. A call budget is
enforced by a supporting child orchestrator through `max_iterations`; a
deadline is enforced by this delegate tool. Provider HTTP timeouts, tool
permissions, cancellation, and recursion controls are separate and unchanged.
Explicit limits inherited from a parent orchestrator are also preserved.

#### Delegate timeout

Configure `settings.timeout` with a positive finite number of seconds to
enable a deadline for spawn and resume. Absent or `null` means no
delegate-level deadline; a hanging child may then require manual cancellation.

**Migration:** the former implicit four-hour deadline is no longer active.
An operator who wants it can explicitly configure `settings.timeout: 14400`.

A timeout **returns; it never raises.** That is load-bearing: delegates in a
parallel batch are awaited under `asyncio.gather` with no `return_exceptions`,
so a raising straggler would propagate and discard every *completed* sibling's
result in the same batch.

A timeout returns `success: false` with structured output containing
`status: timeout`, `completed: false`, the child `session_id`, the agent
identity when available, and metadata with `timeout_seconds`, `elapsed_s`,
`resumable: false`, and `resume_status: pending_child_cleanup`. It emits
`delegate:error` with `error_type: delegate_timeout`, not
`delegate:agent_completed`, because the cancelled child may still be cleaning
up.

#### Partial results on timeout

The output also carries a `partial_available` boolean and, when true, the
straggler's recovered text under `partial_response` -- **never** under
`response`, which is the success-only key. Supporting fields:
`partial_segments`, `partial_source`, `partial_truncated`,
`partial_chars_total`, and a `guidance` string stating plainly that the text
is unfinished work, not a result. `settings.partial_max_chars` (default
`20000`) caps the preserved text, keeping the most recent tail.

Recovery is best-effort and **optional**. The app layer may register a
`session.partial` capability:

```
(sub_session_id: str) -> {"text": str, "segments": int, "source": str} | None
```

sync or async. When it is absent, returns nothing, returns something
malformed, or raises, the result degrades to `partial_available: false`. It
never degrades to success, and recovery never raises out of the timeout path
-- a raise there would discard the very siblings this path protects.

**`partial_available` is `false` for every timeout until an app layer
registers that capability.** That is the correct, expected state of this repo
on its own, not a defect to work around.

Do not immediately resume the returned session ID. The coordinated
persistence-capable `amplifier-app-cli` spawner may persist the interrupted
session after cancellation cleanup finishes, but the delegate timeout response
does not claim that persistence is complete or that the session is ready to
resume. (This honest `resumable: false` reporting stays until
`amplifier-app-cli#260` lands -- it is not on the critical path for Layer 1 or
Layer 3 to ship, since Layer 1's own exit is a normal return and its
`resumable: true` is already a fact today.)

### Tool Inheritance Fix

Agent's explicit tool declarations are always honored, even when parent excludes them. Exclusions apply only to inheritance, not explicit declarations.

## Parameters

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `agent` | string | required | Agent to delegate to (e.g., 'foundation:explorer', 'self') |
| `instruction` | string | required | Clear instruction for the agent |
| `session_id` | string | - | Resume existing session (use full session_id from previous call) |
| `context_depth` | enum | "recent" | How much context: none, recent, all |
| `context_turns` | integer | 5 | Number of turns when context_depth is 'recent' |
| `context_scope` | enum | "conversation" | Which content: conversation, agents, full |
| `provider_preferences` | array | - | Ordered provider/model preferences |
| `max_llm_calls` | integer | - | Optional call cap for a new child, only when requested by the user. Positive values override the module setting; `0` skips it without clearing inherited orchestrator limits. Resume retains the saved child limit. |

## Configuration

```yaml
modules:
  tool-delegate:
    features:
      self_delegation:
        enabled: true
      session_resume:
        enabled: true
      context_inheritance:
        enabled: true
        max_turns: 10
      provider_selection:
        enabled: true
      return_contract:
        enabled: false        # default OFF; purely additive when on
        strip_block: true     # remove the parsed json block from `response`
        reask_on_nonconformance: false  # reserved for a future stage; inert today
    settings:
      exclude_tools:
        - delegate  # Default: spawned agents can't further delegate
      exclude_hooks: []
      timeout: null  # No deadline unless explicitly configured.
      max_llm_calls: null      # Layer 1 call budget (spec: 298-replacement).
                                # None/unset (default): ships dark -- no
                                # budget is injected into any child session;
                                # today's behavior is unchanged. Set to a
                                # positive integer to enforce a per-leg
                                # LLM-call budget (see below). 0 is
                                # equivalent to null (explicit no-budget).
      budget_warn_ratio: 0.8    # Fraction of max_llm_calls at which a
                                # one-shot "start converging" warning fires.
                                # Only meaningful when max_llm_calls is set.
```

### Structured Return Contract

Flag-gated, default **off**. When `features.return_contract.enabled` is `true`, this
tool appends a short instruction to every spawned/resumed agent's instruction, asking
it to append a fenced ` ```json ` block to the end of its normal prose answer:

```json
{
  "summary": "at most 3 sentences -- the answer in brief",
  "findings": [
    {"claim": "one assertion, carried forward verbatim",
     "evidence": "file:line, command run, or URL -- empty string if genuinely none",
     "confidence": "high | medium | low"}
  ],
  "not_covered": ["a thing in scope the agent did NOT examine"],
  "artifacts": [{"path": "file written or modified", "description": "what changed"}]
}
```

Only `findings` is required, and only `claim` within each finding -- everything else
defaults on parse. The parser (`_parse_return_contract`) is tolerant: a partially-good
block is normalized and kept, never discarded outright.

On return, `ToolResult.output` gains one additive `contract` key:

```jsonc
{
  "response": "...",        // the json block is stripped when parsing succeeded
                             // and strip_block is enabled; untouched otherwise
  "contract": {
    "conformant": true,      // false on parse failure, null when the feature is off
    "reason": null,          // populated string when conformant is false
    "summary": "...",
    "findings": [ /* ... */ ],
    "not_covered": [ /* ... */ ],
    "artifacts": [ /* ... */ ]
  },
  "session_id": "...", "agent": "...", "turn_count": 1, "status": "success", "metadata": {}
}
```

**Non-conformance never fails the delegation.** If no fenced json block is found, it
fails to parse, or it's missing the required `findings` array, `contract.conformant`
is `false` with a `reason`, and `response` is byte-identical to what the agent
actually returned -- the fallback is indistinguishable from this feature not existing.

**Per-agent opt-out** -- an agent can opt out even when the feature is globally
enabled, via its `meta:` frontmatter:

```yaml
meta:
  name: git-ops
  return_contract: false
```

This is an opt-**out** that defaults to inheriting the global flag, not a per-agent
opt-in matrix.

**Telemetry** -- no new events. `delegate:agent_completed` gains five additive fields:
`contract_conformant` (`bool | None`, `None` when the feature is off),
`findings_count`, `evidence_backed_count` (findings with a non-empty `evidence`),
`not_covered_count`, and `artifacts_count` (all `int | None`, `None` alongside
`contract_conformant is None`).

## Layer 1 call budget

This module can optionally bound how many main-loop LLM calls a new child
session may make in one leg. The enforcement mechanism is
the child's own orchestrator `max_iterations` config (e.g.
`amplifier-module-loop-streaming`'s streaming loop) -- this module does not
count LLM calls itself; it only injects a value into the child's
`orchestrator_config` at spawn time.

**Off by default.** `settings.max_llm_calls` defaults to `None`, which means no
budget is injected at all -- every child session gets exactly the
`orchestrator_config` its parent would have given it anyway (rank 4 below).
To opt in, an operator sets `settings.max_llm_calls` to a positive integer,
or the caller supplies a positive `max_llm_calls` for that new child. The
per-call input works even when the module setting is absent.

### Precedence (highest first)

| Rank | Source | Key | Status |
|------|--------|-----|--------|
| 1 | Per-call tool input | `max_llm_calls` | Implemented |
| 2 | Per-agent frontmatter | `agents[agent_name]["budget"]["max_llm_calls"]` | **Not implemented -- see "Known gaps" below** |
| 3 | This module's setting | `settings.max_llm_calls` | Implemented (default `null`) |
| 4 | Inherited parent `orchestrator_config` | `max_iterations` (only if 1 and 3 are both absent) | Implemented (pre-existing inheritance path, untouched) |

An explicit `0` at rank 1 means "inject no additional call budget" -- it skips
rank 3 but does not erase rank 4's inherited `max_iterations`. A deadline
applies only if `settings.timeout` is explicitly configured. Negative values
and booleans are rejected at the point they are supplied (fail loud, not a
silent coercion). Resume uses the child's persisted orchestrator configuration;
the per-call budget input does not replace a saved child's limit.

### Negotiated feature, not a contract requirement

`max_iterations` is an **advisory** orchestrator config convention (see
`amplifier-core/docs/contracts/ORCHESTRATOR_CONTRACT.md`), not a required
one. If a budget was requested but the child's orchestrator doesn't
implement `max_iterations` at all (a third-party orchestrator, or one with
no budget support), the child's `orchestrator:complete` metadata will carry
no `llm_call_budget` key. This module detects that and:

- logs a warning naming the agent and the fact that only the wall-clock
  backstop applies
- sets `metadata.budget_enforced: false` on the returned `ToolResult`

so the gap is loud, never silent.

### Known gaps

- **Per-agent frontmatter override (precedence rank 2) is not
  implemented.** A top-level `budget:` block in an agent `.md` file's
  frontmatter does **not** currently survive into
  `coordinator.config["agents"][name]`:
  `amplifier_foundation.bundle._dataclass._load_agent_file_metadata` only
  forwards a fixed allowlist of top-level frontmatter keys (`tools`,
  `providers`, `hooks`, `session`, `provider_preferences`, `model_role`,
  `agents`) -- `budget` is not among them, and is silently dropped. This was
  verified empirically (not just read from the source) -- see
  `tests/test_delegate_call_budget.py`'s
  `test_agent_frontmatter_budget_key_is_dropped`. Ranks 1, 3, and 4 of the
  precedence chain ship and work today; rank 2 is a follow-up that requires
  a change in `amplifier-foundation`'s agent-frontmatter loader, not this
  module.
- **Cross-provider healthy-`llm_calls` distribution is not yet measured.**
  Any default set for `settings.max_llm_calls` today is a hypothesis, not a
  measurement -- see the spec's staged-rollout plan (S0 telemetry-only -> S1
  warn-only -> S2 generous enforcement -> S3 target enforcement) before
  setting a production default.

## Matrix provenance on spawn telemetry

`delegate:agent_spawned` records the `provider_preferences` a delegation
resolved to. It now also records **which routing matrix file produced them**,
under an optional `routing_matrix` key:

```json
"routing_matrix": {
  "matrix_name": "anthropic",
  "matrix_path": "/home/u/.amplifier/routing/anthropic.yaml",
  "matrix_source": "user",
  "shadowed_paths": ["/opt/bundles/routing-matrix/routing/anthropic.yaml"]
}
```

A user file in `~/.amplifier/routing/` silently outranks the bundle's own
same-named matrix, so without this a surprising resolution in the event stream
is indistinguishable from a shadowed matrix, a shipped-matrix change, or no
routing at all. The values are **read from** the `model_role_resolver`
capability's published `matrix_path` / `matrix_source` / `shadowed_paths`
attributes (hooks-routing publishes them); nothing here re-derives matrix
precedence.

The same key is added to `delegate:model_role_unresolved`, where "which matrix
file failed to serve this role" is the first question asked.

### Reading it correctly

| Situation | `routing_matrix` |
|---|---|
| Resolver produced the preferences and reports a source | present |
| No `model_role` (no routing requested) | **absent** |
| Explicit `provider_preferences` pin, or agent-level default | **absent** — the matrix never saw them |
| No routing bundle installed | **absent** |
| Resolver is a non-matrix strategy, or an older routing bundle | **absent** |

**Absent means UNKNOWN, never "no shadowing."** Read it with
`payload.get("routing_matrix")`. Every capture recorded before this field
existed lacks the key, so an analyzer that treats absence as a negative
assertion would silently clear exactly the shadowed sessions the field exists
to catch. The field is purely additive: no existing key's name, type, or value
changed, and consumers that ignore it are unaffected.

## Note

This module is recommended over `tool-task` for new development due to its enhanced context control and bug fixes.
