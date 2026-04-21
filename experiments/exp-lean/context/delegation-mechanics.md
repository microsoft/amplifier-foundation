# Agent Delegation

The `delegate` tool spawns sub-sessions. Consult the tool's runtime "Available agents" list for this bundle's registered agents.

## Targets

| Form | Meaning |
|---|---|
| `agent="<name>"` | Registered specialist (see runtime list) |
| `agent="self"` | Fresh sub-session, clean context |
| `agent="namespace:path/to/bundle"` | Any bundle as an agent |

## Context control

| Parameter | Values |
|---|---|
| `context_depth` | `none`, `recent`, `all` |
| `context_scope` | `conversation`, `agents`, `full` |

- Independent subtask: `context_depth="none"`.
- Sub-session B sees sub-session A's output: `context_scope="agents"`.
- Self-delegation continuing heavy work: `context_depth="all", context_scope="full"`.

## Parallel dispatch

```python
delegate(agent="self", instruction="Check frontend", context_depth="none")
delegate(agent="self", instruction="Check backend", context_depth="none")
```

## Session resume

```python
r = delegate(agent="self", instruction="Start analysis")
delegate(session_id=r["session_id"], instruction="Now examine edge cases")
```

## Relay results

The user sees only your final response text. When a sub-session returns findings, summarize in your final response. Do not assume the user saw raw tool output.
