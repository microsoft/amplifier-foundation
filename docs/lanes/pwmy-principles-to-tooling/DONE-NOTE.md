# DONE-NOTE — model_performance-pwmy

**Move the description rules from convention to enforcement.**

Terminal outcome: **A — RESOLVED.** Every deliverable is DONE. Nothing was
recorded NOT-POSSIBLE, and nothing was blocked. Spend against the $5 authority:
**$0** (see §7).

---

## 1. What this closes, in one paragraph

PR #341 set the no-`<example>` policy for description surfaces in
`context/shared/description-authoring-principles.md`. It was applied inside
`amplifier-foundation` and **nowhere else**, because nothing at authoring or
validation time said otherwise. A sweep a year later found **29 files across 6
repos** still shipping example blocks and fixed them **by hand**. This lane
moves the rules to where they execute: the caps are validator-enforced, the
skill half of the policy is checked for the first time in any repo, awareness
redundancy and per-bundle head cost are measured, the creation surfaces emit
compliant descriptions by default, and there is one documented command that
refreshes an existing repo with a fidelity table.

---

## 2. Deliverables

| Deliverable | State |
|---|---|
| Docs updated with the measured citations, stated as applying to EVERY bundle | **DONE** — §3 |
| Five validator checks, each with a fail-before test on a fixture violating all four | **DONE** — §4 |
| Both recipes re-run on foundation main AND anchors-amp-dev, results quoted | **DONE, with one named deviation** — §5 |
| Creation skills emit compliant descriptions by default, demonstrated | **DONE** — §6 |
| Refresh path demonstrated on an already-swept repo, compared to the hand work | **DONE** — §6 |
| Draft PR, marked ready when CI is green, not merged | **DONE** — see DONE.json `publication` |
| DONE-NOTE at the lane artifact root | this file |

---

## 3. (a) Docs

`context/shared/description-authoring-principles.md`

- **Scope banner**: the rules apply to **every** bundle in any repository, not
  just foundation, and name the two recipes that enforce them.
- **The one-line WHY, with the bill measured**: agent descriptions render into
  the always-on `delegate` catalog, skill descriptions into the
  `hooks-skills-visibility` block; both are paid on every request whether or not
  the capability is used. Cited: `zc6t`'s on-the-wire measurement of the runtime
  agent catalog at **12,904 → 3,597 chars**; the lean head at **84,319 →
  48,249 chars** — cited **as a bundle composition, not as a universal figure**,
  because the same commit measured 320,410 chars of head on a host composing 86
  tools where the eval container composed 14. Quoting 48,249 as "the head" would
  be wrong, and `zc6t`'s own commit says so.
- **The history, so the scope statement does not quietly stop being true too**:
  #341, the 29 files across 6 repos, dot-graph's agent descriptions **14,615 →
  6,484 chars**.
- **V5 rewritten**: the enforced unit is now CHARACTERS, with the measured
  distribution across 9 repos that justifies 600/400 and the head-cost table
  that justifies 4,000. See §4.1.
- **V7 added** — shape: trigger first, then USE WHEN, then DO NOT USE WHEN
  naming what should handle the rejected case, and the fidelity rule.

`docs/BUNDLE_GUIDE.md` — new **"Awareness: concept + trigger + pointer"**
(the test — what can this file say that a catalog line cannot; the four rules;
the thin-variant worked example with anchors → anchors-amp-dev at 2,483 / 1,760
chars; instruction `@mention`s lead the context block, foundation `2ef5e12`; no
root bundle composed as a behavior, app-cli #316) and new **"Refreshing
descriptions"** (§6).

`docs/AGENT_AUTHORING.md` — the token-budget section is replaced by the char
cap; the template now carries the DO NOT USE WHEN clause and the budget; a
short pointer says awareness files are not a place to describe an agent.
`g7h3`'s **−13.57% $/task, CI [−22.27%, −4.86%], all three pre-registered
estimators excluding zero** is cited in the head-cost WHY.

---

## 4. (b) Validators

`recipes/validate-bundle-repo.yaml` **v3.14.0 → v3.15.0**,
`recipes/validate-agents.yaml` **v1.7.0 → v1.8.0**.

### 4.1 Check 1 — length cap, in CHARACTERS

WARN at the cap, ERROR at 2x: **agents 600 / 1,200**, **skills 400 / 800**.
The two agent numbers are byte-identical in both recipes and
`test_both_recipes_carry_the_same_two_cap_numbers` fails the build if they
diverge.

**Why the old gate could not fire.** It was tokens — WARN >300, ERROR >600,
i.e. 1,200 / 2,400 chars at chars/4. Measured 2026-09-07 across 9 bundle repos
on this host:

| Corpus | n | mean | median | p90 | max | over cap | over 2x |
|---|---|---|---|---|---|---|---|
| foundation agents (aligned by #341) | 24 | 410 | 427 | 721 | 761 | 6 | **0** |
| all agents, 8 repos incl. unswept | 54 | 1,084 | 739 | 2,396 | 3,235 | 36 | **17** |
| foundation skills | 3 | 420 | 384 | 566 | 566 | 1 | **0** |
| skills-bundle skills | 31 | 413 | 312 | 814 | 928 | 13 | **4** |

The old ERROR line (2,400 chars) sat **above the longest description in every
already-aligned repo**, so on an aligned repo it was unfireable. The new one
separates aligned from unaligned: foundation 0 errors, dot-graph 12 of 12 over.

**Caveat on that corpus, stated rather than buried:** those are the local
checkouts on this host, and several are PRE-sweep (dot-graph's still carries 12
descriptions with `<example>` blocks). The sweep landed as PRs on other
branches. The distribution is therefore "before alignment", which is exactly
what a cap has to discriminate against.

### 4.2 Check 2 — `<example>`/`<commentary>` for SKILLS, repo-wide

New Phase 2.82. Agents were already covered; **no recipe in any repo has ever
checked a skill description**, though the policy was written for both. Same
example policy (ERROR), tighter cap, and v3.13.0's lesson applied from the
start: each way of failing to READ a description gets its own status and its
own ERROR, because collapsing them into `""` makes a broken skill pass every
check clean.

### 4.3 Check 3 — awareness redundancy, with the rule stated

New Phase 2.84. Two rules, both WARNING, both written out in the step and
echoed in the step's own output under `rule` so a finding can be audited:

- **R1 term-overlap** — a sentence is covered when ≥60% of its key terms appear
  in one catalog description; a file is redundant when ≥60% of its substantive
  (≥3 key terms) sentences are covered. Reports the winning entry **by name**.
- **R2 pointer-only** — the file invokes `delegate(`/`load_skill(` naming an
  in-repo entry, and ≥60% of its sentences are trigger-shaped, by phrase or by
  sitting under a "When/How to Use" heading.

**Calibrated, not guessed.** Across every cached bundle repo on this host —
**36 context files** — R1 fires **0 times** (max coverage observed **0.333**,
median 0.0375) and R2 fires **3 times**: `dtu-awareness.md` (0.889),
`gitea-awareness.md` (0.833), `reality-check-awareness.md` (0.667). Each of the
three is genuinely when-to-use prose plus a pointer. Files with real extra
content — `amplifier-tester`'s resource-accounting section,
`browser-tester`'s troubleshooting table — correctly do **not** fire.

**Honest limit:** R1 has never fired on a real corpus file. It is retained
because it is the rule that catches verbatim restatement, and the fixture
demonstrates it firing; `best_coverage` is reported for every file either way,
so the threshold can be re-argued against numbers rather than re-guessed.

### 4.4 Check 4 — bundle head cost, threshold justified

New Phase 2.86: `context chars + agent description chars + skill description
chars`, per bundle, **WARNING at 4,000**. The step states how each term is
attributed. Measured by the step itself, 2026-09-07:

| Bundle | head chars | engineered for head cost? |
|---|---|---|
| `bundles/anchors-amp-dev/bundle.md` | 1,760 | yes |
| `bundles/anchors/bundle.md` | 2,483 | yes |
| context-intelligence `bundle.md` | 5,479 | no |
| foundation `bundle.md` | 14,198 | no |
| superpowers `bundle.md` | 15,002 | no |
| foundation `behaviors/agents.yaml` | 26,368 | no |
| dot-graph `bundle.md` | 29,684 | no |
| converge `bundle.md` | 87,555 | no |

4,000 is the smallest round number above every cost-engineered bundle (1.6x
headroom) and below every unengineered one (1.37x). **WARNING, not ERROR**,
because foundation's own root bundle exceeds it — that is a design
conversation, not a build break. An include this repo cannot resolve is **not**
charged a flat guess: the total is marked `is_lower_bound` and the unresolved
refs are named (v3.14.0's lesson). A bundle that disables `tool-skills`
visibility does not pay the skill half; the amount is reported separately as
`skill_description_chars_excluded` rather than silently omitted — anchors' 1,261
chars are visible in the report precisely because turning visibility off is a
real lever.

### 4.5 Check 5 — fail-before, reproducible by anyone

`test-fixtures/misaligned-bundle/` violates all four at once (its README
tabulates how). `tests/test_description_alignment_checks.py` runs the recipes'
**own step bodies** against it — never a re-implementation, which would agree
with itself while the recipe drifted.

Point `ALIGNMENT_RECIPE_DIR` at the pre-change recipes and the suite fails:

```
git worktree add --detach /tmp/foundation-main origin/main      # 4384805
ALIGNMENT_RECIPE_DIR=/tmp/foundation-main/recipes pytest tests/test_description_alignment_checks.py
```

| | result |
|---|---|
| origin/main **4384805** | **23 failed, 1 passed** |
| this branch | **24 passed** |

The one test that passes at main is the one that should:
`test_agent_description_under_the_cap_is_clean` — a 599-char clean description
is clean under both the old token gate and the new char gate.

---

## 5. Recipe runs — results quoted

### validate-agents v1.8.0, FULL recipe run

| Target | Verdict | Detail |
|---|---|---|
| foundation (this branch) | **⚠ PASS WITH WARNINGS** | 24 agents / 28 candidates / 4 non-agents; 18 good, 5 polish, 1 needs_work, 0 critical; **0 errors**, 6 warnings, 6 suggestions |
| `bundles/anchors-amp-dev` | **✅ PASS** | 1 agent; 0 errors, 0 warnings; description 385 chars |

The 6 warnings on foundation are 5 × `DESCRIPTION_HIGH` (foundation-expert 761,
session-analyst 721, security-guardian 669, git-ops 647, modular-builder 631 —
all under the 1,200 ERROR line) and 1 × `NO_TOOLS_SECTION` on
`examples/agents/file-responder.md`, a deliberate fixture that four prior lanes
(`xe1u`, `39z0`, `hxcl`, `8rug`) have each reported and correctly left alone.
**Quoted, not tuned.**

Worth noting: the report step, given the new guidance, produced compliant
rewrites *with fidelity tables* for all five over-cap agents unprompted. Those
rewrites are not applied here — re-sweeping foundation's descriptions is not
this item.

### validate-bundle-repo v3.15.0

| Target | Run | Result |
|---|---|---|
| `bundles/anchors-amp-dev` | **FULL** (34 steps, `hygiene_only`, `enhance_diagrams:false`) | agent 1/0 err/0 warn · skill **skipped** (no SKILL.md) · awareness 1 file, best_coverage 0.0, 0 warn · head cost **1,760** (`is_lower_bound`, unresolved `anchors:context/system.md`) |
| foundation | **deterministic steps only** — deviation, see below | agent 24 checked, **0 err / 5 warn** · skill 3 checked, **0 err / 1 warn** (per-repo-conventions 566) · awareness 7 files vs 27 catalog entries, **0 warn** · head cost 19 bundles, **3 warn**, largest **26,368** |

**Named deviation.** The full `validate-bundle-repo` run **writes files** — it
regenerated `bundle.dot`/`bundle.png` into `bundles/anchors-amp-dev/` during the
run above (removed afterwards; `git status` clean). Running it fully against the
repo root would rewrite foundation's own root `bundle.dot`, an unrelated change
in this PR's diff. All four new checks are deterministic and were executed
against foundation directly; the steps not run are the LLM synthesis and
diagram-regeneration phases, which format and draw but do not decide.

**Two findings from the anchors-amp-dev run, reported not fixed** (neither is
this item):

- **F1 — `no_composable_surface` is a false positive on a nested variant
  bundle.** With `repo_path` pointed at `bundles/anchors-amp-dev`, the check
  reports `needs_work` for shipping `agents/` without `behaviors/`. But
  `/bundles/` holds standalone *variants* whose composable surface is the
  enclosing repo's `behaviors/` (12 files). The suggested remediation would
  *introduce* a hygiene violation. Fix would be to gate the check on repo-root
  evidence at `repo_path`.
- **F2 — `bundles/anchors-amp-dev/README.md:34-35` documents the include order
  inverted** relative to `bundle.md:23,26`, on an order `bundle.md` itself calls
  load-bearing ("fixes the merged `tools:` list and tool-skills' search path…
  reproduces the previously shipped mount plan byte-for-byte"). One-line fix.

**F3 — `docs/` is scanned, and it bit me.** Committing four evidence
`SKILL.md` files under `docs/lanes/` took this repo's shipped-skill count from
**3 to 7** and its largest head-cost figure from **26,368 to 28,279**. I widened
the exclusion set to fix it, and
`TestDiscoveryScopeParity::test_exclusion_sets_agree_across_both_recipes_and_this_guard`
caught me — the delta from validate-agents' wider scope is documented as exactly
`docs`. **I reverted rather than widening a contract another lane established**
(narrowing it would also hide a skill that genuinely lives under docs/), renamed
the evidence files to `SKILL.md.txt`, and pinned the trap with a test that fails
if this repo's shipped-skill count ever leaves 3. Recorded here because the next
lane to commit an example `SKILL.md` will hit it too.

---

## 6. (c) + (d) Creation surfaces and the refresh path

### The entry point

`recipes/refresh-descriptions.yaml` (new, v1.0.0), documented in
BUNDLE_GUIDE §"Refreshing descriptions":

```bash
amplifier tool invoke recipes operation=execute \
  recipe_path=foundation:recipes/refresh-descriptions.yaml \
  context='{"repo_path": "/path/to/your/bundle-repo"}'
```

Writes `violations.json`, `proposals.md`, `verdict.json`, `REPORT.md` and
**changes nothing in the repo**. Two design points that matter:

1. **It does not carry a copy of the rules.** It extracts and executes
   `validate-bundle-repo.yaml`'s own step bodies, and **fails loud naming every
   path it searched** if it cannot find that recipe. This repo has paid three
   times in one batch for divergent validator implementations (`xe1u`, `39z0`,
   `dfni`); a fourth copy of the thresholds is how the fourth divergence starts.
2. **The proposal step is an agent, and the fidelity table is machine-checked.**
   `verify-proposals` rejects a proposal with no `### Fidelity table` heading,
   or a table with no rows, or a proposed text still carrying `<example>`, or
   one over the cap with no `### Notes` naming the fact that forced it. Fidelity
   beats brevity — over-cap-with-a-reason is a PASS with a warning; over-cap
   silently is not.

### Demonstrated on an already-swept repo, against the hand work

Run clean-room against `amplifier-bundle-browser-tester` at **origin/main**
(the pre-sweep state, extracted with `git archive` — that repo was never
written to), with no sight of lane `kp79`'s answers:

| | files targeted | agent description chars | examples removed |
|---|---|---|---|
| before | — | 2,457 | 0 of 6 |
| hand sweep (`kp79`) | 3 | **1,664** | 6 of 6 |
| `refresh-descriptions` | **the same 3** | **1,644** | 6 of 6 |

Per file: operator 819 → 560 (hand) / **526** (tool); researcher 877 → 582 /
**564**; documenter 761 → 522 / **554**. Totals within **1.2%**.

**Fact-level comparison, which is the real test.** Every routing fact present in
the pre-sweep text survives in both versions; the tool's fidelity tables account
for each one and justify each drop by rule (`PROACTIVELY` → V6 decision rule;
both `<example>` blocks → V3, each carrying no fact not already in the prose).
**One real difference: the hand pass added a `web_fetch` rejection path to
`browser-operator`; the tool added `web_fetch` to `browser-researcher` only.**
Both are ADDED facts, present in neither original — so this is not a fidelity
loss, but it is the one place the tooling did not reproduce the hand work, and
it is named rather than averaged away. Artifacts:
`evidence/refresh-demo-browser-tester/`.

### Creation skills, compliant by default

**Foundation-owned, changed here:**

- `agents/bundle-design-expert.md` — the authoring authority. Its body said
  descriptions must include "WHY, WHEN, WHAT (taxonomy), **HOW (examples)**"
  and "Activation triggers (MUST, REQUIRED, ALWAYS…)", both now contradicted by
  V3/V6; and its build checklist instructed authors to "**Create awareness
  context — ~25-40 lines, domain exists, delegate to expert**", which is exactly
  the pointer-only file Phase 2.84 now warns about. All three replaced. Its own
  description was **739 chars** (over the cap it teaches); rewritten to **591**,
  trigger-first with a DO NOT USE WHEN clause. Fidelity: every routing fact kept
  (bundle design · mechanism selection · behavioral modeling · YAML authoring ·
  agent file authoring · context architecture · the modeling recipes); the
  `Authoritative on:` list was cut to the terms **not** already stated in the
  first two clauses (V1: state each rule once) and gained `description
  authoring` + `awareness files`; added a DO NOT USE boundary naming
  foundation-expert and core-expert, which was absent.
- `skills/creating-amplifier-modules/SKILL.md` — a tool `description` is a
  description surface too, and was the one with a measured **15,271-char /
  7-8x-over-delegation** failure (V5). The emitted template and the compliance
  checklist now say so.

**Skills-bundle-owned — patch shipped as an artifact, not committed.**
`amplifier-bundle-skills` is held by lane `smy5-patch-skills` and GOAL.md
forbids editing another repo, so
`patches/skills-bundle-description-alignment.md` carries drop-in before/after
patches for `personafy`, `skillify`, `councilify` and
`skills-assist/authoring-guide.md`.

**The defect that patch fixes is not hypothetical.** `personafy` currently
*instructs* a `description:` "at or below **~700–800 characters**" — nearly 2x
the enforced cap — and that bundle measures 13 of 31 skills over 400 chars and 4
over 800. The creation skill is emitting the defect by design.

**Fail-before / pass-after, measured with the shipped validator** (two
clean-room arms, same toy inputs, the only difference being the skill file's own
guidance):

| Arm | `skillify` output | `personafy` output | verdict |
|---|---|---|---|
| shipped guidance | **453 chars** | **714 chars** | 2 WARNINGs (both over the 400 cap) |
| patched guidance | **353 chars** | **391 chars** | **clean** |

Neither arm emitted an `<example>` block — that half of #341 has propagated;
the length half has not. Outputs: `evidence/creation-skill-demo/`
(named `SKILL.md.txt`, see F3).

---

## 7. Spend

**$0 of the $5 authority.** The authority is scoped to "a scratch-session run
of skillify/personafy if that cannot be done deterministically" — i.e. to
standing up scratch/DTU infrastructure. No infrastructure was created, nothing
was registered in the infra ledger, and no DTU was launched. The three
`delegate` calls used (the refresh proposal step and the two creation-skill
arms) run inside this lane's own session on the same footing as every other LLM
call the docs and validators consumed, which the goal prices at $0.

**No $/task measurement was bought.** `g7h3`'s $428.10 answer is cited, not
re-bought.

**Cap arithmetic, checked on first read as the goal requires.** The $5 is stated
as a bare figure with no `runs × arms × per-run / validity` arithmetic. That is
a defect in the goal's own authoring rule — but a harmless one here: this item
buys **no runs at all**, so there is no arithmetic that could fail to close and
no deliverable at risk. Reported, not absorbed.

---

## 8. Verification

```
pytest -q                      2087 passed, 3 skipped, 2 failed
```

The 2 failures are the two the goal names as pre-existing and not mine —
`test_grpc_adapter_main.py::TestVerifyModuleType::test_non_isinstance_object_with_mount_passes`
and `test_sources.py::TestFileSourceHandler::test_resolve_existing_file`.
**Confirmed, not assumed:** both fail identically at origin/main 4384805 in a
clean worktree.

One existing test was updated rather than deleted:
`test_module_dep_resolvability_check.py::TestVersionAndChangelog::test_version_is_current`
pins the recipe version and moved 3.14.0 → 3.15.0 with its reason recorded in
the docstring, matching how the three prior bumps were handled.

---

## 9. What remains open

1. **Foundation's own 5 over-cap agent descriptions** (761/721/669/647/631) are
   WARNINGs, left as WARNINGs. Re-sweeping them is a separate item; the
   validate-agents run above already contains proposed rewrites with fidelity
   tables for all five.
2. **The skills-bundle patch** needs applying once `smy5-patch-skills` releases
   that repo.
3. **F1** (`no_composable_surface` on nested variant bundles) and **F2**
   (anchors-amp-dev README include order) — reported above, not fixed.
4. **R1's threshold** (awareness term-overlap at 0.60) has never fired on a real
   corpus file. `best_coverage` is reported for every file so it can be
   re-argued from data.
