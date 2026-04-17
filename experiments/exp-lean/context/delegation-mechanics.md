# Agent Delegation -- Mechanics

The `delegate` tool spawns sub-sessions for subtasks. Mechanics only -- this bundle's
actual agent roster (if any) is documented separately. Consult the tool's dynamic
"Available agents" list at runtime to see what is actually registered.

## Delegation targets

| Target form | Meaning |
|---|---|
| `agent="<name>"` | A specialist agent registered in this bundle (if any). Check runtime list. |
| `agent="self"` | Spawn yourself as a sub-session. Fresh context window. |
| `agent="namespace:path/to/bundle"` | Direct reference to any bundle as an agent. |

If the tool reports "No agents currently registered," only `self` and bundle paths
are usable in this bundle.

## When delegation pays off

- Task matches a registered agent's specialty (runtime list).
- Multi-file exploration -- sub-session absorbs the token cost, parent gets summary.
- Parallel independent subtasks -- dispatch multiple sub-sessions simultaneously.
- You are nearing context limits and want to continue work in a fresh window.

## Basic usage

```python
delegate(agent="self", instruction="Continue the analysis in a fresh context")
```

## Context control

Two orthogonal parameters govern what the sub-session sees:

| Parameter | Values | Use |
|-----------|--------|-----|
| `context_depth` | `none`, `recent`, `all` | How much history to pass |
| `context_scope` | `conversation`, `agents`, `full` | Which content types |

- `context_depth="none"` -- independent task, clean slate.
- `context_scope="agents"` -- sub-session B sees sub-session A's output.
- `context_depth="all", context_scope="full"` -- self-delegation continuing heavy work.

## Parallel dispatch

Independent subtasks run concurrently. Call delegate multiple times in one turn:

```python
delegate(agent="self", instruction="Check frontend code", context_depth="none")
delegate(agent="self", instruction="Check backend code", context_depth="none")
```

## Session resume

Delegate returns a full `session_id`. Pass it back to continue:

```python
result = delegate(agent="self", instruction="Start the analysis")
delegate(session_id=result["session_id"], instruction="Now examine the edge cases")
```

## Relaying sub-session results

The user sees only your final response text, not tool output. When a sub-session
returns findings, summarize the important parts in your own words. Do not assume
the user saw the raw result.
