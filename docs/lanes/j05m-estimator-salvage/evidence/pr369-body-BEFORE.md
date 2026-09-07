> ## ⛔ STILL HELD — DO NOT MERGE, DO NOT MARK READY
>
> **UPDATE 2026-09-07 — the eval has now been FUNDED ($80) and RUN IN FULL. The verdict is NO-SHIP.**
>
> 14 launches · 14 valid (validity 100 %) · **$69.18 of $80**. The rule frozen in `07d51b9` before
> any file in this split was edited, applied verbatim — *ship iff success LB ≥ −5 pp AND delegation
> count within ±30 % of main* — **fails**:
>
> | condition | anthropic (opus-5 @ medium) | openai (terra @ medium) |
> |---|---|---|
> | success LB ≥ −5 pp | LB **−28.1** (point **+25.0**) | LB **−79.2** (point **−33.3**) |
> | delegation within ±30 % | `[2,2,2,0]` → `[1,1,1,1]`, ratio **0.667** ✗ | `[11,12,9]` → `[9,12,17]`, ratio **1.188** ✓ |
>
> Neither cell is UNEVALUABLE under the pre-registered degeneracy test.
>
> **What the gate caught is NOT a suppressed delegation.** On the anthropic cell every delegation in
> every run of both arms is in **turn 1**, is `foundation:explorer`, and carries
> `context_depth=none, context_scope=conversation`; turns 2–5 delegate zero times in both arms. The
> difference is **fan-out width on that one turn** — arm A: *"delegate **two explorer agents in
> parallel** to survey each file"* (two spawns, one `parallel_group_id`); arm B: *"delegating to a
> **single explorer** with very specific …"*. **The imperative survived the cut from 6,276 tokens to
> 487. The granularity did not.** On openai the split moves fan-out the other way (+18.8 %, inside
> the band). Quality did not follow the count down: anthropic arm B **4/4 pass** vs arm A **3/4**;
> openai arm B **$1.35/run cheaper** (descriptive at n=3–4, not a rescue argument).
>
> **Condition 1 could not have passed at the n it was priced for.**
> `best-case LB = −(1 − wilson_lower(n,n))` ⇒ n=3 → **−56.15 pp**, n=50 → −7.13, **n=73 → −5.00**.
> This design is 12 runs ($73.44); the condition needs **146 pooled (~$765)** or **292 per-provider
> (~$1,530)**. A perfect treatment would have failed it. Reported as failed, verbatim, not rescued.
>
> Arms **A = `a0decc6`** (this branch's merge-base — not today's main, which carries #368) vs
> **B = `3d676d6`**, pinned by source override in four DTUs, **arm purity verified per container**:
> root prompt **123,064 → 99,699 chars (−23,365, −19.0 %)**; terra input tokens **42,562 → 37,701
> (−4,861, −11.4 %)**. **foundation as ROOT** was required — `anchors`/`anchors-amp-dev` mount
> `tool-delegate` directly and never include `foundation:behaviors/agents.yaml`, so the standard
> eval container is blind to this treatment.
>
> Full evidence, every launch and every per-run delegation count: **PR #370** →
> `docs/lanes/8rugb-delegation-split-eval/` (`VERDICT.md`, `runs-table.md`, `PREREG-ADDENDUM.md`,
> `REPRICING.md`, `ANALYSIS-prereg-n3.json`). 4 DTUs destroyed, 0 open ledger rows.
>
> **PR 1 (#368, A + C — merged as `aac89ea`) is independent of this and is unaffected.**
> This branch also deletes `context/agents/delegation-instructions.md`; bundles outside this repo
> that `@`-mention the old path would break if it ever merges.

---

<details>
<summary>Original body (written when the eval was unfunded) — kept verbatim</summary>

> ## ⛔ HELD — DO NOT MERGE, DO NOT MARK READY
>
> This is PR 2 of 2 for `model_performance-8rug`. Its pre-registered eval **could not be bought at the $10 authority** — priced *before* spending, arithmetic below. The **primary metric is unmeasured**, so the treatment is not shippable on this branch's evidence. The branch and the finding stand.
>
> **PR 1 (#368, A + C) is independent of this and is unaffected.**

## The defect

`behaviors/agents.yaml` and `behaviors/tasks.yaml` each `context.include`d `delegation-instructions.md` + `multi-agent-patterns.md` — **6,276 tokens (`len//4`) / 5,402 (`o200k_base`)**, measured on disk — into the root system prompt of **every foundation-root session**.

**The validator reported 1,000 and graded it a WARNING.**

`recipes/validate-bundle-repo.yaml` behavior-hygiene Rule 4 resolved a `context.include` two ways: a leading `@` was charged a flat 500 tokens; anything else was tried as a relative path. `foundation:context/agents/delegation-instructions.md` matches **neither** — no leading `@`, and neither `behaviors/foundation:context/...` nor `<repo>/foundation:context/...` exists — so it fell through to the 500-token default.

Two includes × 500 = **exactly 1000**, against a `> 1000` ERROR gate. **6,276 real tokens reported as 1000 and graded WARNING — one token below the ERROR that was true.** A 6.28× understatement that landed precisely on its own boundary.

## Fail-before / pass-after

```
### MAIN a0decc6 (fail-before)
  behaviors/agents.yaml: includes=2
      OLD estimator:  1000 tokens -> WARNING
      NEW estimator:  6276 tokens -> ERROR
  behaviors/tasks.yaml: includes=2
      OLD estimator:  1000 tokens -> WARNING
      NEW estimator:  6276 tokens -> ERROR

### THIS BRANCH (pass-after)
  behaviors/agents.yaml: includes=1
      OLD estimator:   500 tokens -> ok
      NEW estimator:   487 tokens -> ok
  behaviors/tasks.yaml: includes=1
      OLD estimator:   500 tokens -> ok
      NEW estimator:   487 tokens -> ok
```

The honest fail-before requires the *fixed* estimator — same shape as `aaa5c47`, which made (A)'s fail-before trustworthy.

## The estimator repair (`validate-bundle-repo` v3.14.0)

Strips an optional `@` and, for a `<namespace>:<path>` ref, also tries the path half against the behavior directory and the repo root. When the file is in this repo, **read it**. When it genuinely is not, keep the 500 fallback — but record the include in `context_unresolved_includes` and set `context_tokens_is_estimate`. An unknown folded silently into a number that reads as measured is the defect; the fallback itself is fine as long as it is visible.

## The split

| file | role | `len//4` | o200k |
|---|---|---:|---:|
| `context/agents/delegation-core.md` **(new)** | loaded by the behavior | **487** | **464** |
| `context/agents/delegation-depth.md` (renamed) | agent bodies only | 3,196 | 2,722 |
| `context/agents/multi-agent-patterns.md` | agent bodies only | 2,052 | 1,774 |

**Core** = the delegation imperative, the immediate triggers, basic `delegate` usage, the two context parameters — the root session *is* the delegator and needs these. **Depth** = session resumption, wave discipline, reading a structured return, scrutinising an agent's "N/A", large session-file handling, and the context-sink pattern itself — which is precisely the material that pattern says to defer.

### Which agents get depth, and why

| Agent | Why |
|---|---|
| `foundation-expert` | The **only** foundation agent declaring `tool-delegate`. The behavior sets `exclude_tools: [tool-delegate]`, so no other spawned agent can delegate at all — it is the only sub-agent that ever fans out. Gets `delegation-depth.md` **and** `multi-agent-patterns.md`. |
| `session-analyst` | The **only** agent that resumes sessions by `session_id` and reads large session files — the two depth sections written for exactly that. Gets `delegation-depth.md`. |

`test_the_only_agent_declaring_tool_delegate_is_the_one_we_routed_depth_to` pins the "only one" claim, so a future agent gaining `tool-delegate` fails a test rather than silently losing the routing.

### `tasks.yaml` — references the same core, does not drop context

`tool-task` **is** a delegation-shaped tool (it spawns sub-agents), so the imperative and the triggers apply to it; and pointing both behaviors at one file makes drift between them impossible. It gets **no depth**: `tool-task` exposes neither `context_depth`/`context_scope` nor session resumption.

## Real-session effect — foundation as ROOT

Project-scope `.amplifier/settings.yaml` source override. **`~/.amplifier/cache` was not edited.**

```
### BEFORE (main a0decc6)  session 5dfe9c07-d95d-41a8-815b-962c0ce5a0dd
    raw.system[0].text chars = 130,055
      PRESENT  core: 'You are an ORCHESTRATOR, not a worker.'
      PRESENT  depth: '## Wave Discipline'
      PRESENT  depth: '## The Context Sink Pattern'
      PRESENT  depth: 'Reading a Structured Agent Return'
      PRESENT  depth: 'Multi-Agent Patterns'
### AFTER  (this branch)   session eb36571b-b9c4-4667-a833-6990fb349e51
    raw.system[0].text chars = 106,655
      PRESENT  core: 'You are an ORCHESTRATOR, not a worker.'
      ABSENT   depth: '## Wave Discipline'
      ABSENT   depth: '## The Context Sink Pattern'
      ABSENT   depth: 'Reading a Structured Agent Return'
      ABSENT   depth: 'Multi-Agent Patterns'

Delta: -23,400 chars.  Provider-reported input: 52,741 -> 47,014 tokens (-5,727)
```

Depth stays reachable:

```
agents/foundation-expert.md   RESOLVES @foundation:context/agents/delegation-depth.md      (13,378 B)
                              RESOLVES @foundation:context/agents/multi-agent-patterns.md  ( 8,272 B)
agents/session-analyst.md     RESOLVES @foundation:context/agents/delegation-depth.md      (13,378 B)
```

## Why this is HELD — the eval could not be bought

Pre-registered **before any file was edited**, in its own commit `07d51b9`:

> **SHIP if — and only if — success LB ≥ −5 pp AND delegation count within ±30 % of main.**

Design: S3-class scenarios, foundation root, arms `main` vs branch, **n≥3 valid runs per arm per provider, both providers** (anthropic opus-5 root; openai gpt-5.6-terra root), in DTUs. Primary = task success + delegation-count sanity; secondary = root-prompt tokens and $/task.

Priced against the **$10** authority using this program's own observed rates and its **67 % observed validity rate**:

```
Pre-registered design = 3 runs × 2 arms × 2 providers = 12 VALID runs

launches needed          = 12 / 0.67            = 18 launches (9 anthropic, 9 terra)
anthropic @ opus-5 $3.53 = 9 × $3.53            = $31.77
openai    @ terra  $4.63 = 9 × $4.63            = $41.67
                                          TOTAL = $73.44   vs $10 authority

At 100% validity (12 launches):  6×$3.53 + 6×$4.63 = $48.96   vs $10
At the cheaper sonnet figure:    6×$2.29 + 6×$4.63 = $41.52   vs $10
```

**The arithmetic does not close, by 4×–7×.** At the cheapest observed rate ($2.62/run) $10 buys **3 launches ⇒ ~2 valid runs** — n=1 per arm on **one** provider. n=1 cannot produce a lower bound on success, so it cannot evaluate the rule's first condition, and one delegation count per arm cannot support a ±30 % comparison: a **0 % chance** of satisfying the pre-registered rule. Spending it would be the exact failure mode the goal names against lane `1ru`.

**$0 was spent on the eval.** The authority that would close it is **$73.44** (**$48.96** at perfect validity).

**−5,727 root-prompt tokens is the SECONDARY metric.** The primary one is unmeasured, and the split could plausibly fail in either direction — suppressing legitimate delegation, or removing the wave discipline that told the root to batch. That is why the gate is two-sided, and why this stays a draft.

**Recorded in advance so it cannot read as an excuse later:** `otr`'s pre-registered gate came back UNEVALUABLE because delegation counts at its cell were `[0,0,1,0,0]`. If arm-A S3 runs here median 0 delegations, the ±30 % condition is likewise unevaluable, and the honest report is "unevaluable" — not a substitute gate invented after the fact.

## ⚠ Breaking for external references

`context/agents/delegation-instructions.md` **no longer exists** — it is `delegation-depth.md`. Any bundle outside this repo that `@`-mentions the old path will stop resolving. All in-repo references were updated.

## Tests

`tests/test_behavior_context_budget.py` — 33 tests, **12 of which fail on `main`**. They measure the real files from disk, so the budget cannot be satisfied by an estimator's blind spot.

```
2,064 passed, 3 skipped, 2 failed in 21.63s
```

Both failures are pre-existing and reproduce on `a0decc6`:
- `tests/test_sources.py::TestFileSourceHandler::test_resolve_existing_file`
- `tests/test_grpc_adapter_main.py::TestVerifyModuleType::test_non_isinstance_object_with_mount_passes`

## Spend

**$0 on the eval** (not bought — see above). **$0.20** total on three `amplifier run "hi"` `haiku` sessions for the real-session measurements. **No DTUs created; 0 ledger rows opened.**

---
Generated with Amplifier

Co-Authored-By: Amplifier <240397093+microsoft-amplifier@users.noreply.github.com>


</details>

