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

### Delegate Timeout

Delegated spawn and resume operations time out after 1800 seconds by default.
Configure `settings.timeout` with a positive finite number of seconds to change
the limit, or set it explicitly to `null` to disable the delegate-level timeout.

A timeout returns `success: false` with structured output containing
`status: timed_out`, the child `session_id`, the agent identity when available,
and metadata with `timeout_seconds`, `resumable: false`, and
`resume_status: pending_child_cleanup`. It emits `delegate:error` with
`error_type: delegate_timeout`, not `delegate:agent_completed`, because the
cancelled child may still be cleaning up.

Do not immediately resume the returned session ID. The coordinated
persistence-capable `amplifier-app-cli` spawner may persist the interrupted
session after cancellation cleanup finishes, but the delegate timeout response
does not claim that persistence is complete or that the session is ready to
resume.

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
    settings:
      exclude_tools:
        - delegate  # Default: spawned agents can't further delegate
      exclude_hooks: []
      timeout: 1800  # Default; set to null to disable
```

## Note

This module is recommended over `tool-task` for new development due to its enhanced context control and bug fixes.
