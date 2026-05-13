# Delegation

## Why Delegate

Agents carry @-mentioned documentation and specialized tools that your root session lacks. Delegating gets better results AND preserves your context window — agents absorb the token cost of exploration (~20,000 tokens) and return distilled summaries (~500 tokens).

## When to Delegate

| Trigger | Agent | Why this agent |
|---------|-------|----------------|
| Multi-file exploration (>2 files) | `foundation:explorer` | Structured sweep, context sink |
| Python code navigation (types, calls, refs) | `python-dev:code-intel` | LSP/Pyright semantic tools |
| Architecture, design, or review | `foundation:zen-architect` | Philosophy context, analysis modes |
| Implementation from spec | `foundation:modular-builder` | Requires complete spec with paths/interfaces |
| Bug or error reported | `foundation:bug-hunter` | Hypothesis-driven debugging methodology |
| Git commit, PR, branch ops | `foundation:git-ops` | Safety protocols, co-author attribution |
| Session analysis or events.jsonl | `foundation:session-analyst` | Handles 100k+ token lines safely |
| External API or MCP setup | `foundation:integration-specialist` | Clean integration patterns |
| Security review | `foundation:security-guardian` | OWASP, secrets, crypto review |
| Web research | `foundation:web-research` | Search + fetch + synthesis |
| Bundle/behavior authoring | `foundation:bundle-design-expert` | Full lifecycle: design → model → implement |
| Ecosystem/multi-repo coordination | `foundation:ecosystem-expert` | DTU validation, cross-repo workflows |
| Recipe creation or editing | `recipes:recipe-author` | Schema knowledge, validation |

**Single-file reads, quick `git status`, `ls`** — do directly. Everything else: delegate.

## Delegate Parameters

```python
delegate(
    agent="foundation:explorer",
    instruction="Survey auth module structure",  # Include WHY + WHAT needed
    context_depth="none"|"recent"|"all",         # HOW MUCH context
    context_scope="conversation"|"agents"|"full", # WHICH content
    model_role="coding",                          # Optional model override
    session_id="abc123...",                       # Resume existing session
)
```

| Depth | Use when |
|-------|----------|
| `none` | Independent task, fresh perspective |
| `recent` | Work just completed (default) |
| `all` | Complex multi-step work, PRs |

| Scope | Use when |
|-------|----------|
| `conversation` | Independent work (default) |
| `agents` | Agent B needs to see Agent A's output |
| `full` | Debugging — agent needs everything |

## Parallel Dispatch

For non-trivial investigations, dispatch multiple agents simultaneously:

```python
delegate(agent="foundation:explorer", instruction="Survey auth/", context_depth="none")
delegate(agent="python-dev:code-intel", instruction="Trace authenticate() calls")
delegate(agent="foundation:zen-architect", instruction="Review auth design")
```

Different agents bring different tools (LSP vs grep vs design analysis). Together they reveal more than any single perspective.

## Self-Delegation

When your context is filling up, spawn yourself to continue:
```python
delegate(agent="self", instruction="Continue analysis", context_depth="all", context_scope="full")
```
