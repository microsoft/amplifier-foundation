---
skill:
  name: delegation-patterns
  description: Agent delegation routing table and patterns — when to delegate, which agent, delegate() parameters, parallel dispatch, self-delegation.
  version: 1.0.0
---

# Delegation Patterns

## Why Delegate

Agents carry @-mentioned documentation and specialized tools that your root session lacks. Delegating gets better results AND preserves your context window — agents absorb the token cost of exploration (~20,000 tokens) and return distilled summaries (~500 tokens).

## Routing Table

| Trigger | Agent | Why |
|---------|-------|-----|
| Multi-file exploration (>2 files) | `foundation:explorer` | Structured sweep, context sink |
| Python code navigation | `python-dev:code-intel` | LSP/Pyright semantic tools |
| Architecture, design, review | `foundation:zen-architect` | Philosophy context, analysis modes |
| Implementation from spec | `foundation:modular-builder` | Requires complete spec |
| Bug or error | `foundation:bug-hunter` | Hypothesis-driven debugging |
| Git commit, PR, branches | `foundation:git-ops` | Safety protocols, co-author |
| Session events.jsonl | `foundation:session-analyst` | Handles 100k+ token lines |
| External API/MCP | `foundation:integration-specialist` | Clean integration |
| Security review | `foundation:security-guardian` | OWASP, secrets, crypto |
| Web research | `foundation:web-research` | Search + fetch + synthesis |
| Bundle authoring | `foundation:bundle-design-expert` | Full lifecycle |
| Ecosystem coordination | `foundation:ecosystem-expert` | DTU validation, cross-repo |
| Recipe creation | `recipes:recipe-author` | Schema knowledge |

## Delegate Parameters

```python
delegate(
    agent="foundation:explorer",
    instruction="Survey auth module",     # WHY + WHAT needed
    context_depth="none"|"recent"|"all",  # HOW MUCH context
    context_scope="conversation"|"agents"|"full",  # WHICH content
    model_role="coding",                  # Optional model override
    session_id="abc123...",               # Resume existing session
)
```

## Parallel Dispatch

For investigations, dispatch multiple agents simultaneously:

```python
delegate(agent="foundation:explorer", instruction="Survey auth/", context_depth="none")
delegate(agent="python-dev:code-intel", instruction="Trace authenticate() calls")
delegate(agent="foundation:zen-architect", instruction="Review auth design")
```

## Self-Delegation

When context fills up:
```python
delegate(agent="self", instruction="Continue analysis", context_depth="all", context_scope="full")
```
