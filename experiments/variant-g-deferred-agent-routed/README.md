# Variant G: Deferred + Agent-Routed

## What This Tests

**Agent descriptions as the sole routing signal + deferred loading.**
All awareness files deleted — the delegate tool schema's agent descriptions
already say "Use PROACTIVELY when..." which is the routing instruction.
Reference docs (delegation patterns, ecosystem overview, testing, workflows)
move behind `load_skill()` for on-demand loading.

**Hypothesis:** Agent descriptions are sufficient for routing. Awareness
files are redundant middleware (~345 lines repeating what agents say).
Deferred loading via skills gives the model what it needs, when it needs it.

## What Changed vs Variant F

| Layer | Variant F | Variant G |
|-------|-----------|-----------|
| Delegation docs | Loaded at start (69 lines) | Behind skill `delegation-patterns` |
| Dev essentials | Loaded at start (41 lines) | Split into skills `dev-workflows` + `testing-patterns` |
| System core | 52 lines | Trimmed to ~40 lines (goals + essentials) |
| On-demand skills | None | 4 skills available via load_skill() |

## Awareness Files Deleted

These are suppressed because agent descriptions already carry routing:

| File | What agent description already says |
|------|--------------------------------------|
| bundle-awareness.md | foundation-expert desc covers it |
| bundle-design-awareness.md | bundle-design-expert desc covers it |
| recipe-awareness.md | recipe-author desc covers it |
| browser-awareness.md | browser-operator desc covers it |
| terminal-awareness.md | terminal-operator desc covers it |
| dot-awareness.md | dot-author desc covers it |
| discovery-awareness.md | discovery-orchestrator desc covers it |
| dtu-awareness.md | dtu-profile-builder desc covers it |
| amplifier-tester-awareness.md | setup-digital-twin desc covers it |

**Note:** External bundles still inject their own context (recipes,
superpowers, python-dev, etc.). This experiment controls only
foundation-managed content.

## Skills Created

| Skill | Lines | Loaded when |
|-------|-------|-------------|
| `delegation-patterns` | 60 | Complex delegation needed |
| `amplifier-ecosystem` | 35 | Working on Amplifier internals |
| `dev-workflows` | 40 | Multi-repo development |
| `testing-patterns` | 35 | Testing Amplifier changes |

## Estimated Token Count

| Layer | Variant F | Variant G | Delta |
|-------|-----------|-----------|-------|
| Foundation body | ~500 tokens | ~350 tokens | -30% |
| Foundation delegation context | ~700 tokens | 0 (behind skill) | -100% |
| Foundation dev context | ~400 tokens | 0 (behind skill) | -100% |
| External bundle context | ~5,000 tokens | ~5,000 tokens | unchanged |
| **Total first-turn** | **~11,000** | **~9,000** | **-18%** |
| **Skills available on demand** | 0 | ~170 lines (~800 tokens) | n/a |

## How to Run

```bash
amplifier bundle add 'git+https://github.com/microsoft/amplifier-foundation@main#subdirectory=experiments/variant-g-deferred-agent-routed/variant-g-deferred-agent-routed.md' --name variant-g
amplifier bundle use variant-g
```
