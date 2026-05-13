# Agent Delegation

The `delegate` tool spawns sub-sessions. Consult the tool's runtime "Available agents" list for this bundle's registered agents -- because the registry is the source of truth at call time, while any list written here can drift as agents are added or renamed. This bundle registers four agents (`explorer`, `planner`, `coder`, `tester`); other specialists are reachable by bundle path.

## Targets

| Form | Meaning |
|---|---|
| `agent="self"` | Fresh sub-session, clean context |
| `agent="namespace:path/to/bundle"` | Any bundle as an agent (e.g., `foundation:explorer`) |

Pick the most specific target you have -- because a named specialist (`build-up:planner`) ships with role-tuned instructions and tools, while `self` is a generic fan-out mechanism with no specialization. Reach for `self` only when no named agent fits the work.

## Context control

| Parameter | Values |
|---|---|
| `context_depth` | `none`, `recent`, `all` |
| `context_scope` | `conversation`, `agents`, `full` |

- Independent subtask: `context_depth="none"` -- because passing irrelevant context wastes tokens in the sub-session and can mislead the agent into "solving" the wrong problem it sees upstream. A clean context is faster and more accurate when the work genuinely stands alone.
- Sub-session B sees sub-session A's output: `context_scope="agents"` -- because this is the cheap way to chain specialists (e.g., planner -> coder) without you manually copy-pasting the spec into the next instruction. The downstream agent gets the upstream result verbatim.
- Self-delegation continuing heavy work: `context_depth="all", context_scope="full"` -- because resuming long-running investigation needs the full prior conversation to avoid re-deriving conclusions you already reached. Use this sparingly; it's the most expensive option.

## Parallel dispatch

```python
delegate(agent="self", instruction="Check frontend", context_depth="none")
delegate(agent="self", instruction="Check backend", context_depth="none")
```

Fan out when subtasks are genuinely independent -- because parallel dispatch finishes in wall-clock time rather than sequential time, and each sub-session has its own context budget. Do NOT fan out when results depend on each other (sub-session B needs A's findings) -- that's a chain, not a fan-out, and forcing it parallel just gives you two half-informed answers to reconcile.

## Session resume

```python
r = delegate(agent="self", instruction="Start analysis")
delegate(session_id=r["session_id"], instruction="Now examine edge cases")
```

Use session resume for genuine continuations -- because the resumed session keeps its prior tool state, file knowledge, and reasoning thread, which is exactly what you want for "now go deeper" follow-ups. For a fresh angle on the same topic, prefer a new sub-session with the relevant facts re-stated; carrying stale assumptions forward is the classic resume failure mode.

## Relay results

The user sees only your final response text. When a sub-session returns findings, summarize in your final response -- because tool output is hidden from the user, and a sub-session finding that you don't relay is functionally invisible. Bugs spotted, gaps flagged, alternatives considered: these are the exact high-value outputs that justified the delegation in the first place. Do not assume the user saw raw tool output.
