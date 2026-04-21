# Experimental Lean Bundles

Stripped-down Amplifier bundles for cost-effective sessions. Two tiers available.

## Install

```bash
# Lean Foundation (84% token reduction: 54,568 -> 8,634 tokens)
amplifier bundle add 'git+https://github.com/microsoft/amplifier-foundation@main#subdirectory=experiments/exp-lean/exp-lean-foundation.md' --name exp-lean-foundation
amplifier bundle use exp-lean-foundation

# Lean Amplifier Dev (70% token reduction: 60,539 -> 18,046 tokens)
amplifier bundle add 'git+https://github.com/microsoft/amplifier-foundation@main#subdirectory=experiments/exp-lean/exp-lean-amplifier-dev.md' --name exp-lean-amplifier-dev
amplifier bundle use exp-lean-amplifier-dev
```

Single-quote the URI to prevent shell expansion of the `#` fragment. The `.md` suffix is required — pointing at the directory alone fails because there is no `bundle.md` at the directory root.

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
| **exp-lean-foundation** | **8,634** |
| amplifier-dev (standard) | 60,539 |
| **exp-lean-amplifier-dev** | **18,046** |

## Delegation context

Both bundles ship a small amount of delegation-related context so the model uses the `delegate` tool correctly.

- `context/delegation-mechanics.md` (~300 tokens) — loaded by both bundles. Bundle-agnostic reference for the `delegate` tool: target forms (named agent, `self`, bundle path), context control parameters, parallel dispatch, session resume, and result relay. Contains no hardcoded agent names — the model consults the tool's runtime "Available agents" list for each bundle's registered specialists.
- `context/exp-lean-amplifier-dev-agents.md` (~240 tokens) — loaded by `exp-lean-amplifier-dev` only. Enumerates the 7 registered specialists (`bug-hunter`, `explorer`, `file-ops`, `git-ops`, `modular-builder`, `zen-architect`, `post-task-cleanup`) and the `zen-architect` → `modular-builder` handoff pattern for under-specified implementation tasks.

`exp-lean-foundation` registers **zero** agents — its only sensible delegation target is `agent="self"` for fresh sub-sessions. Any named foundation specialist (e.g., `foundation:explorer`) can still be reached via `agent="foundation:explorer"` since bundle paths work as agent targets, but they are not pre-registered.

### Agents excluded from exp-lean-amplifier-dev

Seven foundation agents ship in the full dev bundle but were cut from the lean version to keep context small. If a session needs one, it can still be invoked by bundle path (`agent="foundation:<name>"`) — it just isn't pre-registered.

| Excluded agent | Typical use | How to still use it |
|---|---|---|
| `session-analyst` | Debugging/rewinding sessions, analyzing events.jsonl | `delegate(agent="foundation:session-analyst", ...)` |
| `web-research` | Web search, documentation lookup | `delegate(agent="foundation:web-research", ...)` |
| `security-guardian` | Security reviews, vulnerability assessment | `delegate(agent="foundation:security-guardian", ...)` |
| `test-coverage` | Test gap analysis, test-case suggestions | `delegate(agent="foundation:test-coverage", ...)` |
| `integration-specialist` | External service/API/MCP integration | `delegate(agent="foundation:integration-specialist", ...)` |

### Why the handoff pattern

`modular-builder` refuses under-specified tasks — it expects file paths, interfaces, and success criteria up front. `zen-architect` does the analysis and design work, producing a spec that `modular-builder` implements strictly. Skipping the architect step for non-trivial work tends to push `modular-builder` into research loops or paralysis.

## Feedback

These are experiments in token cost reduction. Please report whether sessions feel degraded, about the same, or better than standard bundles.
