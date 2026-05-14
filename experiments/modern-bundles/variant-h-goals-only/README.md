# Variant H: Goals-Only

## What This Tests

**THE floor test.** All foundation and amplifier-dev instruction context
(~1,400 lines, ~7,000 tokens) replaced with 10 lines of goals. Same tools,
same agents, same hooks.

**Hypothesis:** If quality matches the baseline, all that context is
redundant for modern models. If quality drops, the delta shows exactly
what foundation context adds.

## What Changed

| Layer | Baseline (amplifier-dev) | Variant H |
|-------|-------------------------|-----------|
| Foundation body (common-system-base.md) | ~400 lines | 10 lines of goals |
| Delegation instructions | ~180 lines | Removed |
| Multi-agent patterns | ~150 lines | Removed |
| Bundle awareness | ~30 lines | Removed |
| Amplifier-dev context (ecosystem-map, workflows, testing) | ~370 lines | Removed |
| Bundle design awareness | ~30 lines | Removed |
| Awareness index | ~130 lines | Removed |

## What Stayed the Same

- All tools (filesystem, bash, web, search, delegate, todo, skills, recipes, etc.)
- All agents (bug-hunter, explorer, zen-architect, git-ops, etc.)
- All hooks (streaming-ui, status-context, redaction, todo-reminder, etc.)
- All external bundles (superpowers, python-dev, browser-tester, etc.)
- External bundle awareness context (recipes, skills, superpowers, etc.)

## Estimated Token Impact

- **Baseline:** ~10,000-12,000 tokens (foundation body + behaviors + awareness)
- **Variant H:** ~100 tokens (10 lines of goals)
- **Reduction:** ~95% of foundation-controlled context removed

Note: External bundle context (~4,000-5,000 tokens) remains — it comes
with the tool/agent bundles. AGENTS.md files (~330 tokens) are user-level
and always loaded.

## How to Run

```bash
amplifier bundle add 'git+https://github.com/microsoft/amplifier-foundation@main#subdirectory=experiments/variant-h-goals-only/variant-h-goals-only.md' --name variant-h
amplifier bundle use variant-h
```

Or for eval harness:
```bash
amplifier run --bundle experiments/variant-h-goals-only/variant-h-goals-only.md "<prompt>"
```
