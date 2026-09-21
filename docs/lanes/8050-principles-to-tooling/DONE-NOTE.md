# DONE-NOTE — model_performance-8050

**Encode the awareness/description principles into the authoring docs, creation
skills, and validators.**

Terminal outcome: **A — RESOLVED**, with a finding the goal did not anticipate:
**this item is a duplicate of `model_performance-pwmy`, whose PR #373 merged to
`origin/main` as `5f0f04b` at 12:27 PT on 2026-09-07 — before this lane's first
tool call.** Both items quote the same owner directive verbatim, both were
created 2026-09-07, and `pwmy`'s deliverable list is 8050's deliverable list.

So this lane did not re-implement shipped work. It did three things instead:

1. **Independently verified** every deliverable against the merged code —
   re-running the fail-before/pass-after counts and both recipes' checks from a
   clean base rather than quoting `pwmy`'s DONE-NOTE back at itself.
2. **Closed the gaps that verification found** — three of them, all real, all
   in `pwmy`'s own scope, one of them a creation surface still teaching the
   defect the new validator warns about.
3. **Filed the follow-up 8050 required and nobody had** — the skills-bundle
   half of (c), which was sitting in an artifact directory with no queue item.

Spend against the **$0** authority: **$0**. No API measurement, no DTU, no
scratch session. Every number below came from executing recipe step bodies and
`git archive` on local checkouts.

---

## 1. The supersession, established rather than assumed

| | `model_performance-pwmy` | `model_performance-8050` |
|---|---|---|
| Owner directive | verbatim, 2026-09-07 | **the same text, verbatim** |
| (a) docs | ✔ | ✔ |
| (b) four validator checks + fail-before | ✔ | ✔ |
| (c) creation skills + skills-bundle scope boundary | ✔ | ✔ |
| (d) refresh entry point | ✔ | ✔ |
| Landed | **PR #373, MERGED `5f0f04b`** | this PR |

`git merge-base --is-ancestor 5f0f04b HEAD` → true.

**This lane's worktree launched 22 commits behind.** It was cut from
`origin/main` at `df8a266`; by first tool call `origin/main` was `cfd0e23`, and
`5f0f04b` (PR #373) sat between them. Had this lane trusted its base and worked
from `df8a266`, it would have re-written all four validator checks, all three
docs and the refresh recipe from scratch and opened a competing PR. The first
action taken here was `git fetch` + `git log HEAD..origin/main`, and it is the
only reason that did not happen. **This is `model_performance-napw` (stale lane
base → published false negative) recurring with a different blast radius**, and
it is the single most transferable finding in this note: *fetch before you
believe your own base.*

---

## 2. Independent verification of the merged work

Nothing here is quoted from `pwmy`'s note. Each row was re-run in this worktree
on 2026-09-07.

### 2.1 Fail-before / pass-after, reproduced

```
git worktree add --detach /tmp/foundation-pre-8050 4384805
ALIGNMENT_RECIPE_DIR=/tmp/foundation-pre-8050/recipes uv run pytest \
    tests/test_description_alignment_checks.py -q
```

| Recipes under test | Result |
|---|---|
| `origin/main` **4384805** (pre-#373) | **23 failed, 1 passed** |
| `HEAD` **cfd0e23** (post-#373) | **24 passed** |

Matches `pwmy`'s published counts exactly. The single test that passes at
4384805 is the one that should: a 599-char clean description is clean under
both the old token gate and the new char gate.

### 2.2 Both recipes' checks, re-run on foundation and anchors-amp-dev

Executed via `docs/lanes/8050-principles-to-tooling/run_checks.py`, which
imports the step-body extractor from `tests/test_description_alignment_checks.py`
and therefore runs **the recipes' own bodies**, never a re-implementation.

| Target | agents | skills | awareness | head cost | `validate-agents` structural |
|---|---|---|---|---|---|
| foundation `HEAD` | 24 checked, **0 err / 5 warn** (`agent_description_high`) | 3 checked, **0 err / 1 warn** | 7 files, **0 warn**, max coverage **0.111** | 19 bundles, **3 warn**, largest **26,368** | 24 agents, **0 err / 6 warn** |
| `bundles/anchors-amp-dev` | 1 checked, **0 err / 0 warn** | 0 (no SKILL.md) | 1 file, **0 warn** | 1 bundle, **0 warn**, **1,760** | 1 agent, **0 err / 0 warn** |

Every figure reproduces `pwmy` §5, including the 26,368 largest-head-cost
number and the 6th `validate-agents` warning (`NO_TOOLS_SECTION` on the
deliberate `examples/agents/file-responder.md` fixture, which four prior lanes
have each reported and correctly left alone).

Raw output: `evidence/checks-foundation-main.json`,
`evidence/checks-anchors-amp-dev.json`.

### 2.3 NEW — the checks agree with a hand sweep they never saw

Neither `pwmy` nor 8050 asked for this, and it is the strongest evidence in
either lane that the thresholds are calibrated rather than chosen.

`amplifier-bundle-android-tester` was swept **by hand** (lane `kp79`) *before*
Phase 2.82/2.84/2.86 existed. Exporting that repo at its pre- and post-sweep
commits with `git archive` (read-only; that repo was not written to) and
running all four checks over both:

| | agents | skills | head cost | verdict |
|---|---|---|---|---|
| pre-sweep `443e393` | **9 ERRORs** — `agent_description_excessive`, `example_block_present`, `commentary_present` | 0 err / 1 warn | **8,116** chars, **1 WARNING** | fails |
| post-sweep `863afa1` | **0 err / 0 warn** | **0 err / 0 warn** | **3,791** chars, **0 warnings** | clean |

The hand pass crossed the 4,000-char head-cost line from the wrong side to the
right one, on a threshold derived later from a *different* corpus (8 bundles,
none of them this one). A machine rule and an independent human judgement of
"short enough" landing on the same side is the calibration argument the
threshold could not make for itself.

Per-description, same pair, rendered `delegate`-catalog bytes (UTF-8 bytes of
the description as stored, plus the catalog line's own `  - <bundle>:<name>: `
framing and newline):

| agent | pre | post |
|---|---:|---:|
| `android-debugger` | 2,122 | 598 |
| `android-operator` | 2,271 | 594 |
| `android-visual-tester` | 1,746 | 600 |
| framing | 119 | 119 |
| **total** | **6,258** | **1,911** |

Both totals match the figures the item asserted, to the byte. That is now cited
in `description-authoring-principles.md` with the method and the two commits, so
the next reader can reproduce it instead of trusting it.

Raw output: `evidence/checks-android-tester-{pre,post}-sweep-*.json`.

---

## 3. The three gaps verification found, and closed

Each is a defect *inside* `pwmy`'s scope, found by checking its claims against
the merged files rather than reading its summary.

### G1 — two creation surfaces still teach the pattern Phase 2.84 warns about

`pwmy` corrected `agents/bundle-design-expert.md`, whose build checklist said
"Create awareness context — ~25-40 lines, domain exists, delegate to expert".
**It left the same instruction in two documents:**

* `docs/AGENT_AUTHORING.md` §"The Behavior + Agent Pattern" opened by telling
  authors to pair the agent with a behavior injecting a *"thin awareness
  pointer (~30 lines)"* that *"tells root sessions: This domain exists. Delegate
  to `my-bundle:my-expert`"* — and then, 18 lines later, said "EVEN BETTER: no
  always-on context at all". The wrong version was the headline; the right one
  was a parenthetical.
* `docs/BUNDLE_GUIDE.md` labelled the same file an **"Acceptable variant"** in a
  worked example — in the same document whose new §"Awareness: concept +
  trigger + pointer" says that file is exactly what
  `awareness_is_pointer_only` warns on.

This is worse than an unenforced convention. An author who followed the doc
earned a WARNING the doc told them to earn — the same shape as `personafy`
instructing a ~700–800-char cap against a 400-char enforced one, which `pwmy`
itself identified as the reason creation surfaces matter.

**Fixed.** The default behavior now ships *no* `context.include`; the agent's
own `meta.description` is named as the discovery surface; a `context.include`
is added only for something the catalog line cannot say, cross-referenced to
BUNDLE_GUIDE's section rather than restated (V1). The phrase survives in both
files only under a ❌ marker, and a test pins that.

### G2 — the principles doc contradicted its own enforced table

V5 marks the agent cap **Enforced** with a nine-repo corpus behind it. The
closing "Settled" section still read *"agent `meta.description` budgets in V5
remain provisional … WARN/ERROR calibration is still open"* — a second copy of
the rule, disagreeing with the first, in the file whose own V1 says a rule
stated twice "can drift from the first and force the model to arbitrate between
two versions of your own instructions".

**Fixed.** That paragraph now reads as history and points at V5 as the answer.

### G3 — two measured citations the item required were absent

The item named them explicitly; neither was in any of the three docs.

* **`g7h3`'s −13.57% $/task, 95% CI [−22.27%, −4.86%]**, all three
  pre-registered estimators excluding zero, from 98 paired end-to-end tasks on
  the daily driver. `pwmy`'s DONE-NOTE §3 states this "is cited in the
  head-cost WHY" of AGENT_AUTHORING; `grep -c 13.57` over all three docs
  returned **0**. Without it the WHY was argued entirely in bytes, and bytes
  are what people argue with.
* **android-tester's 6,258 → 1,911 catalog bytes** — the per-bundle
  granularity a bundle author actually works in. `pwmy` substituted dot-graph
  and `zc6t` figures, which are repo- and composition-level.

**Fixed**, both with their units and their provenance, and both verified here
rather than copied (§2.3).

---

## 4. What is pinned so it cannot silently return

`tests/test_description_docs_alignment.py` — 8 tests, deliberately narrow
string pins on the *contradiction* rather than the prose around it, so the docs
stay editable.

| Recipes/docs under test | Result |
|---|---|
| `origin/main` **cfd0e23** | **7 failed, 1 passed** |
| this branch | **8 passed** |

The one that passes at `cfd0e23` is the one that should:
`test_agent_authoring_points_at_the_canonical_awareness_section` — #373 already
added that cross-reference; what it did not do was remove the contradicting
guidance further down the same file.

---

## 5. Deliverables, per 8050

| Deliverable | State |
|---|---|
| (a) Docs updated, citing the measured numbers | **DONE** — shipped by #373; **two required citations were missing and are added here** (§3 G3), plus a self-contradiction removed (§3 G2) |
| (b) Four checks + fail-before tests, both recipes re-run on foundation AND anchors-amp-dev with results quoted | **DONE** — shipped by #373; **independently reproduced here**, §2.1–2.2, with one *added* real-repo demonstration §2.3 |
| (c) Creation skills emit compliant descriptions by default | **DONE** — foundation-owned surfaces shipped by #373; **two that still emitted the defect are fixed here** (§3 G1). Skills-bundle half is out of this lane's single-repo checkout — see below |
| (d) Refresh entry point, documented and demonstrated | **DONE** — `recipes/refresh-descriptions.yaml`, BUNDLE_GUIDE §"Refreshing descriptions", demonstrated clean-room against `kp79`'s hand sweep of browser-tester (#373) |
| Refresh path demonstrated on a Stage-1 repo vs the kp79 lane's output | **DONE** — browser-tester (#373); this lane adds an independent **android-tester** cross-check of the *checks* against the same lane's hand output (§2.3) |
| Out-of-scope work stated, not dropped | **DONE** — §6 |

---

## 6. Scope boundary, and the follow-up that was missing

`skillify`, `personafy`, `councilify` and `skills-assist/authoring-guide.md`
live in **`microsoft/amplifier-bundle-skills`**, which this lane's worktree does
not cover and its goal forbids checking out. `pwmy` shipped drop-in patches for
all four as an artifact
(`docs/lanes/pwmy-principles-to-tooling/patches/skills-bundle-description-alignment.md`),
because that repo was held by `smy5-patch-skills`.

**A patch in an artifact directory with no queue item is a patch that does not
get applied.** 8050 required a *linked follow-up* for exactly this case. The
queue was checked on 2026-09-07 — 31 open items, none tracking it — so this
lane filed:

> **`model_performance-jlo6`** — *Apply the shipped skills-bundle
> description-alignment patch (personafy / skillify / councilify /
> skills-assist) — the creation skills still emit over-cap descriptions.*
> Linked `follow-up-of` `pwmy` and `relates-to` `8050`. Carries the measured
> defect (personafy instructs ~700–800 chars against a 400-char enforced cap;
> that bundle measures 13 of 31 skills over the cap, 4 over the ERROR line) and
> `pwmy`'s already-measured fail-before/pass-after (skillify 453→353,
> personafy 714→391), so whoever picks it up does not re-derive any of it.

Nothing else in 8050 is out of scope.

---

## 7. Findings reported, not fixed

**F1 — a lane's worktree can launch behind `origin/main` far enough to hide a
merged duplicate of its own item.** This lane launched 22 commits behind, and
the commit that had already done its work sat in that gap. Distinct from
`model_performance-napw` only in blast radius: napw published a false negative,
this would have published a duplicate PR and a duplicate resolution. **A
`git fetch && git log HEAD..origin/main` before the first substantive action
should be in the goal template, not in each lane's judgement.**

**F2 — two items were opened for one owner directive**, 8050 and `pwmy`, with
byte-comparable descriptions. Both were claimable; both were claimed. The
`discovered-from` / `relates-to` edges that would have shown this exist and were
not used at creation time. Same family as `model_performance-40d8` (one item
fanned to multiple lanes) inverted: multiple items for one job.

**F3 — `pwmy`'s DONE-NOTE §3 over-claims one citation.** It states `g7h3`'s
$/task figure "is cited in the head-cost WHY"; it was not in the merged file.
Not a fabrication — the claim is about a doc edit that did not land — but it is
the reason this note verified rather than quoted, and the reason §2 re-runs
everything. Recorded so the next reader of that note knows which line to
distrust; the rest of it verified exactly.

**F4 — the `AWARENESS_INDEX.md` question is untouched here.**
`context/shared/AWARENESS_INDEX.md` exists and was not part of either item's
scope; whether it is itself subject to Phase 2.84's rules is a separate
question nobody has asked.

---

## 8. Verification

```
uv run pytest -q        2110 passed, 4 skipped, 1 warning in 25.07s
```

**Zero failures.** Worth stating plainly, because `pwmy`'s note recorded
`2087 passed, 3 skipped, 2 failed` and named both failures as pre-existing at
`4384805`. Both now pass at `cfd0e23`; the three commits merged after #373
(#375, #376, #377) close the gap. No test was skipped, xfailed or deselected to
get here.

Also green, and worth naming because it is the trap `pwmy` reported as F3:
`TestDiscoveryScopeParity` and the shipped-skill-count pin. This lane commits
one `.py` and four `.json` files under `docs/lanes/` and **no `SKILL.md`**, so
the repo's shipped-skill count stays 3 and its head-cost figures do not move.

**Census safety** (`GOAL.md`): no `amplifier` binary was run on this host at
any point in this lane, with or without a scratch `AMPLIFIER_HOME`. The
guard `grep -l /tmp/ ~/.local/share/uv/tools/amplifier/lib/python3.13/site-packages/*.pth`
was checked before the completion marker.

---

## 9. What remains open

1. **`model_performance-jlo6`** — apply the skills-bundle patch (§6).
2. **Foundation's own 5 over-cap agent descriptions** (761/721/669/647/631) stay
   WARNINGs. `pwmy` left them deliberately; re-sweeping them is a separate item
   and `validate-agents`' report step already produced rewrites with fidelity
   tables for all five.
3. **F1's goal-template change** — fetch-before-you-trust-your-base.
4. `pwmy`'s own open list (F1 `no_composable_surface` false positive, F2
   anchors-amp-dev README include order, F4 `validate-agents` on Windows, F5
   `publication/v1` step ordering, R1's never-fired threshold) is unchanged by
   this lane and still stands.
