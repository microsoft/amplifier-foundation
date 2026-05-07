# Experimental Build-Up Bundle

The inverse of `exp-lean`: where `exp-lean` strips `amplifier-foundation` down, `build-up` starts from zero and adds back only what is necessary, with **delegation as the primary scale-out mechanism**.

**The radical move in v0.2.0:** the parent session has only `todo` and `delegate`. It cannot read files, run commands, search code, or make edits. Every concrete action goes through one of four self-sufficient sub-session agents that carry their own tools.

## Install

```bash
amplifier bundle add 'git+https://github.com/microsoft/amplifier-foundation@main#subdirectory=experiments/build-up/build-up-foundation.md' --name build-up-foundation
amplifier bundle use build-up-foundation
```

Single-quote the URI to prevent shell expansion of the `#` fragment. The `.md` suffix is required.

## Architecture

```
parent session ─ tools: todo, delegate
  │
  ├─► explorer  ─ tools: bash, filesystem, search, todo, delegate
  │              read-only multi-file reconnaissance
  │
  ├─► planner   ─ tools: filesystem, todo, delegate
  │              design / architecture / review (no bash, no write)
  │
  ├─► coder     ─ tools: bash, filesystem, search, todo, delegate
  │              implementation from a complete spec
  │
  └─► tester    ─ tools: bash, filesystem, search, todo, delegate
                 test execution + coverage + test generation
```

The parent's job is to understand intent, plan, dispatch, and synthesize. Real work happens in sub-sessions.

## What's in it

| Component | Parent | Agents | Notes |
|-----------|--------|--------|-------|
| `tool-todo` | Yes | All four | Planning |
| `tool-delegate` | Yes | All four | Composition |
| `tool-bash` | **No** | explorer, coder, tester | Run commands, tests, lint |
| `tool-filesystem` | **No** | All four | Read/write files, glob, list |
| `tool-search` | **No** | explorer, coder, tester | Grep |
| `tool-web`, `tool-skills`, `tool-mcp`, `tool-task`, `tool-slash-command`, `tool-lsp`, `apply-patch` | No | No | Earn back via smoke testing |
| Free-cost hooks (`streaming-ui`, `status-context`, `redaction`, `logging`, `todo-reminder`, `todo-display`, `session-naming`) | Yes | — | No per-turn context cost |
| Pre-registered agents | — | — | The 4 above; reach foundation specialists by bundle path if needed |
| Modes, recipes, superpowers, design intelligence, etc. | No | No | Add when value is demonstrated |

## Design imperatives

Derived from prior baseline measurements of `amplifier-foundation` and `amplifier-dev`:

1. **Delegate-by-default** — bundle prompts must explicitly bias toward `delegate`. Doesn't happen organically.
2. **No pre-registered dev agents from foundation** — observed only 1 delegation across 24 baseline trials. Generic foundation agents go unused. Build-up ships its own four, tuned to be the obvious target for any non-trivial work.
3. **Skill loading is fragile** — non-deterministic across model tiers. Don't depend on it for critical paths.
4. **Cache write/read ≈ 95% of token cost** — the biggest lever is shrinking initial context size. Every line of bundle prompt is paid every turn.
5. **Test across model tiers** — Sonnet and Opus behave dramatically differently.

## Why the parent has no tools

If the parent has `read_file`, the model will use it. If it has `bash`, the model will use it. The eval baseline showed models reach for direct implementation over delegation by ~20:1 even when scenarios mapped perfectly to delegation handoffs. Removing the temptation removes the regression. The parent's *only* path to action is `delegate`.

The cost: every concrete action requires a delegation hop. The benefit: parent context stays lean across hundreds of turns, and the agent-router pattern is enforced structurally rather than just rhetorically.

## Comparison to siblings

| Bundle | Approach | Parent tools | Agents pre-registered | Skills | Token target (initial input) |
|---|---|---|---|---|---|
| `amplifier-foundation` (standard) | Full | 6+ | 7+ foundation specialists | Yes | ~54.5k |
| `exp-lean-foundation` | Strip-down | 6 | 0 | Yes (visibility off) | ~8.6k (measured) |
| **`build-up-foundation` v0.1.0** | Build-up | 4 | 0 | No | ~? (not yet measured) |
| **`build-up-foundation` v0.2.0** | Build-up + agent-router | **2** | **4 (built into the bundle, tool-equipped)** | No | TBD |

## Methodology

**Start with the absolute minimum and add back only what is earned.** When a smoke test or real task shows the model is degraded by missing a capability, restore it — don't preemptively include things "just in case." The agent boundaries are designed to be *the* mechanism for everything: if you find yourself wanting a tool at the parent, the answer is almost always "make an agent do it."

## Smoke testing

```bash
# In a Digital Twin Universe environment for clean-slate measurement
amplifier run --mode single "hello"
```

Look for:
- Initial input token cost.
- Whether the model attempts to use absent tools at the parent (e.g., reaches for `read_file` directly — that's a bug to surface).
- Whether `delegate` is the model's first reach for any non-trivial task.
- Whether the four agents are correctly chosen (planner before coder for under-specified work; tester after coder; explorer when context is missing).

## Files

```
build-up/
├── README.md                              # this file
├── build-up-foundation.md                 # bundle entrypoint
├── behaviors/
│   └── build-up-foundation.yaml           # session/tool/hook/agent wiring
├── agents/
│   ├── explorer.md                        # multi-file recon
│   ├── planner.md                         # design / spec / review
│   ├── coder.md                           # implementation from spec
│   └── tester.md                          # test execution / coverage / gen
└── context/
    ├── system-base.md                     # parent system prompt
    └── delegation-mechanics.md            # `delegate` tool reference
```

## Status

Experimental, version 0.2.0. Not yet smoke tested. Tool / agent / hook set is the maximally-aggressive starting point and will be relaxed only when observation demonstrates a clear gap.
