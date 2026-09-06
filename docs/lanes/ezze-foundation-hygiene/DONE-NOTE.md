# Lane ezze — foundation hygiene (four defects found while deciding what NOT to pull)

**Outcome: A — RESOLVED.** All four deliverables DONE. Nothing NOT-POSSIBLE. No blocker.

Work item: `model_performance-ezze` (project `model_performance`)
Branch: `lane/ezze-foundation-hygiene` off `200dfe6`
Artifact root: `docs/lanes/ezze-foundation-hygiene/` (artifact-path/v1)

---

## Deliverable status

| # | Deliverable | State |
|---|---|---|
| 1 | session-analyst: glob + `SCRIPT_DISCOVERY_LINE` + `:42` notice + events.jsonl 5× → 1× | **DONE** |
| 2 | common-agent-base: one footer, emoji contradiction, todo absolute, `sources:` + round-trip test | **DONE** |
| 3 | ecosystem-expert: dead `## Delegation Pattern` deleted; ISSUE_HANDLING cost flagged, not restructured | **DONE** |
| 4 | bundle-design-expert `<example>` lines; awareness → one-line pointer | **DONE** |
| — | Real-session check for (1) under the hashed cache dir | **DONE** |
| — | Suite green, draft PR, no merge | **DONE** |

**OPTIONAL-IF-CAP-PERMITS dropped:** none. The goal declared none, and the cap never bound.

---

## The judgment call (goal said measure, do not assume)

**Question:** the item says delete the dead IDENTITY NOTICE at `:42` because the anchors family sets
`exclude_tools: [tool-delegate]`. Does **foundation's own** composition do the same?

**Answer: yes — so the notice is dead and was DELETED.** Three independent measurements:

1. `behaviors/agents.yaml:31` — `settings.exclude_tools: [tool-delegate]  # Spawned agents can't further delegate by default`
2. Root `bundle.md` includes `foundation:behaviors/agents`, so every foundation session inherits it.
3. Even absent that config, the module default is the same:
   `modules/tool-delegate/amplifier_module_tool_delegate/__init__.py:636` —
   `settings.get("exclude_tools", ["tool-delegate"])`

The only carve-out that would restore delegate is an agent re-declaring it explicitly
(`amplifier_app_cli/session_spawner.py::_filter_tools`, "explicit declarations always honored").
`session-analyst.md`'s `tools:` block declares filesystem/search/bash only. **It cannot delegate.**

`TestNoSelfDelegationNotice` pins both invariants, so the notice returns if either ever changes.

---

## Evidence

### (1) fail-before → pass-after, unit level

```
FAIL  TestScriptDiscovery::test_script_discovery_uses_find
FAIL  TestScriptDiscoveryResolvesRealLayouts::test_every_discovery_glob_matches_cache_layout
      Discovery glob '*/amplifier-foundation/scripts/amplifier-session.py' does not match the
      module-cache layout '/home/user/.amplifier/cache/amplifier-foundation-c909465861f9d6ce/...'
2 failed, 35 passed   →   43 passed
```

### (1) real-session check — a real delegation, both directions

Scratch `AMPLIFIER_HOME=/tmp/ezze-realsession/home`, scratch venv with `amplifier-app-cli @ c120a365`.
**`~/.amplifier/cache` was never edited** (verified after: it still carries 3 occurrences of the
broken form). The patch was applied to the *scratch* cache.

| | RELEASED agent (before) | PATCHED agent (after) |
|---|---|---|
| glob run | `*/amplifier-foundation/scripts/…` | `*/amplifier-foundation*/scripts/…` |
| resolved | nothing — timed out at 30s, scoped retries empty | `/var/tmp/wB-scratch/homeC/foundation/cache/skills/amplifier-foundation-c909465861f9d6ce/scripts/amplifier-session.py` |
| script ran | **no** — "neither info nor --help ever executed" | **yes** — `usage: amplifier-session [-h] {diagnose,repair,rewind,info,find}` |

The resolved path is a **hashed** `amplifier-foundation-<hash>` cache directory — precisely the
layout the released glob could not match.

Full capture: `/home/bkrabach/dev/openai-evals-team-ci/.amplifier/evaluation/treatment-validation/2026-09-06-ezze/`

### (2) round-trip through the REAL settings loader

```
fail-before: Loader returned {}, doc promised {'tool-bash': ..., 'provider-anthropic': ...}
pass-after:  12 passed
```

`test_flat_example_would_have_failed` pins the flat shape returning `{}` so the guard cannot rot
into a tautology.

### (3) ISSUE_HANDLING cost — FLAGGED, NOT RESTRUCTURED

Re-measured on this tree (@mention closure, ~4 chars/token):

| agent | files | tokens/spawn |
|---|---|---|
| `ecosystem-expert` | 11 | **~31,384** — of which `ISSUE_HANDLING.md` alone ~8,576 |
| `amplifier-dev-expert` (post-ux32) | 4 | **~4,810** |

≈ **6.5×**. The item quoted 8,561 for `amplifier-dev-expert`; ux32 has since made it a thin layer,
so the gap is *wider* than the item reported. Restructuring is the maintainer's call — untouched.

### (4) sizes

`context/bundle-design-awareness.md`: 51 lines → 3 (2,171 → 253 bytes, ~540 → ~63 tokens) off every
root prompt composing `behaviors/bundle-design.yaml`. Nothing lost — the lifecycle, all four
recipes, and their required context keys are already in `agents/bundle-design-expert.md:358-369`,
which is the context sink `behaviors/bundle-design.yaml`'s own comment says it should be.

### Suite (honest baseline comparison)

| tree | result |
|---|---|
| `origin/main` (`5bc2ed1`, clean worktree) | **1 failed**, 1798 passed, 1 skipped |
| this branch | **1 failed**, 1820 passed, 3 skipped |

Same single failure both sides: `tests/test_sources.py::TestFileSourceHandler::test_resolve_existing_file`
— pre-existing, named in the goal, **not absorbed**. +22 tests, zero new failures.

**Honest deviation on the known-failures list:** the goal names a *second* pre-existing failure,
`tests/test_grpc_adapter_main.py::TestVerifyModuleType::test_non_isinstance_object_with_mount_passes`.
It did **not** reproduce on `origin/main` on this host. I am not claiming it as pre-existing and I
am not claiming I fixed it — it simply did not fail here.

The 2 new skips are `TestSourcesOverrideExampleRoundTrip`, skipped **with a stated reason**
(see below), not silently.

---

## Decisions I made (no human was waited on)

1. **Canonical footer home = `context/shared/common-agent-base.md`.** The goal picked the plain
   `Generated with Amplifier` and said "make the other file reference the winner." `bundles/anchors/`
   is scoped out of this lane, so the canonical statement had to live on the foundation side; the
   anchors copy already uses the same plain string, so the repo is consistent either way.

2. **Fixed two footer restatements the item did not name** — `agents/git-ops.md` (×2) and
   `context/ISSUE_HANDLING.md` (×1). Deliverable (2) says "exactly one commit-footer form mandated
   **repo-wide**"; leaving three live restatements would not have satisfied it. The ISSUE_HANDLING
   edit is a footer block → pointer line only. **Its structure and token cost are untouched** — that
   remains the maintainer's decision per the scope-out.

3. **`experiments/` excluded from the repo-wide footer sweep.** It is a sandbox outside the live
   bundle composition; one emoji footer survives at
   `experiments/behavioral-anchor-amplifier-dev/context/amplifier-dev/dev-workflows.md:155`.
   Named here rather than silently swept.

4. **`amplifier-app-cli` was NOT added as a dev dependency.** Its own metadata declares
   `Requires-Dist: amplifier-foundation` — a dev-dep here is circular. So the round-trip test skips
   with that reason stated in the skip message, and CI's coverage of the same defect is the
   always-running `TestSourcesOverrideExampleShape`, which pins the loader's read path as a verbatim
   quote. The round-trip **was** executed here, in a scratch venv, and it is what produced the
   `Loader returned {}` fail-before above.

5. **The pass-after real session used a patched *scratch* cache, not a source override.**
   `sources.bundles.foundation` did not take: `foundation` is a WELL-KNOWN bundle pinned to its git
   URI, and `bundle add --name` refuses to alias it. Applying the lane diff to the scratch cache
   under `/tmp` was the honest way to run the real agent file. `~/.amplifier/cache` untouched.

6. **`behaviors/amplifier-dev.yaml`'s YAML comment** still says "Delegate to foundation-expert",
   which is the same dead-routing defect class as (3). It is a YAML comment — zero token cost, reader
   guidance only — and the item scoped (3) to the agent file. Left alone; flagged here.

---

## Spend

Authority: **$0** — arithmetic `0 runs x 0 arms x $0 / 1.00 = $0.00`, slack $0.00.
Pure content/test change; no DTU, no containers. The goal explicitly permits the one local
`amplifier run` for the (1) real-session check as a normal invocation, "record its cost anyway".

| item | cost |
|---|---|
| real-session run 1 (fail-before, root session) | $1.1124 |
| └ its `session-analyst` sub-session | $0.0396 |
| real-session run 2 (pass-after, root session) | $0.5705 |
| DTU / containers / API beyond the above | $0.00 |
| **total** | **$1.7225** |

**The cap never bound.** Nothing was dropped for budget; there is no NOT-POSSIBLE deliverable and
therefore no residue-vs-smallest-purchase question to answer. Two runs were needed rather than one
because the check is only meaningful as a *pair* — a pass-after with no fail-before proves nothing
about a glob that "compiles fine" either way.

**Finding against the goal's own authoring rule (branch-B-style, reported not absorbed):** the goal
states its cap as `0 runs x 0 arms x $0` while *also* requiring a real `amplifier run`. The
arithmetic therefore does not close on its own terms — a $0 authority cannot fund a run it mandates.
The goal resolves this by carving the run out as "a normal invocation", so this is a wording defect,
not a funding gap: the work was fully reachable. Noted so the next goal author states it as, e.g.,
`2 runs x 1 arm x ~$0.85 / 1.00 = $1.70`.

## Infrastructure

None registered — no DTU, no container, no Gitea, no VM. `infra_ledger.sh` not invoked, so nothing
to tear down. Scratch artifacts left on this host only (`/tmp/ezze-roundtrip-venv`,
`/tmp/ezze-realsession`, `/tmp/ezze-baseline-main` git worktree) — all under `/tmp`, all disposable.
