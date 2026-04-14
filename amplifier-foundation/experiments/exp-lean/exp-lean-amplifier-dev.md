---
bundle:
  name: exp-lean-amplifier-dev
  version: 0.1.0
  description: |
    EXPERIMENTAL lean amplifier-dev bundle -- minimal token footprint with dev tooling.
    
    Stripped amplifier-dev for cost-effective dev sessions: 71% reduction (60K → 17.8K tokens).
    Includes lean-foundation base plus dev agents, Python tooling, and LSP.
    
    To use this bundle:
      amplifier bundle add foundation:experiments/exp-lean/exp-lean-amplifier-dev --name exp-lean-amplifier-dev
      amplifier bundle use exp-lean-amplifier-dev

includes:
  - bundle: foundation:experiments/exp-lean/behaviors/lean-foundation
  - bundle: foundation:experiments/exp-lean/behaviors/lean-amplifier-dev
---

# Experimental Lean Amplifier Dev Bundle

Development bundle with **71% token reduction** vs standard amplifier-dev.

## What's Included

| Component | Included |
|-----------|----------|
| Everything in exp-lean-foundation | Yes |
| Dev agents (explorer, git-ops, bug-hunter, zen-architect, modular-builder, file-ops, post-task-cleanup) | Yes |
| Python tooling (ruff, pyright, auto-check hook) | Yes |
| LSP / code intelligence (pyright) | Yes |
| apply_patch (V4A diff editing) | Yes |
| Browser testing, terminal testing | No |
| Design intelligence | No |
| Stories, dot-graph, superpowers | No |
| Recipes, shadow environments | No |
| Expert consultants (amplifier, core, foundation) | No |
| Routing matrix, MCP | No |

## Measured Token Cost

| Bundle | Input Tokens |
|--------|-------------|
| amplifier-dev (standard) | 60,539 |
| **exp-lean-amplifier-dev** | **17,790** |

Measured in a clean shadow environment with `amplifier run --mode single "hello"`.

## Feedback

This is an experiment in token cost reduction. Please report whether sessions feel
degraded, about the same, or better than standard amplifier-dev.

@foundation:experiments/exp-lean/context/system-base.md
