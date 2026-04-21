# Registered Agents

| Agent | Use for |
|---|---|
| `foundation:bug-hunter` | Errors, test failures, unexpected behavior. Hypothesis-driven debugging. |
| `foundation:explorer` | Multi-file code exploration, codebase surveys. |
| `foundation:file-ops` | Targeted single/multi-file reads, edits, searches. |
| `foundation:git-ops` | Commits, branches, PRs, GitHub CLI, repo discovery. |
| `foundation:modular-builder` | Implementation from a complete spec. |
| `foundation:zen-architect` | Design, architecture, code review. Modes: ANALYZE, ARCHITECT, REVIEW. |
| `foundation:post-task-cleanup` | Post-task file review, remove temp artifacts. |

## Handoff

Under-specified implementation: route to `foundation:zen-architect` first (produces spec), then `foundation:modular-builder` implements strictly from that spec. Do not send under-specified tasks directly to `modular-builder`.

See `delegation-mechanics.md` for tool usage.
