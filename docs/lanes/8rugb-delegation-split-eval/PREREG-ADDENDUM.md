# PRE-REGISTRATION ADDENDUM — lane `8rugb`, execution parameters

**Written 2026-09-06 BEFORE any eval run was launched, and committed on its own ahead of any
result.** It does not restate, weaken, or re-derive the frozen design in
`docs/lanes/8rug-foundation-root-hygiene/B-eval-preregistration.md` (commit `07d51b9`) — that
document is the authority and is unchanged. This addendum fixes only the parameters the original
left to the executor, so that *how a number is produced* also provably predates the number.

The frozen rule, quoted, unchanged:

> **SHIP if — and only if — success LB ≥ −5 pp AND delegation count is within ±30 % of main.**

---

## 1. What each arm is, exactly

| arm | git ref | sha |
|---|---|---|
| **A (main)** | pre-registered as "`main` (`a0decc6`)" | `a0decc61f41ed78a92db6d5b70a71e15722a5624` |
| **B (treatment)** | `lane/8rug-foundation-root-hygiene-b` (PR #369) | `3d676d68d0e45620330c24ae0142de86efa39234` |

`a0decc6` is used rather than today's `origin/main` (`aac89ea`) for one reason, stated before
running: **`a0decc6` is the merge-base of the B branch.** `aac89ea` merged PR #368 (parts A + C),
which the B branch does not contain. Comparing B against `aac89ea` would put A's 499-char
system-prompt change inside the contrast and measure two treatments as one. `a0decc6` is both the
pre-registered baseline and the only ref that isolates B.

**Arm purity is verified per container, not assumed** (`mikb`: `amplifier update` refreshes the
bundle cache while `site-packages` stays newer — a split state that silently produces a false arm).
Each container is checked, before its first run, for:

- the cached foundation bundle's `context/agents/` listing —
  arm A must contain `delegation-instructions.md` and no `delegation-core.md`;
  arm B must contain `delegation-core.md` + `delegation-depth.md` and no `delegation-instructions.md`;
- the composed root system-prompt length, which must differ between arms in the expected direction.

A container failing its arm check is destroyed, not repaired.

## 2. Bundle, scenario, and cell

- **Bundle: foundation as ROOT** (`bundle.active: foundation`, `bundle.added.foundation:
  git+…/amplifier-foundation@<arm sha>`, `bundle.app: []`). This is required, not stylistic:
  `anchors` / `anchors-amp-dev` mount `tool-delegate` directly and never include
  `foundation:behaviors/agents.yaml`, so the standard eval container's root bundle **cannot see the
  treatment at all**.
- **Scenario: the program's existing S3, byte-unchanged** (`scenarios/s3`), driven by the existing
  deterministic `scripted_driver.py` (5 turns, `amplifier run` + `--resume`), graded by the existing
  `grader.py`. No new scenario, no edited rubric, no edited turns. `invocation_guide.md` still says
  "the `anchors-amp-dev` bundle"; it is left byte-identical rather than corrected, because it is
  read identically by both arms and editing the scenario is a larger deviation than the stale name.
- **Cells: both providers at `medium` effort** — anthropic `claude-opus-5 @ medium`, openai
  `gpt-5.6-terra @ medium`. Reasons, all pre-existing and none result-dependent: `00-what-we-know`
  §2c records the effort dial pointing **down** for both families; the S3 Pareto cells are
  `terra-medium` and `sonnet5-medium`; and **opus-5 at xhigh passes S3 only 1/5 versus 5/5 at
  medium**, so running xhigh would fill both arms with effort-induced failures and confound the
  success comparison. `medium` is also the price point the goal's own arithmetic quotes ($3.53).
- Everything else in the container is the shared `_harness/eval_container.yaml` unchanged, including
  `routing.matrix: openai` (the sub-agent routing confounder of §2c is symmetric across arms), with
  the app-cli pinned to `28588b9` so all four containers run one CLI build.

## 3. The two measured quantities, operationalised

**Success (primary, condition 1).** A run's success is the existing S3 grader's own pass rule,
unchanged: `total ≥ 75 AND b_constraint_retention ≥ 20 AND c_plan_revision_handling ≥ 10`
(`scenarios/s3/grader.yaml`). Success rate per arm is `passes / valid runs`, pooled across
providers for the headline and also reported per provider.

**"success LB ≥ −5 pp"** is read as: the lower bound of a 95 % confidence interval on the
**difference** `success(B) − success(A)`, in percentage points, must be at least −5. The interval
used is the Newcombe/Wilson score interval for a difference of two independent proportions — chosen
now, before data, because it is the standard small-n choice and it does not degenerate at 0 % or
100 % the way a normal-approximation Wald interval does. Both the point estimate and the LB are
reported.

**Delegation count (primary, condition 2).** `root_delegate_calls` from the program's existing
`probes/dw_measure.py`: the number of `delegate:agent_spawned` events in the **root** session's
`events.jsonl`, for the run's own session tree. Not sub-agent totals, not tool-call text matches.
This is a pre-existing program instrument, used here unmodified.

**"within ±30 % of main"** is read as: `mean(delegation count, arm B) / mean(delegation count,
arm A) ∈ [0.70, 1.30]`, per provider, with **every run's count listed individually** so a reader can
see degeneracy rather than infer it from a mean.

**The UNEVALUABLE rule, restated before any number exists.** If arm A's delegation counts are
degenerate — concretely: **median 0**, or fewer than 2 of 3 valid arm-A runs at that provider
recording ≥1 delegation — the ±30 % condition has no denominator worth dividing by and the verdict
for that provider is **UNEVALUABLE**, not a pass and not a substitute gate. This is `otr`'s recorded
failure mode (`[0,0,1,0,0]` across n=5) and it is written here, before spending, exactly so it
cannot be argued afterwards. If either provider is UNEVALUABLE, the overall verdict is UNEVALUABLE
and PR #369 stays a draft.

## 4. Validity — what makes a run countable

A launched run counts toward `n` only if **all** hold. Pre-registered so that discarding a run is
never a judgement call made after seeing its score:

1. all 5 turns completed (`driver_record.json`: `turns_done == 5`, no `TIMEOUT` marker);
2. session continuity held (`session_continuity_ok == true`) — one session id across all 5 turns;
3. the grader produced a scorecard (`scorecard.json` parses and carries `total`);
4. the run's root session was found and `dw_measure` produced a non-empty tree.

A launch failing any of these is **invalid**, is reported with its reason and its cost, and is
replaced by a relaunch. Invalid runs are never silently dropped: every launch appears in the
run table.

## 5. Spend discipline

Authority **$80**. The design was priced by the previous lane at **$73.44** (18 launches at 67 %
validity: 9 × $3.53 + 9 × $4.63). That price was observed on **anchors-amp-dev**-root S3 runs;
this eval runs **foundation**-root, whose system prompt is ~2.4× larger (130,055 vs the anchors
head), so **the per-run price is expected to be higher and must be re-measured before the bulk
spend.** The first wave is therefore one run per cell (4 launches); the arithmetic is then restated
at the observed price against the remaining authority, in the DONE-NOTE, **before** waves 2 and 3.

If the restated arithmetic does not close at $80, the shortfall is reported with its arithmetic.
**The design is not shrunk** — no arm dropped, no provider dropped, no n reduced and relabelled.
