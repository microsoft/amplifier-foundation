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
| `max_llm_calls` | integer | - | Override the Layer 1 LLM-call budget for this delegation (per session leg). `0` disables the budget for this call. Only takes effect when `settings.max_llm_calls` is configured -- see "Layer 1 call budget" below. |

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
      timeout: 300
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

This module can bound how many main-loop LLM calls a delegated child
session may make in one leg, as a first line of defense in front of
`settings.timeout`'s wall-clock backstop (see spec: 298-replacement,
"Layered Bounding for Delegated Sessions"). The enforcement mechanism is
the child's own orchestrator `max_iterations` config (e.g.
`amplifier-module-loop-streaming`'s streaming loop) -- this module does not
count LLM calls itself; it only injects a value into the child's
`orchestrator_config` at spawn time.

**Ships dark.** `settings.max_llm_calls` defaults to `None`, which means no
budget is injected at all -- every child session gets exactly the
`orchestrator_config` its parent would have given it anyway (rank 4 below).
Nothing about this feature is active until an operator sets
`settings.max_llm_calls` to a positive integer.

### Precedence (highest first)

| Rank | Source | Key | Status |
|------|--------|-----|--------|
| 1 | Per-call tool input | `max_llm_calls` | Implemented |
| 2 | Per-agent frontmatter | `agents[agent_name]["budget"]["max_llm_calls"]` | **Not implemented -- see "Known gaps" below** |
| 3 | This module's setting | `settings.max_llm_calls` | Implemented (default `null`) |
| 4 | Inherited parent `orchestrator_config` | `max_iterations` (only if 1 and 3 are both absent) | Implemented (pre-existing inheritance path, untouched) |

An explicit `0` at rank 1 means "no Layer 1 budget for this delegation" --
the wall-clock backstop (`settings.timeout`) still applies. Negative values
and booleans are rejected at the point they are supplied (fail loud, not a
silent coercion).

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

## Note

This module is recommended over `tool-task` for new development due to its enhanced context control and bug fixes.
