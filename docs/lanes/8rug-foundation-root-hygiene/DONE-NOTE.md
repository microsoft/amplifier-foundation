# DONE-NOTE — lane `8rug-foundation-root-hygiene`

Work item: `model_performance-8rug` (project `model_performance`) — foundation ROOT bundle
hygiene, owner-approved for all three parts.

**Terminal outcome: (B) RESOLVED AT THE CAP.** A and C are DONE and shipped. B's code half is
DONE and measured; B's **eval is NOT-POSSIBLE at the $10 authority**, priced before spending,
with the arithmetic recorded. That is branch B of the goal's three outcomes, and it resolves.

---

## Deliverables

| # | Deliverable | State |
|---|---|---|
| A1 | Root `bundle.md` body is the single `@mention`; prose in frontmatter `description:`; fail-before via the fixed validator | **DONE** |
| A2 | Real-session check — foundation as ROOT, `raw.system` head is common-system-base, no feature table | **DONE** |
| C1 | Compose-semantics finding stated in the PR body either way, with a test asserting the effective `tool-skills` config | **DONE** |
| C2 | Real-session check — foundation's skills discoverable | **DONE** |
| B1 | Pre-registered eval, written before editing | **DONE** (commit `07d51b9`) |
| B2 | The split: `delegation-core.md` <500 tokens on disk, depth loaded from named agent bodies, same triage for `multi-agent-patterns.md` | **DONE** |
| B3 | `tasks.yaml` references the same core, or drops context — justified | **DONE** (references the core) |
| B4 | The ~6× estimator understatement fixed, on-disk measurement quoted | **DONE** (6.28×, `validate-bundle-repo` v3.14.0) |
| B5 | **The eval itself: n≥3/arm, both providers, decision rule evaluated** | **NOT-POSSIBLE — cap** |
| B6 | DTUs destroyed, ledger 0 open rows, spend arithmetic in the PR body | **DONE** (no DTUs created, 0 rows opened) |

### B5 — NOT-POSSIBLE, leading with what WAS executed

**Executed before this refusal:** the complete code half of B — a 487-token core file, the
depth split, the agent-body routing for two named agents, the `tasks.yaml` decision, and the
estimator repair; **33 new tests** (12 of which fail on `main`); a full suite at **2,064
passed**; the estimator fail-before/pass-after matrix (`1000 → WARNING` vs `6276 → ERROR` on
main; `487 → ok` on branch); and **3 real foundation-root sessions** measuring the root
prompt at **130,055 → 106,655 chars, 52,741 → 47,014 provider-reported input tokens
(−5,727)**. What was *not* bought is the eval that would test the primary metric.

The pre-registered design is 3 runs × 2 arms × 2 providers = **12 valid runs**. At the
program's observed **67 %** validity that is **18 launches**; at the observed per-run prices
(**$3.53** opus-5 S3, **$4.63** terra) that is **9 × $3.53 + 9 × $4.63 = $73.44**. Even at
100 % validity it is **$48.96**; even substituting the cheaper sonnet figure, **$41.52**.
**The authority is $10.**

What $10 could buy — 3 launches at the cheapest observed rate ($2.62/run) ⇒ **~2 valid runs =
n=1 per arm on one provider** — cannot produce a lower bound on success, so it cannot
evaluate the rule's first condition at all, and a single delegation count per arm cannot
support the ±30 % comparison. **0 % chance of landing the deliverable.** Priced on first read
of the goal; **$0 spent on the eval**; design not shrunk and re-labelled.

**The authority that WOULD close it: $73.44** (`12 valid runs / 0.67 validity = 18 launches ×
blended $4.08 = $73.44`), or **$48.96** if every launch is valid.

**Residue and the smallest useful purchase it could not buy:** the full **$10** is unspent.
The smallest purchase that would advance B5 is a *complete* n≥3 both-provider arm pair —
**$73.44**. There is no smaller indivisible purchase that produces an evaluable result
against the frozen rule, so the entire authority is unspendable for this deliverable.

---

## Published

| PR | Branch | Contents | State |
|---|---|---|---|
| [#368](https://github.com/microsoft/amplifier-foundation/pull/368) | `lane/8rug-foundation-root-hygiene` | **A + C** | **ready for review** (gates hold) |
| [#369](https://github.com/microsoft/amplifier-foundation/pull/369) | `lane/8rug-foundation-root-hygiene-b` | **B** | **DRAFT — HELD**, eval unfunded |

Neither is merged. The manager verifies the fail-befores and merges.

---

## Findings

### 1. (A) Foundation's ROOT body was 499 chars of documentation prose — sent as system instruction

`docs/BUNDLE_GUIDE.md:138`: *"Everything below the frontmatter is sent to the model as system
instruction. It is not documentation, and it is not free."* Root `bundle.md` carried a
`# Foundation Bundle v2.0` title, a feature table and an MCP config sample below the
frontmatter, **ahead of** `@foundation:context/shared/common-system-base.md`.

Same class as the notify-README defect fixed by app-cli #315/#316 — this time in the
composition base every non-anchors bundle inherits.

```
check_body_is_instruction("bundle.md")
  main a0decc6 : FLAG, prose_chars 499  "no second-person address -- reads as documentation"
  this branch  : OK,   prose_chars 0    "body carries no prose (mentions/scaffolding only)"
repo-wide body-instruction-check: 1 prose_below_frontmatter WARNING -> 0, over 32 files
```

Real session, foundation as ROOT: `raw.system[0].text` head went from
`# Foundation Bundle v2.0 / This bundle provides... | Feature | Description |` to
`@foundation:context/shared/common-system-base.md → # Primary Core Instructions / You are
Amplifier...`. **130,055 → 129,178 chars.**

### 2. (C) The `tool-skills` double mount is a FINDING, not a bug — and nothing is dropped

Measured **before** touching anything, as the goal required. `merge_module_lists` collapses
the two declarations by module id; `deep_merge` **concatenates** list-typed config, so both
`skills` sources survive in **both** compose orders, and `visibility` (contributed by only one
mount) survives too. A live foundation-root session lists all three foundation skills
alongside the curated collection. **No code changed for (C).**

But the safety is load-bearing on one invisible rule — the list-concat added in `70d521f`
(#120), which fails **silently** if it regresses (the skills just stop being discoverable).
`tests/test_tool_skills_double_mount.py` pins it: disabling list-concat fails 2 of its 7
tests.

**Residual risk recorded, not fixed:** the two mounts are order-insensitive *only* because
they name the same `source`, and `source` is a scalar (later wins). A test now pins that
agreement.

### 3. (B) The validator understated by 6.28× — and landed exactly on its own ERROR boundary

Rule 4 resolved a `context.include` two ways: leading `@` → flat 500 tokens; otherwise → try
as a relative path. A `<namespace>:<path>` ref matches **neither**, so it took the 500
default. Two includes × 500 = **exactly 1000**, against a `> 1000` ERROR gate: **6,276 real
on-disk tokens reported as 1000 and graded WARNING — one token below the ERROR that was
true.** The measurement that would justify splitting the context was being taken with a ruler
wrong by 6×, in the direction that made the problem invisible.

### 4. Cross-cutting: three defects in this lane, one shape

(A) a validator whose extractor returned an empty string that satisfied every check
(`aaa5c47`, fixed before this lane); (B) an estimator whose fallback produced a number that
read as measured; (C) a merge rule whose regression produces no error at all. **In each case
the instrument reported "fine" for a condition it could not observe.** The (B) fix therefore
does more than raise a number — it makes an unresolvable include *say so*
(`context_unresolved_includes`, `context_tokens_is_estimate`), because a guess folded silently
into a measured-looking total is the actual defect.

---

## Spend

| item | cost |
|---|---|
| (A) real-session checks — 2 × `amplifier run "hi"`, `haiku` | $0.14 |
| (B) real-session check — 1 × `amplifier run "hi"`, `haiku` | $0.06 |
| First orientation run before pinning a cheap provider (default opus, 161k input) | $1.01 |
| **(B) eval** | **$0.00 — NOT bought, priced at $73.44 against a $10 authority** |
| **Total** | **$1.21** |

**Authority: $10, for B's eval only.** The eval was not bought, so **$10 of the eval
authority is unspent**. The $1.21 above is the goal's "$0 — code + tests + local sessions"
category; it is reported rather than rounded to zero because it is real money.

**Infrastructure: none.** No DTUs created, no Gitea instances, **0 ledger rows opened**, so
nothing to tear down:

```
$ awk -F'\t' '$4=="open"' <batch>/infra.tsv | wc -l   # this lane contributed 0 rows
```

---

## Deviations and choices recorded

1. **Two branches, not one.** The goal requires two PRs with independent merge fates. PR 2 is
   branched from `origin/main`, not from PR 1, so A/C can merge while B is held.
2. **`haiku` for the real-session checks.** The first run cost **$1.01** at the default
   provider with 161k input tokens (the host's global `bundle.app` list composed onto the
   root). Subsequent runs pin `-p haiku` and set `bundle.app: []` in project-scope settings —
   which also makes the measurement *cleaner*, since it measures foundation-as-ROOT rather
   than foundation-plus-this-host's-app-bundles.
3. **`~/.amplifier/cache` never edited.** Source override via a project-scope
   `.amplifier/settings.yaml` in a scratch directory (`/tmp/8rug-rootcheck`), which takes
   precedence over global settings. The host is shared with live lanes.
4. **`infra_ledger.sh sweep` never run.** No infrastructure was created, so
   `lane_teardown.sh` had nothing to claim or tear down.
5. **Two test files touched that this lane did not otherwise own**, both because the rename
   made them false: `tests/test_frontmatter.py` (used `delegation-instructions.md` as a live
   example of a resolvable mention) and `tests/test_module_dep_resolvability_check.py` (pins
   the recipe version, bumped 3.13.0 → 3.14.0). Neither assertion's *subject* changed.
6. **Pre-existing suite failures not claimed:** `test_sources.py::test_resolve_existing_file`
   and `test_grpc_adapter_main.py::test_non_isinstance_object_with_mount_passes` — both
   reproduce on `a0decc6` with this lane's changes stashed, verified explicitly.
7. **`examples/agents/file-responder.md`'s `NO_TOOLS_SECTION` warning left alone** — it is a
   fixture demonstrating capability inheritance, per the goal's KNOWN section.

## What remains open

- **B5.** The pre-registered eval is unbought. B does not merge without it. `#369` carries the
  frozen rule, the design, and the price; whoever funds **$73.44** can run it against the
  branch unchanged.
- **Evaluability risk on B5, recorded before results exist:** if arm-A S3 runs median 0
  delegations, the ±30 % condition is unevaluable and the honest report is "unevaluable" — not
  a substitute gate invented afterwards (`otr`'s failure mode).
- **Breaking for external references:** `context/agents/delegation-instructions.md` no longer
  exists on `#369`'s branch. In-repo references were updated; bundles outside this repo that
  `@`-mention the old path would break if B ever merges.

## Capture root

`/home/bkrabach/dev/openai-evals-team-ci/.amplifier/evaluation/treatment-validation/2026-09-06-8rug/`

```
A-before-raw-system-head.txt        A-after-raw-system-head.txt
A-before-after-bundle-md-check.json A-after-body-instruction-check.json
C-skills-discoverable.txt           B-estimator-before-after.txt
B-root-prompt-before-after.txt      B-depth-reachable-from-agents.txt
```
