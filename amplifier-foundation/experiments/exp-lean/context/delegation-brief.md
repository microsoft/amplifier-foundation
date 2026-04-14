# Agent Delegation

You can delegate tasks to specialist agents using the `delegate` tool.

## When to Delegate

- File exploration (>2 files) -- agents absorb the token cost, returning only summaries
- Git operations (commits, PRs) -- git-ops has safety protocols
- Architecture/design decisions -- zen-architect has design expertise
- Implementation from specs -- modular-builder follows implementation patterns
- Debugging -- bug-hunter uses hypothesis-driven methodology

## Basic Usage

```python
delegate(agent="foundation:explorer", instruction="Survey the auth module")
```

## Context Control

| Parameter | Values | Use |
|-----------|--------|-----|
| `context_depth` | `none`, `recent`, `all` | How much history to pass |
| `context_scope` | `conversation`, `agents`, `full` | Which content types |

- `context_depth="none"` for independent tasks
- `context_scope="agents"` when agent B needs to see agent A's output
- `context_depth="all", context_scope="full"` for self-delegation

## Parallel Dispatch

Launch multiple agents simultaneously for independent work:

```python
delegate(agent="foundation:explorer", instruction="Check frontend", context_depth="none")
delegate(agent="foundation:explorer", instruction="Check backend", context_depth="none")
```
