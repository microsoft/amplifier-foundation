# Variant I: Goals + Forced Delegation

## What This Tests

**Structural forced delegation.** Root session has ONLY orchestration tools
(delegate, todo, mode, load_skill, recipes, team_knowledge). All file, code,
bash, web, and search tools are removed from root — agents have the full
toolkit.

**Hypothesis:** Forcing delegation via tool restriction achieves the context
sink pattern mechanically. Complex tasks benefit from agent specialization;
simple tasks pay an overhead cost. The question is where the break-even is.

**Decision:** Uses Option 1 (standard agent descriptions) to isolate the
forced-delegation variable without confounding it with description changes.

## What Changed vs Baseline

| Layer | Baseline (amplifier-dev) | Variant I |
|-------|-------------------------|-----------|
| Root tools | ~15 direct tools | 6 orchestration tools |
| System prompt | ~1,400 lines | 15 lines of orchestrator goals |
| All foundation context | Present | Removed |
| Agent descriptions | Standard (unchanged) | Standard (unchanged) |

## Tools at Root vs Agents

| Root HAS | Root LOST (agents have them) |
|----------|------------------------------|
| delegate | bash, read_file, write_file, edit_file |
| todo | apply_patch, grep, glob |
| mode | web_search, web_fetch |
| load_skill | LSP, python_check |
| recipes | dot_graph, terminal_inspector |
| team_knowledge | |

## Risk

Simple one-file operations (read a config, fix a typo) that take 1 tool call
now require a full agent dispatch. This adds latency and cost for trivial tasks.

## How to Run

```bash
amplifier bundle add 'git+https://github.com/microsoft/amplifier-foundation@main#subdirectory=experiments/variant-i-goals-forced-delegation/variant-i-goals-forced-delegation.md' --name variant-i
amplifier bundle use variant-i
```
