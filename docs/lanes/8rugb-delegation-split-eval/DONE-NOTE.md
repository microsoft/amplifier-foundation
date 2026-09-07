# DONE-NOTE — lane `8rugb-delegation-split-eval`

Work item: `model_performance-8rug` (project `model_performance`), **PR-2** — the eval that was
recorded NOT-POSSIBLE at the $10 authority and is now funded at **$80**.

**Terminal outcome: (A) RESOLVED.** Every deliverable is **DONE**. Nothing is NOT-POSSIBLE at the
cap: the pre-registered design was bought in full, unshrunk, for **$69.18 of the $80 authority**.
The eval's *result* is a **NO-SHIP**, which is a result, not a shortfall.

**Landing stage:** the deliverable whose final state is "the verdict is in #369 and the PR reflects
it" is satisfied by **demonstrating and shipping** — the verdict is written into #369's body and
the PR is left **DRAFT** because the rule said NO-SHIP. Nothing was merged; the manager merges.

---

## Deliverables

| # | Deliverable (from the goal) | State |
|---|---|---|
| D1 | The eval run **exactly as pre-registered**: S3-class, foundation root, arms `main` vs branch, **n≥3 valid per arm per provider, BOTH providers**, DTUs with the branch pinned by source override | **DONE** — 14 launches, **14 valid**, n=4/4 opus + 3/3 terra |
| D2 | The decision rule applied **verbatim**, with SHIP / NO-SHIP / **UNEVALUABLE** stated explicitly | **DONE** — **NO-SHIP**; neither cell is UNEVALUABLE, and the pre-registered degeneracy test is quoted and shown not to trigger |
| D3 | **Delegation counts reported per run**, not just aggregated | **DONE** — `runs-table.md`, every launch, every count |
| D4 | Spend arithmetic to the cent, DTUs destroyed, **ledger 0 open rows verified** | **DONE** — §Spend; 4 DTUs destroyed and verified GONE; `awk -F'\t' '$4=="open"' infra.tsv \| wc -l` → **0** |
| D5 | The verdict written into **PR #369's body**, PR marked ready **only if** the rule says SHIP | **DONE** — verdict in the body; **left DRAFT** (rule said NO-SHIP); the body states plainly that A+C (`aac89ea`) are unaffected |
| D6 | **Do NOT merge #369** under any outcome | **HONORED** — nothing merged, by this lane, anywhere |

The verdict itself, with the evidence and the arithmetic, is **`VERDICT.md`**. The per-run table is
**`runs-table.md`**. Machine-readable: `ANALYSIS-prereg-n3.json` (the pre-registered set, frozen
before the extension was bought) and `ANALYSIS.json` (with the extension).

---

## The result in three lines

> **NO-SHIP.** The split does **not** stop the root delegating — across 14 runs both arms delegate
> at the same turn, to the same agent, with the same context parameters. What it changes is
> **fan-out width**: on the anthropic cell arm A sends **two parallel explorers**, arm B sends
> **one**, and the ±30 % count gate fires on that (ratio 0.667). Condition 1 (success LB ≥ −5 pp)
> also fails — but it fails on **interval width at n=3–4, not on any measured regression**, and it
> is arithmetically unsatisfiable below **n ≈ 73 per arm (~$765)**.

## Findings

### 1. The gate caught a fan-out-width change, not a suppressed delegation

Every anthropic delegation in every run of both arms is in **turn 1**, is `foundation:explorer`,
and carries `context_depth=none, context_scope=conversation`. Turns 2–5 delegate zero times in both
arms. Arm A: *"delegate two explorer agents in parallel to survey each file"* — two spawns sharing
one `parallel_group_id`. Arm B: *"delegating to a single explorer with very specific …"*. The
imperative survived the cut to 487 tokens; the granularity did not. **A count gate cannot express
that difference — and this is the first measurement in the program that shows the difference
exists.** On openai the split moves fan-out the *other* way (10.67 → 12.67, +18.8 %, inside the band).

### 2. Condition 1 was ~0 % likely to pass at the n it was priced for

`best-case LB = −(1 − wilson_lower(n,n))` ⇒ n=3: −56.15 pp · n=10: −27.75 · n=50: −7.13 ·
**n=73: −5.00**. The design was **12 runs at $73.44**; the condition needs **146 runs pooled
(~$765)** or **292 per-provider (~$1,530)** at the observed $5.24/run. A perfect treatment would
have failed it. Same family as §2a's vacuous Gate 3 and `1ru`'s 0.0 %-chance deliverable, in the
opposite direction. **Reported as failed, verbatim, not rescued** — the lesson is to price a rule
against its own power *before* freezing it.

### 3. Two shared-harness instrument defects (found, reported, NOT silently absorbed)

- **`scripted_driver.py`'s `session_continuity_ok` is a false negative by construction** — it greps
  each turn's stdout for `Session ID: <sid>`, but `amplifier run --resume` prints
  `Resuming session: <sid>`. **False for 14/14 runs of both arms** while every container's root
  session carried exactly **5 `prompt:complete` events**. Taken at face value it voids 100 % of
  runs of any lane using this driver — including, potentially, `h7n`'s.
- **Turn stdout capture returns empty on large turns** — `B-oai-01` turns 4 and 5 came back
  `out_len == 0` on the host while the container had executed both. Grading is nearly unaffected
  (90 of 100 points read the container filesystem; only `d_evidence`'s 10 use the transcript) but
  any transcript-based judge would silently score a truncated artifact.

### 4. Quality did not follow the delegation count down

Anthropic: arm B **4/4 pass** (90, 85, 90, 85) vs arm A **3/4** (80, 85, 75, 90). OpenAI: arm B
**$1.35/run cheaper**. Descriptive at n=3–4, offered as neither a gate nor a rescue argument.

### 5. `anchors` cannot see this treatment at all

`anchors` / `anchors-amp-dev` mount `tool-delegate` directly and never include
`foundation:behaviors/agents.yaml`. The standard eval container's root bundle is therefore blind to
the delegation-context split — which is why this eval had to run **foundation as ROOT**, and why no
existing S3 number could have answered the question.

### 6. Still true, and still breaking for external references

`context/agents/delegation-instructions.md` does not exist on #369's branch (it is
`delegation-depth.md`). In-repo references were updated by PR-1; **bundles outside this repo that
`@`-mention the old path would break if B ever merges.**

---

## Spend — to the cent

| item | amount |
|---|---|
| 14 S3 eval runs, measured `cost_usd` from `dw_measure` over each run's own session tree | **$69.1752** |
| 4 container warm-ups (`amplifier run "Say exactly: warmup-ok"`, haiku, ~26k-token prompt) | **not measured** — the per-run reset wipes sessions before each run; **≤ $0.15** estimated |
| **Eval total against the $80 authority** | **$69.18** (≤ $80) ✓ |

```
priced before spending (previous lane, anchors-root prices):   18 launches x blended $4.08 = $73.44
re-priced at OBSERVED foundation-root prices (REPRICING.md):   9 remaining x $4.32 / 0.67   = $73.43
ACTUAL:            14 launches x $4.9411 mean, validity 100%                                = $69.18
                                                                            authority       = $80.00
                                                                            residue         = $10.82
```

**Validity ran at 100 %, not the 67 % budgeted** — 14 launches, 14 valid, 0 discarded. That is why
the design completed under a cap sized for 18 launches even though the observed per-run price
($4.94 mean) was above the $4.08 blended figure it was priced at.

**Residue and the smallest useful purchase it could not buy: $10.82.** It could buy ~2 more runs,
which cannot change either condition — condition 1 needs **~$765**, and *settling* condition 2's
stability on the opus cell (so one run cannot move the ratio across the band, `2/n < 0.07`) needs
**n ≥ 29 per arm ≈ $244**. Both are far beyond the residue, so it is unspendable **for this
deliverable** and was not spent.

**Reported separately, and NOT netted against the eval authority: this lane's own driving session
cost $20.62** (81 LLM responses, measured from its own `events.jsonl`). The authority's own
arithmetic is `launches × per-run price`, which structurally cannot include the driving session —
the previous lane read it the same way ($10 "for B's eval only", $1.21 of session spend reported
beside it). **Flagged as a goal-authoring ambiguity, not absorbed silently:** if the manager
intends $80 to cover *everything*, this lane's all-in total is **$89.80** and exceeds it by $9.80.
The number is stated here so the manager can decide rather than discover.

## Infrastructure

4 DTUs created, registered at creation, claimed by this lane, **all 4 destroyed and verified**:

```
TEARDOWN: lane=8rugb-delegation-split-eval verified-gone=4 rows-flipped=4
  already-absent=0 failed=0 live-skipped=0 unknown-skipped=0 unverifiable=0 protected-untouched=0
$ awk -F'\t' '$4=="open"' /home/bkrabach/dev/hw-model-performance/infra.tsv | wc -l
0
```

`infra_ledger.sh sweep` was **never run** (it is the manager's batch-close verb; two other lanes'
DTUs — `steward-desk-test`, `wayfinder-scout-eval` — were live throughout and are untouched).

## Deviations and choices recorded

1. **The item was RESOLVED when this lane started, so it was `work_reopen`ed, not re-resolved.**
   `work_claim` refused (`already claimed by agent-spark-1-2772377`) because the record still
   carried the previous lane's holder. The stored resolution itself says *"STILL OPEN: B's eval"*,
   and the owner has since funded it — so the **work** is incomplete, which is exactly what
   `work_reopen` is for (an erratum would have been wrong: the record was right, the work was not
   finished). Recorded cost: `closed_at` was cleared, so the item re-lands on today's date and one
   item moves in the throughput roll-up. This is **not** goal branch C: the claim was not refused
   for an unreachable reason.
2. **Arm A is `a0decc6`, not today's `origin/main` (`aac89ea`)** — `a0decc6` is both the
   pre-registered baseline and the B branch's merge-base. `aac89ea` carries PR #368 (parts A+C),
   which B does not, so using it would have put A's 499-char system-prompt change inside the
   contrast. Stated in `PREREG-ADDENDUM.md` before running.
3. **Both cells at `medium` effort**, declared before running: opus-5 at xhigh passes S3 only 1/5
   (§2c), which would have filled both arms with effort-induced failures.
4. **Continuity read from the printed session ids + the root session's `prompt:complete` count**,
   not from the driver's own flag — see Finding 3. The deviation and its reason were written into
   `summarize_run.py` **before any score was read**.
5. **One declared extension beyond the pre-registered n** — `EXTENSION-DECLARED.md`, written before
   the two runs it bought, as a *falsification attempt* on the condition that decided the verdict.
   It partly succeeded (arm A's 4th opus run delegated **0**, not 2), which is reported rather than
   buried. The verdict is NO-SHIP at n=3 **and** at n=4; `ANALYSIS-prereg-n3.json` preserves the
   pre-registered computation untouched.
6. **No further runs bought after seeing the boundary.** §4 of `VERDICT.md` shows a single run
   could flip condition 2 on the anthropic cell; buying runs after seeing which side of a boundary
   a result landed on is the failure pre-registration exists to prevent.
7. **`invocation_guide.md` still says "the `anchors-amp-dev` bundle"** while the container runs
   foundation as ROOT. Left byte-identical rather than corrected: it is read identically by both
   arms, and editing the shared scenario is a larger deviation than the stale name.
8. **The app-cli was pinned** (`28588b9`) so all four containers run one CLI build; the ~20
   external bundles foundation's root includes are `@main` and were **not** pinnable at reasonable
   cost — they were fetched within one hour across all four containers and are symmetric across
   arms. Recorded as a limitation.

## Proposed diff for `ai-notes/00-what-we-know.md` (manager applies — this lane did not edit it)

> ### (o) The delegation-context split: the imperative survives a 92 % cut; the FAN-OUT does not **[8rugb]**
>
> `8rug`'s (B) split — `delegation-instructions.md` (6,276 tok) → `delegation-core.md` (**487 tok**)
> plus an agent-body depth file — was evaluated against its own pre-registered rule (*ship iff
> success LB ≥ −5 pp AND delegation count within ±30 % of main*) on **S3, foundation-as-ROOT**,
> arms `a0decc6` vs `3d676d6`, **14 launches / 14 valid / $69.18**. **VERDICT: NO-SHIP.**
>
> **The root still delegates.** Both arms delegate on 13 of 14 runs, at the same turn, to the same
> agent, with the same context parameters. **What changes is fan-out width:** anthropic arm A
> `[2,2,2,0]` vs arm B `[1,1,1,1]` (ratio **0.667**, outside the band) — arm A spawns *two parallel
> explorers*, arm B *one*, for the same survey. OpenAI moves the other way, `[11,12,9]` →
> `[9,12,17]` (ratio **1.188**, inside). Quality did not follow the count down (anthropic 4/4 pass
> vs 3/4; openai $1.35/run cheaper), at an n far too small to claim either.
>
> **The success condition was unsatisfiable at its own n.** `best-case LB = −(1 − wilson_lower(n,n))`
> ⇒ n=3 → **−56 pp**, n=73 → **−5.00 pp**. The design was 12 runs (**$73.44**); the condition needs
> **146 pooled (~$765)** or **292 per-provider (~$1,530)**. Third instance of this family after
> §2a's vacuous Gate 3 and `1ru` — **price a rule against its own power before freezing it.**
>
> **`anchors` cannot see this treatment**: it mounts `tool-delegate` directly and never includes
> `foundation:behaviors/agents.yaml`. Any foundation-behavior treatment needs a foundation-root
> container; the standard eval container is blind to it.
>
> **Harness defect, affects every lane using the S3 scripted driver:**
> `scripted_driver.py`'s `session_continuity_ok` greps each turn for `Session ID: <sid>` but
> `--resume` prints `Resuming session: <sid>` ⇒ **false for 14/14 runs of both arms** while every
> root session carried 5 `prompt:complete`. Read continuity from the printed ids + the
> `prompt:complete` count instead. Second defect: turn stdout capture can return **empty** on large
> turns (`B-oai-01` turns 4–5) while the container executed them.

## What remains open

- **Is narrower fan-out actually worse?** Arm B scored higher on anthropic and cost less on openai.
  Unanswered at n=3–4, and this eval was not designed to answer it.
- **The success comparison.** Needs ≥73 valid runs per arm (~$765 pooled). Unbought, priced.
- **Condition 2's stability on the opus cell.** One run can move the ratio across the band at n=4;
  n ≥ 29 per arm (~$244) would settle it. Unbought, priced.
- **The two harness defects** are reported, not fixed — they live in the shared evals repo, which
  this lane does not own (0rg hazard).
- **#369 must not merge as-is.** It also deletes `context/agents/delegation-instructions.md`;
  external bundles `@`-mentioning that path would break.

## Capture root

`/home/bkrabach/dev/openai-evals-team-ci/.amplifier/evaluation/treatment-validation/20260906-8rugb/`

```
profiles/profile-8rugb-{a,b}.yaml   pre_run.sh  post_run.sh  summarize_run.py  analyze.py
runs/<run-id>/{cell.json,driver_record.json,transcript.txt,turn{1..5}.out,
               scorecard.json,measure.json,summary.json,all-sessions/,run.log}
ANALYSIS-prereg-n3.json   ANALYSIS.json   EXTENSION-DECLARED.md   logs/
```
