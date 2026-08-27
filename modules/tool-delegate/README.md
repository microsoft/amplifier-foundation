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

## Note

This module is recommended over `tool-task` for new development due to its enhanced context control and bug fixes.
