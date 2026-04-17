# Registered Agents -- exp-lean-amplifier-dev

This bundle composes seven specialist agents (all from `foundation:`). Use the
`delegate` tool with these agent names. See `delegation-mechanics.md` for how the
tool works (context control, parallel dispatch, session resume).

## Roster

| Agent | Use for |
|---|---|
| `foundation:bug-hunter` | Errors, test failures, unexpected behavior. Hypothesis-driven debugging. |
| `foundation:explorer` | Multi-file code exploration, codebase surveys, understanding unfamiliar modules. |
| `foundation:file-ops` | Targeted single-file or multi-file reads/edits/searches without broader exploration scope. |
| `foundation:git-ops` | Commits, branches, PRs, GitHub CLI, repo discovery. Has safety protocols. |
| `foundation:modular-builder` | Implementation from a complete specification (file paths, interfaces, success criteria given). |
| `foundation:zen-architect` | Design, architecture planning, code review against simplicity principles. Three modes: ANALYZE, ARCHITECT, REVIEW. |
| `foundation:post-task-cleanup` | Review touched files after a task completes, remove temp artifacts, ensure hygiene. |

## Handoff pattern

Under-specified implementation tasks flow through two agents:

1. `foundation:zen-architect` -- analyze the problem, produce a spec with file paths, interfaces, and success criteria.
2. `foundation:modular-builder` -- implement strictly from the spec.

Do not send under-specified tasks directly to `foundation:modular-builder`. It will stall or ask clarifying questions. Get the spec first.

## Not in this bundle

These common `foundation:` agents are intentionally excluded from `exp-lean-amplifier-dev` to keep the bundle lean:

- `foundation:session-analyst` -- session debugging and transcript surgery
- `foundation:web-research` -- external documentation lookups
- `foundation:security-guardian` -- security reviews
- `foundation:test-coverage` -- test gap analysis
- `foundation:integration-specialist` -- third-party API integrations

If you need one of these, either self-delegate (`agent="self"`) with an appropriate instruction, or compose a different bundle that includes them.
