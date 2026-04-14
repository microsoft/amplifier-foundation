# Experimental Lean Bundles

Stripped-down Amplifier bundles for cost-effective sessions. Two tiers available.

## Install

```bash
# Lean Foundation (84% token reduction: 54,568 -> 8,614 tokens)
amplifier bundle add foundation:experiments/exp-lean/exp-lean-foundation --name exp-lean-foundation
amplifier bundle use exp-lean-foundation

# Lean Amplifier Dev (71% token reduction: 60,539 -> 17,790 tokens)
amplifier bundle add foundation:experiments/exp-lean/exp-lean-amplifier-dev --name exp-lean-amplifier-dev
amplifier bundle use exp-lean-amplifier-dev
```

## exp-lean-foundation

Minimal infrastructure -- core tools, delegate, skills, UX hooks. No modes, no expert consultants, no heavy context docs.

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

## exp-lean-amplifier-dev

Dev tooling on top of lean-foundation -- dev agents, Python tooling, LSP, apply_patch.

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

All numbers measured in a clean shadow environment (zero host contamination) with `amplifier run --mode single "hello"`.

| Bundle | Input Tokens |
|--------|-------------|
| Foundation (standard) | 54,568 |
| **exp-lean-foundation** | **8,614** |
| amplifier-dev (standard) | 60,539 |
| **exp-lean-amplifier-dev** | **17,790** |

## Feedback

These are experiments in token cost reduction. Please report whether sessions feel degraded, about the same, or better than standard bundles.
