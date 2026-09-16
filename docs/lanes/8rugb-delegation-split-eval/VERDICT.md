# (B) delegation-context split — EVAL VERDICT: **NO-SHIP**

The frozen rule, quoted from `docs/lanes/8rug-foundation-root-hygiene/B-eval-preregistration.md`
(commit `07d51b9`, written before any file in the split was edited) and applied **verbatim**:

> **SHIP if — and only if — success LB ≥ −5 pp AND delegation count is within ±30 % of main.**

**Result: NO-SHIP.** Both conditions fail, for very different reasons, and the difference matters:

| condition | anthropic (opus-5) | openai (terra) | verdict |
|---|---|---|---|
| **1 · success LB ≥ −5 pp** | LB **−28.1 pp** (point estimate **+25.0**) | LB **−79.2 pp** (point estimate **−33.3**) | **FAIL — but see §3: unsatisfiable at any n this program can buy** |
| **2 · delegation within ±30 %** | ratio **0.667** (mean 1.5 → 1.0) | ratio **1.188** (mean 10.67 → 12.67) | **FAIL on anthropic**, PASS on openai |

**Neither cell is UNEVALUABLE** under the pre-registered degeneracy rule (arm-A delegation median
2 on anthropic and 11 on openai; 3/4 and 3/3 arm-A runs recorded ≥1 delegation). `otr`'s failure
mode did not recur — but the anthropic cell came close, and §4 says exactly how close.

**PR #369 stays a DRAFT and is not marked ready.** A and C (merged as `aac89ea`) are unaffected —
they are a different branch, a different change, and were never gated on this eval.

---

## 1. What was actually run

**14 launches · 14 valid · validity 100 % · $69.18 measured**, against a $73.44 estimate that
assumed 67 % validity. Full per-run detail in `runs-table.md`; capture root
`/.amplifier/evaluation/treatment-validation/20260906-8rugb/`.

- **Arms** — A = foundation `a0decc6` (the pre-registered baseline, and the B branch's merge-base),
  B = `lane/8rug-foundation-root-hygiene-b` `3d676d6`. Pinned by source override, **arm purity
  verified per container** before any run: arm A's composed root prompt is **123,064 chars** and
  contains `# Agent Delegation Instructions`; arm B's is **99,699 chars** and contains the
  depth-file reference instead. Identical to the byte across all 7 runs of each arm.
- **Bundle** — **foundation as ROOT**, which is not a stylistic choice: `anchors` /
  `anchors-amp-dev` mount `tool-delegate` directly and never include
  `foundation:behaviors/agents.yaml`, so the standard eval container's root bundle **cannot see
  this treatment at all**.
- **Scenario** — the program's existing S3, byte-unchanged, driven by the existing
  `scripted_driver.py`, graded by the existing `grader.py` and its own pass rule
  (`total ≥ 75 AND b_constraints ≥ 20 AND c_revision ≥ 10`).
- **Cells** — `claude-opus-5 @ medium` and `gpt-5.6-terra @ medium`, chosen in the addendum
  before running (opus-5 at xhigh passes S3 only 1/5, which would have filled both arms with
  effort-induced failures).

## 2. Condition 2 — the one that carries real signal

Delegation counts, **per run**, because degenerate counts are the known failure mode here and a
mean would hide them (`root_delegate_calls` = `delegate:agent_spawned` events in the root session):

| cell | arm A (main) | arm B (split) | mean A → B | ratio |
|---|---|---|---|---|
| **opus-5** | `2, 2, 2, 0` | `1, 1, 1, 1` | 1.50 → 1.00 | **0.667** ✗ |
| **terra** | `11, 12, 9` | `9, 12, 17` | 10.67 → 12.67 | **1.188** ✓ |

At the pre-registered n=3 the anthropic ratio was **0.50** (`2,2,2` vs `1,1,1`). The 4th pair was
bought as a declared falsification attempt (`EXTENSION-DECLARED.md`, written before those two runs)
and it **partly falsified the clean-halving reading**: arm A's 4th run delegated **0** times.
The verdict is unchanged at n=3 and at n=4; the *story* is not.

**The mechanism, and it is not "delegation was suppressed".** On the anthropic cell every
delegation in every run of both arms happens in **turn 1** (the survey turn) and is
`foundation:explorer` with `context_depth=none, context_scope=conversation`. Turns 2–5 delegate
zero times in both arms. The difference is **fan-out width on that one turn**:

- arm A: *"delegate **two explorer agents in parallel** to survey each file"* — two spawns sharing
  one `parallel_group_id`, i.e. correctly batched into one turn;
- arm B: *"delegating to a **single explorer** with very specific …"* — one spawn, same turn.

So the delegation *decision* survives the split intact — the root still refuses to read the
reference files itself — while the *granularity* narrows from one-explorer-per-file to
one-explorer-for-both. On openai the split moves fan-out the other way (+18.8 %), still inside the
band. **A count-based gate cannot tell those apart, and it fired.** Applied verbatim, that is
NO-SHIP; the finding is that the thing it caught is a fan-out-width change, not a lost delegation.

**Quality did not follow the count down.** On anthropic, arm B scored **4/4 pass (90, 85, 90, 85)**
against arm A's **3/4 (80, 85, 75, 90)** — fewer, wider delegations and *better* scores. On openai,
arm B was **$1.35/run cheaper** ($5.28 vs $6.63). Neither observation is a gate and neither is
offered as a rescue argument; both are recorded because they are what the runs show.

## 3. Condition 1 was unsatisfiable at the n it was priced for — a defect in the frozen rule

The pooled point estimate is **0.0 pp** (arm A 6/7, arm B 6/7). The lower bound is **−38.8 pp**,
and it fails. But it fails **because of interval width, not because of a measured regression**, and
the arithmetic says the rule could never have passed:

```
best case for the LB = both arms pass 100% of runs.  Newcombe LB then = -(1 - wilson_lower(n,n))
    n =   3 / arm  ->  LB = -56.15 pp
    n =  10 / arm  ->  LB = -27.75 pp
    n =  50 / arm  ->  LB =  -7.13 pp
    n =  73 / arm  ->  LB =  -5.00 pp   <- first n that clears "LB >= -5 pp"
```

**≥73 valid runs per arm.** Pooled across providers that is 146 runs ≈ **$765** at the observed
blended $5.24/run; evaluated per provider as the design implies, 292 runs ≈ **$1,530**. The
pre-registered design was **n ≥ 3 per arm per provider — 12 runs, priced at $73.44**, i.e. between
**10× and 21× too small for its own first condition.** A perfect treatment would have failed this
gate; so would a catastrophic one.

This is the same class of defect this program keeps catching, in a new direction: §2a's Gate 3 was
**vacuous** (it passed on the runs that defined the defect); `1ru`'s deliverable had a 0.0 % chance
of landing at its authority. Condition 1 here has a ~0 % chance of **passing** at its own n,
regardless of the treatment. **It is reported as failed, verbatim, and not rescued** — but a future
rule of this shape should be priced against its own power before it is frozen.

## 4. The honest caveat on condition 2 — and why no more runs were bought

The anthropic cell decides the verdict on counts of **0–2 delegations per run**. With arm B pinned
at 1.0, the band `[0.70, 1.30]` requires `mean_A ∈ [0.77, 1.43]`; the observed `mean_A` is **1.50**.
One run can move `mean_A` by `2/n` — at n=4 that is 0.5, far more than the 0.07 that separates the
observation from the band edge. **A single additional run could flip this condition**, and that is
precisely why none was bought: the pre-registered n is satisfied, the rule is frozen, and buying
runs after seeing which side of a boundary the result landed on is the failure pre-registration
exists to prevent.

For the record, the purchase that would actually *settle* it: `2/n < 0.07` ⇒ **n ≥ 29 per arm** on
the opus cell alone ≈ 58 runs ≈ **$244**. The residue was $10.82.

The cell is **not** degenerate by the pre-registered test (median 2, 3/4 runs with ≥1 delegation),
so it is reported as an evaluated FAIL with this caveat attached — not retro-labelled UNEVALUABLE,
which would be a substitute gate invented after the fact.

## 5. Secondary metrics (known before the eval; confirmed inside it)

| metric | arm A | arm B | delta |
|---|---|---|---|
| composed root system prompt | **123,064 chars** | **99,699 chars** | **−23,365 (−19.0 %)** |
| provider-reported input tokens, first full-context request (terra, n=3 each) | 42,701 / 42,700 / 42,286 | 37,704 / 37,700 / 37,700 | **−4,861 (−11.4 %)** |
| $/run, opus-5 (n=4) | $4.03 | $4.33 | +$0.30 |
| $/run, terra (n=3) | $6.63 | $5.28 | **−$1.35** |
| wall/run, opus-5 | 620 s | 711 s | +91 s |
| wall/run, terra | 1,247 s | 1,096 s | −151 s |

The anthropic token figures are not quoted: caching makes the first `input_tokens` unstable there.
Cost and wall deltas are **descriptive at n=3–4** — the program's own guidance is that pass/fail
counts are the robust signal and cost means are noisy — and no cost claim is made from them.

## 6. Two instrument defects found in the shared harness (reported, not silently absorbed)

1. **`scripted_driver.py`'s `session_continuity_ok` is a false negative by construction.** It
   re-greps every turn's stdout for `Session ID: <sid>`, but `amplifier run --resume` prints
   `Resuming session: <sid>`. The flag was **false for all 14 runs of both arms** while the
   containers' own root sessions each carried exactly **5 `prompt:complete` events** — i.e.
   continuity actually held every time. Taken at face value this flag voids 100 % of runs. This
   lane read continuity from the printed ids plus the root session's `prompt:complete` count
   instead, and recorded the deviation before reading any score.
2. **Turn stdout capture can come back empty on large turns.** `B-oai-01` turns 4 and 5 have
   `out_len == 0` on the host while the container recorded both turns. Grading is barely affected
   (90 of 100 points are read from the container filesystem, not the transcript; only
   `d_evidence`'s 10 points use it) but the transcript is incomplete for those turns, and any
   future transcript-based judge would silently score a truncated artifact.

Both are proposed as diffs for the manager in `DONE-NOTE.md`; neither shared file was edited by
this lane.

## 7. What this does and does not settle

- **Settled:** the split does **not** stop the root delegating. Across 14 runs, both arms delegate
  on every run except one, at the same turn, to the same agent, with the same context parameters.
  The 487-token core carries the imperative.
- **Settled:** the split changes **fan-out width**, and on the anthropic cell that change is large
  enough in ratio terms to fail a ±30 % count gate.
- **Not settled:** whether narrower fan-out is *worse*. Arm B scored higher on anthropic and cost
  less on openai — at an n far too small to claim either.
- **Not settled, and not buyable at this authority:** the success comparison. It needs ≥73 runs per
  arm (~$765 pooled).
