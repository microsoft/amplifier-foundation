# Anchors Bundle

A lean experimental bundle that shapes the agent's conduct with a short, explicit
set of **behavioral principles** placed at the very top of the system prompt --
rather than encoding behavior across large rule documents.

The bet: a handful of sharp, well-chosen principles -- which the model re-reads on
every turn -- steer conduct more cheaply and more reliably than verbose policy
text. The whole system prompt is small enough to stay cheap on every turn, while
still producing disciplined, delegation-aware behavior.

## Install

```bash
amplifier bundle add 'git+https://github.com/microsoft/amplifier-foundation@main#subdirectory=bundles/anchors/bundle.md' --name anchors
amplifier bundle use anchors
```

Single-quote the URI to prevent shell expansion of the `#` fragment. The `.md`
suffix is required.

## The idea

Most bundle behavior is shaped by long, rule-heavy context that is paid for in
tokens on every single turn. This experiment goes the other way: encode the
desired conduct as a small set of named principles, loaded once at the head of
the system prompt, and let those principles -- not paragraphs of policy -- anchor
how the agent acts.

The bundle is built around a small set of principles:

1. **Investigate before acting** -- understand the problem fully before proposing
   solutions; curiosity over assumptions.
2. **Minimum viable change** -- nothing speculative; every line, file, and
   abstraction must earn its place.
3. **Verify at every step** -- run tests, check types, validate assumptions;
   evidence before assertions.
4. **Delegate complex work** -- push multi-file exploration, design, implementation,
   debugging, and git work to sub-agents so the parent context stays lean.

These principles do most of the steering. Everything else in the bundle exists to
support them.

## What's in it

| Component | Notes |
|-----------|-------|
| **System prompt** | A minimal prompt: the four principles, a short operating-rules block, and a commit-message convention. That's it. |
| **Orchestrator** | `loop-streaming` with extended thinking enabled. |
| **Context** | `context-simple`, 300k window, auto-compact at 80%. |
| **Tools** | A standard roster at the parent: filesystem, bash, web, search, todo, apply-patch, delegate, skills (discovery only), mode, recipes. |
| **Agents** | Six thin, purposeful sub-agents -- `explorer`, `architect`, `builder`, `debugger`, `git-ops`, `researcher` -- each a tight USE-WHEN contract so delegation targets are obvious. |
| **Hooks** | Free-cost UX hooks only (`streaming-ui`, `status-context`, `redaction`, `logging`, `todo-reminder`, `todo-display`, `session-naming`) -- runtime behavior with no per-turn context cost. |
| **Skills** | Discovery available; auto-injection (`visibility`) turned off to keep first-turn context small. |

## Design philosophy

- **Principles over policy.** Behavior is anchored by a few re-read-every-turn
  principles instead of large rule documents. Cheaper context, sharper steering.
- **Thin agents, clear contracts.** Each sub-agent has a one-paragraph identity
  and an explicit USE-WHEN / DO-NOT-USE-WHEN boundary, so the parent always has an
  obvious target to delegate to.
- **Pay only for what earns its place.** Skill auto-injection is off, hooks are
  limited to the free-cost set, and the system prompt is deliberately short.
  Capabilities are added back only when a real task shows they're missing.
- **Delegation-aware by default.** "Delegate complex work" is a first-class
  principle, not an afterthought -- the parent is expected to route non-trivial
  work to the agents.

## Self-contained by design

Every module, tool, hook, and behavior is referenced by its full
`git+https://...` source URL rather than a `foundation:` namespace. The bundle's
own agents and context are referenced through its own `anchors:`
namespace. It does not depend on the `amplifier-foundation` bundle being composed
or registered -- it just happens to live in this repo's `bundles/` folder, and
can be lifted out without rewiring.

## Files

```
anchors/
├── README.md                 # this file
├── bundle.md      # bundle entrypoint (session / tools / hooks / agents)
├── agents/
│   ├── explorer.md           # multi-file recon
│   ├── architect.md          # design / spec / review
│   ├── builder.md            # implementation from spec
│   ├── debugger.md           # hypothesis-driven bug fixing
│   ├── git-ops.md            # git / gh operations
│   └── researcher.md         # external research
└── context/
    └── system.md             # the behavioral principles + operating rules
```

## Status

Promoted from `experiments/behavioral-anchor` to a published bundle. Version 0.1.0. The principle set and tool/agent roster are a
starting point and will be adjusted as observation shows what helps or hurts.
