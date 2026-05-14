# Variant F: Deduplicated + Stripped

## What This Tests

**Conservative cleanup.** Same architecture as amplifier-dev, ~50% content
reduction by removing duplicate routing tables, tutorials modern models
don't need, and implementation details the agent never uses.

**Hypothesis:** Removing noise improves signal-to-noise ratio. Quality
should hold or improve because the model spends fewer tokens parsing
redundant instructions.

## Content Changes

### Collapsed Duplicates

| Content | Before (locations) | After |
|---------|-------------------|-------|
| Delegation routing | 4+ files (delegation-instructions, AWARENESS_INDEX, multi-agent-patterns, 12 awareness files) | 1 routing table in delegation.md (~69 lines) |
| Verification guidance | 3 files (common-agent-base, common-system-base, superpowers) | 1 section in system-core.md |
| Tool usage guidance | 3 files (common-system-base, common-agent-base, tool schemas) | Tool schemas only |
| Git commit format | 2 files | 1 mention in system-core.md |
| Skills-checking rules | 3 files | Kept in superpowers (external bundle) |

### Deleted (tutorials/implementation details)

| Content | Lines | Why |
|---------|-------|-----|
| lsp-general.md, python-lsp.md | ~220 | Tool schemas explain LSP. Model knows Pyright. |
| editing-guidance.md | ~40 | Tool descriptions explain each tool. |
| AWARENESS_INDEX.md (standalone) | ~130 | Routing collapsed into delegation.md |
| multi-agent-patterns.md (standalone) | ~150 | Essential patterns collapsed into delegation.md |
| delegation-instructions.md (standalone) | ~180 | Replaced by delegation.md |
| ecosystem-map.md + dev-workflows.md + testing-patterns.md | ~370 | Replaced by dev-essentials.md (~41 lines) |
| common-system-base.md + common-agent-base.md | ~400 | Replaced by system-core.md (~52 lines) |

### WHY-Anchored Rewrites

All remaining instructions use WHY reasoning instead of flat rules.

| Before | After |
|--------|-------|
| "NEVER create files unless necessary" | "Edit existing files — the project structure is intentional." |
| "MUST delegate" (×12) | "Agents carry @-mentioned docs and specialized tools you lack. Delegating gets better results AND preserves your context." |

## What Stayed the Same

- All tools (unchanged)
- All agents (unchanged descriptions, same roster)
- All hooks (unchanged)
- All external bundle includes and their context (unchanged)
- Architecture (same composition pattern)

## Estimated Token Count

| Layer | Baseline | Variant F | Delta |
|-------|----------|-----------|-------|
| Foundation-controlled context | ~7,000 tokens | ~3,000 tokens | -57% |
| External bundle context | ~5,000 tokens | ~5,000 tokens | unchanged |
| Tool schemas + agent descriptions | ~3,000 tokens | ~3,000 tokens | unchanged |
| **Total first-turn** | **~15,000** | **~11,000** | **-27%** |

## How to Run

```bash
amplifier bundle add 'git+https://github.com/microsoft/amplifier-foundation@main#subdirectory=experiments/variant-f-dedup-strip/variant-f-dedup-strip.md' --name variant-f
amplifier bundle use variant-f
```
