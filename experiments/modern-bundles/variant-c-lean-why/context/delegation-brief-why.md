# Agent Delegation

You can delegate tasks to specialist agents using the `delegate` tool. Delegation isn't a politeness convention -- it's a context-budget tool. The root session's window is finite; each delegated task consumes the agent's window instead, and only the summary returns.

## When to Delegate

- File exploration (>2 files) -- agents absorb the token cost of reading and grepping, returning only the distilled answer. Doing it inline pollutes the root context with material you'll never reference again.
- Git operations (commits, PRs) -- the git-ops agent has safety protocols (clean-state checks, branch guards) that prevent the destructive-by-default mistakes git makes easy.
- Architecture/design decisions -- zen-architect carries design heuristics and trade-off frameworks the root session doesn't have loaded. Designing inline means re-deriving them.
- Implementation from specs -- modular-builder follows established implementation patterns (module shape, testing layout) so the output is consistent with the rest of the codebase.
- Debugging -- bug-hunter uses hypothesis-driven methodology that prevents the common failure mode of fixing symptoms while the root cause stays live.

## Basic Usage

```python
delegate(agent="foundation:explorer", instruction="Survey the auth module")
```

The instruction should be specific enough that the agent doesn't have to guess at scope -- vague instructions produce vague reports.

## Context Control

| Parameter | Values | Use |
|-----------|--------|-----|
| `context_depth` | `none`, `recent`, `all` | How much history to pass |
| `context_scope` | `conversation`, `agents`, `full` | Which content types |

- `context_depth="none"` for independent tasks -- passing irrelevant history wastes the agent's window and can bias its reasoning toward earlier (now stale) decisions.
- `context_scope="agents"` when agent B needs to see agent A's output -- this is how a multi-agent chain hands off findings without re-doing work.
- `context_depth="all", context_scope="full"` for self-delegation -- the resumed self needs the complete picture to continue coherently.

## Parallel Dispatch

Launch multiple agents simultaneously for independent work -- serial delegation when there's no dependency wastes wall-clock time and adds turns to the conversation for no analytical gain:

```python
delegate(agent="foundation:explorer", instruction="Check frontend", context_depth="none")
delegate(agent="foundation:explorer", instruction="Check backend", context_depth="none")
```
