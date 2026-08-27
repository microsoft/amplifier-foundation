# Delegation Batching Hook

Detects sequential delegation waves and injects a batching reminder before the
next wave is planned.

## Problem It Solves

`delegate` calls emitted in a single assistant turn run concurrently; calls
split across separate turns run sequentially, and the root blocks on each one
before planning the next. Stage 1 of the parallel-delegation guidance work
(see `context/agents/multi-agent-patterns.md` and
`context/agents/delegation-instructions.md`) addresses this at the prose
layer. This hook is the Stage 2 reinforcement: it fires at the actual
decision point (after wave N's results land, before wave N+1 is planned) and
nudges the root to check whether the next wave could have gone out with the
last one.

This module is **opt-in and ships default-off**. It is not composed into
`behaviors/agents.yaml`. Compose `behaviors/delegation-batching.yaml`
explicitly to enable it, so its effect can be measured in isolation from
Stage 1's guidance edits.

## Detection

The hook watches `tool:post` events for the `delegate` tool. Each assistant
turn that emits one or more `delegate` calls shares one `parallel_group_id`.
The hook counts each *new* `parallel_group_id` it observes as one delegation
wave, and injects an ephemeral reminder once a wave threshold is reached —
unless the wave was wide (multiple delegations in the same turn, i.e. already
batched) or non-delegate work happened between waves (in which case the wave
chain resets, since that wave genuinely could not have been planned earlier).

## Configuration

```yaml
hooks:
  - module: hooks-delegation-batching
    source: git+https://github.com/microsoft/amplifier-foundation@main#subdirectory=modules/hooks-delegation-batching
    config:
      enabled: true           # master switch (default: true)
      min_wave_index: 2       # first wave index that may be nudged (default: 2)
      max_nudges: 3           # per-session cap on nudges (default: 3)
      narrow_wave_only: true  # only nudge narrow (< 2 member) waves (default: true)
```

## Telemetry

Emits `delegate:batching_nudge` with payload
`{"wave_count": int, "parallel_group_id": str, "wave_size": int, "nudges_issued": int, "metadata": None}`.

## Design Notes

- State is per mounted instance, not keyed by session id — modules mount per
  session, so the instance already scopes to one session.
- `group_sizes[group_id]` at the moment of any single `tool:post` is a lower
  bound on the wave's final width (members complete in nondeterministic
  order). A single-member wave always reads `1` and always trips the narrow
  guard; a wide wave is suppressed by its second completion at the latest.
  `nudged_groups` dedupe bounds the imprecision to at most one spurious
  reminder per wave.
- The injection is `ephemeral=True` — it must never persist into the
  transcript, or it accumulates across a long session and poisons later
  context.
