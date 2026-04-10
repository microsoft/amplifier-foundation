---
bundle:
  name: exp-lean-foundation
  version: 0.1.0
  description: |
    EXPERIMENTAL lean foundation bundle -- minimal token footprint.
    
    Stripped Foundation for cost-effective sessions: 84% reduction (54K → 8.6K tokens).
    Core tools, essential UX hooks, delegate tool, skills. No modes, no expert
    consultants, no heavy context docs.
    
    To use this bundle:
      amplifier bundle add foundation:experiments/exp-lean/exp-lean-foundation --name exp-lean-foundation
      amplifier bundle use exp-lean-foundation

includes:
  - bundle: foundation:experiments/exp-lean/behaviors/lean-foundation
---

# Experimental Lean Foundation Bundle

Minimal Amplifier infrastructure with **84% token reduction** vs standard Foundation.

## What's Included

| Component | Included |
|-----------|----------|
| Session config (orchestrator, context) | Yes |
| Core tools (filesystem, bash, web, search, todo) | Yes |
| Delegate tool (agent orchestration) | Yes |
| Skills tool (on-demand context loading) | Yes |
| UX hooks (streaming, status, redaction, logging) | Yes |
| Todo hooks (reminder, display, session naming) | Yes |
| Modes (brainstorm, debug, verify, etc.) | No |
| Expert consultants (amplifier, core, foundation) | No |
| Heavy context docs (delegation-instructions, multi-agent-patterns) | No |
| Design intelligence, browser-tester, terminal-tester | No |
| Recipes, superpowers, routing-matrix | No |

## Measured Token Cost

| Bundle | Input Tokens |
|--------|-------------|
| Foundation (standard) | 54,568 |
| **exp-lean-foundation** | **8,614** |

Measured in a clean shadow environment with `amplifier run --mode single "hello"`.

## Feedback

This is an experiment in token cost reduction. Please report whether sessions feel
degraded, about the same, or better than standard Foundation.

@foundation:experiments/exp-lean/context/system-base.md
