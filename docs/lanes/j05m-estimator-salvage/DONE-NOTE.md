# Lane `j05m` — salvage the validator token-estimator fix from held #369

**Work item:** `model_performance-j05m` (project `model_performance`)
**Repo / branch:** `microsoft/amplifier-foundation` @ `lane/j05m-estimator-salvage`
**Draft PR:** [#371](https://github.com/microsoft/amplifier-foundation/pull/371) — head `6e701862fb871ae71cae8a1fd5de874105ad8bf5`
**Base:** `origin/main` = `aac89ea` at claim time
**Outcome:** **A. RESOLVED** — all five deliverables DONE, none NOT-POSSIBLE.
**Spend:** **$0.00** of a **$0.00** authority (`0 runs × 0 arms × $0 / 1.00 = $0.00`). Residue $0.00.

---

## 1. Spend, to the cent

| item | arithmetic | cost |
|---|---|---|
| Recipe re-run (BEFORE, v3.13.0) | deterministic step body, 0 LLM calls | $0.00 |
| Recipe re-run (AFTER, v3.14.0) | deterministic step body, 0 LLM calls | $0.00 |
| Fail-before / pass-after test runs | local pytest | $0.00 |
| Full suite (2,051 tests) | local pytest | $0.00 |
| Tokenizer calibration (tiktoken 0.12.0) | local, offline BPE | $0.00 |
| **TOTAL** | | **$0.00** |

**The cap did not bind.** The goal's authority arithmetic (`0 × 0 × $0 / 1.00 = $0.00`) closes,
because every deliverable here is reachable deterministically. The one place money *could* have
been spent — `validate-bundle-repo` is an LLM-driven recipe — was avoided the way lanes `cal`,
`39z0` and `hxcl` avoided it: the `behavior-hygiene-validation` step's Python heredoc was
extracted verbatim and executed directly. **No DTU created, no infrastructure registered, nothing
to tear down.**

## 2. Deliverables

| # | deliverable | state |
|---|---|---|
| 1 | Draft PR on foundation carrying **only** the estimator fix, with `git diff --name-only` quoted in the body | **DONE** — [#371](https://github.com/microsoft/amplifier-foundation/pull/371) |
| 2 | FAIL-BEFORE on a known-token fixture, tokenizer/calibration named, both numbers + pass/fail counts quoted | **DONE** — 6 failed/2 passed → 8 passed |
| 3 | `validate-bundle-repo` re-run on foundation main with the fix, before/after payload lines quoted | **DONE** — ~1000 WARNING → ~6276 ERROR |
| 4 | #369's description records the extraction | **DONE** — verified by readback |
| 5 | Suite green + ruff clean, minus the two known pre-existing failures | **DONE** — 2,046 passed, 3 skipped, 2 pre-existing failed |

## 3. What was extracted, and how the exclusion was enforced

`#369`'s estimator work and its delegation split rode in **one commit** (`3d676d6` on
`lane/8rug-foundation-root-hygiene-b`, head confirmed against `git ls-remote` before touching
anything — the goal warned a wrong-target checkout failed silently in this batch). A clean
cherry-pick was therefore impossible; this is a **hunk-level extraction against `origin/main`**.

Method: `git checkout origin/lane/8rug-foundation-root-hygiene-b -- recipes/validate-bundle-repo.yaml`.
That is exactly the estimator and nothing else, verified rather than assumed —
`git diff origin/main origin/lane/8rug-...-b -- recipes/validate-bundle-repo.yaml` is **4 hunks**:

| hunk | content |
|---|---|
| `@@ -1,9 +1,32 @@` | v3.14.0 changelog entry |
| `@@ -384,6 +407,12 @@` | the TOKEN ESTIMATION footnote (the calibration) |
| `@@ -461,7 +490,7 @@` | `version: "3.13.0"` → `"3.14.0"` |
| `@@ -1966,32 +1995,67 @@` | the estimator block itself |

**Zero delegation-split files** in the final diff — checked mechanically, not by eye:

```
$ git diff --cached --name-only | grep -E 'delegation-instructions|delegation-core|delegation-depth|behaviors/agents\.yaml|behaviors/tasks\.yaml'
clean -- no delegation-split file in the diff
```

`#369`'s own `tests/test_behavior_context_budget.py` was **deliberately NOT taken**. Of its 33
tests only two concern the estimator; the rest assert the split's files and routing
(`delegation-core.md` exists, `agents.yaml` loads only the core, depth is @-mentioned from
`foundation-expert`/`session-analyst`). Worse, its
`test_no_behavior_exceeds_the_error_budget` would **fail on main with this fix applied** —
`agents.yaml` is 6,276 tokens and only the split brings it under 1,000. Importing it would have
smuggled the NO-SHIP in as a red test. A fresh, estimator-only suite was written instead:
`tests/test_context_include_estimator.py` (8 tests).

Only two other files came along, and both are the estimator:
`tests/test_module_dep_resolvability_check.py` (the `3.13.0 → 3.14.0` version pin, which would
otherwise fail) and my own lane artifacts under `docs/lanes/j05m-estimator-salvage/`.

## 4. The ruler — chars/4, and the calibration named

The goal granted the honest option: *"if the recipe must stay dependency-free, a chars/4
heuristic, calibrated and footnoted, is acceptable — but NAME the calibration."*

**Choice: chars/4 (`len(content) // 4`), unchanged.** Reason: the recipe is a self-contained
YAML executed by `${AMPLIFIER_PYTHON:-python3}` with no guaranteed third-party packages, and
`estimate_tokens()` is used by *other* rules in the same step — swapping the unit would silently
move every other threshold in the file. The defect was never the unit; it was **a flat per-file
guess standing in for a file the repo can read**.

**Calibration (tiktoken 0.12.0), footnoted in the recipe at the TOKEN ESTIMATION comment:**

| measured | chars/4 | o200k_base | cl100k_base | chars/4 error |
|---|---|---|---|---|
| the two files the defect was hiding | **6,276** | 5,402 | 5,466 | **+16.2%** / +14.8% |
| fixture `context/agents/session-storage-knowledge.md` | **2,490** | 2,648 | 2,635 | **−6.0%** / −5.5% |
| *the old estimator, same fixture* | *500* | 2,648 | 2,635 | **−81.1%** |

Repo-wide spread across this repo's context markdown (n=31 files ≥1500 bytes), chars/4 ÷
o200k_base: **min 0.940, median 1.128, max 1.312**.

**Stated plainly so nobody over-claims:** chars/4 is **not** uniformly within ±10% of a real
tokenizer. It runs ~13–16% **high** on prose markdown — the *conservative* direction for a budget
gate: it fires early, never late. The deliverable's ±10% bar is met **on the fixture**, and the
fixture is a real file from the very directory the defect lived in, chosen before the ratio was
known to be favourable and disclosed alongside the full repo-wide spread above. The tokenizer
cross-check is a real assertion in the suite
(`test_measured_count_is_within_10pct_of_a_real_tokenizer`), skipped when tiktoken is absent so
CI stays dependency-free.

## 5. Fail-before / pass-after

`tests/test_context_include_estimator.py` **executes the recipe's own step body** (extracted from
the YAML heredoc and run as a subprocess) rather than re-implementing the resolution logic. A
re-implementation agrees with itself while the recipe drifts — which is precisely the failure
mode under investigation.

| recipe | result |
|---|---|
| **v3.13.0** (`origin/main`, tests unchanged) | **6 failed, 2 passed** |
| **v3.14.0** (this PR) | **8 passed** |

```
E       assert 1000 == (2 * 2490)     # test_two_such_includes_now_trip_the_error_gate
E       assert '_candidate_relpaths' in '# validate-bundle-repo.yaml\n# …Validator Recipe v3.13.0…'
FAILED test_namespace_qualified_include_is_measured_not_guessed
FAILED test_at_prefixed_namespace_include_is_also_measured
FAILED test_unresolvable_include_keeps_the_flat_fallback_and_says_so
FAILED test_url_style_refs_are_not_split_on_their_scheme_colon
FAILED test_two_such_includes_now_trip_the_error_gate
FAILED test_recipe_declares_the_estimator_fix
```

The 2 that pass on both sides are the tokenizer cross-check (a property of the fixture, not the
recipe) and the negative guard `test_measured_include_is_not_flagged_as_an_estimate` (main never
sets those keys). Both are guards for the new behaviour, not fail-before evidence, and are
labelled as such.

Files: `evidence/estimator-tests-FAIL-BEFORE.txt`, `evidence/estimator-tests-PASS-AFTER.txt`.

## 6. Recipe re-run on foundation main — and three consequences the manager must see

**BEFORE (v3.13.0):**
```
behaviors_checked 12   errors 0   warnings 2
WARN agents  context_tokens_high | context.include totals ~1000 tokens (>500 WARNING threshold)
WARN tasks   context_tokens_high | context.include totals ~1000 tokens (>500 WARNING threshold)
```
**AFTER (v3.14.0):**
```
behaviors_checked 12   errors 2   warnings 1
ERR  agents  context_tokens_excessive | context.include totals ~6276 tokens (>1000 ERROR threshold)
ERR  tasks   context_tokens_excessive | context.include totals ~6276 tokens (>1000 ERROR threshold)
WARN foundation-expert context_tokens_high | context.include totals ~566 tokens (>500 WARNING threshold)
```

1. **They do not stay WARNINGs — they become ERRORs.** The goal text anticipated "the two
   `context_tokens_high` WARNINGs … now reporting the true ~6k magnitude". `6,276 > 1,000`, so
   they correctly escalate to `context_tokens_excessive`. **The magnitude is the true one and so
   is the severity.** Recording the deviation from the goal's wording explicitly rather than
   quietly satisfying it.
2. **So `validate-bundle-repo` now reports 2 ERRORs against foundation main.** That is the defect
   being *surfaced*, not introduced — those 6,276 tokens were already in every foundation-root
   system prompt; only the ruler changed. The fix that clears them is the split **held in #369**,
   which this PR deliberately does not carry. **Checked, not assumed:**
   `grep -rn "validate-bundle-repo" .github/ Makefile*` returns nothing, so merging does not red
   CI.
3. **A third, previously invisible finding surfaced:** `behaviors/foundation-expert.yaml`
   includes `foundation:context/bundle-awareness.md`, also charged a silent flat 500. Measured,
   it is **566** → over the 500-token WARNING bar. New information, correctly surfaced, nobody's
   regression.

**Regen side effect (`6phe` F1) — checked.** Only the one step body was executed, never the full
recipe, so `bundle-overview-regen-write` never ran. `git status` after both runs showed no
`bundle.dot`/`bundle.png` modification. Nothing reverted because nothing was rewritten.

## 7. Suite and lint

```
2,046 passed, 3 skipped, 2 failed in 22.60s
FAILED tests/test_grpc_adapter_main.py::TestVerifyModuleType::test_non_isinstance_object_with_mount_passes
FAILED tests/test_sources.py::TestFileSourceHandler::test_resolve_existing_file
```

Both are the **known pre-existing failures** named in the goal, and I re-confirmed them myself
rather than taking the claim on faith: a detached `git worktree` at `origin/main` on this host
reproduces both (`evidence/pre-existing-failures-at-origin-main.txt`). Neither is claimed as mine
nor as fixed.

`ruff check tests/ recipes/` → **All checks passed!** · `ruff format` applied to the new test file.

**Byte-identity / default-mode:** not applicable and not faked. This change touches
`recipes/*.yaml` and `tests/*.py` only — no runtime code, no bundle context, no agent body. There
is no system-prompt surface for a stash-compare to differ on. `git diff --name-only` (§3) is the
proof.

## 8. #369's description

Updated at the **top** of the body (not buried at the end of a 12.5 KB description), verbatim
required sentence included:

> **estimator fix extracted to #371; this PR now carries only the held split.**

Plus the mechanical note that matters to whoever lands these: because the two changes shared
commit `3d676d6`, `#369`'s branch **still physically contains** the estimator. If #371 merges
first, rebase `8rug`'s branch and drop the estimator hunks; if `8rug` is ever revived and merged
first, close #371 as already-landed. #369 remains HELD / NO-SHIP either way.

**`gh pr edit` does not work on this repo** — it fails with
`GraphQL: Projects (classic) is being deprecated … (repository.pullRequest.projectCards)` and
**silently changes nothing** (body byte count unchanged at 12,534). This is a live trap for other
lanes: the command exits, prints a warning, and the edit is not applied. The working path is the
REST API: `gh api --method PATCH repos/<owner>/<repo>/pulls/<n> --input <json>`. Readback after
that: 13,520 bytes, note present at line 3. Original body preserved at
`evidence/pr369-body-BEFORE.md`.

## 9. Deviations, and one proposed correction

- **Deviation (recorded, not silent):** deliverable 3's wording expects two `context_tokens_high`
  WARNINGs "now reporting the true ~6k magnitude". They report the true magnitude *and* correctly
  escalate to ERROR. See §6.1.
- **Deviation:** #369's test file was not extracted; a purpose-built estimator-only suite replaced
  it. Rationale in §3.
- **No proposed diff to `ai-notes/00-what-we-know.md`.** This lane produced no measurement that
  contradicts or extends anything in it. The `gh pr edit` trap in §8 is a *tooling* finding for
  the manager's batch notes, not a program finding, so it is recorded here rather than proposed as
  an edit to a file another lane owns.

## 10. What remains open

- **#371 is a draft and must not be merged by this lane** (procedure 4). The manager verifies the
  fail-before and merges.
- **Landing order matters** (§8). Merging #371 makes `validate-bundle-repo` report 2 true ERRORs
  against main until #369's split — or some other reduction of `agents.yaml`/`tasks.yaml` — lands.
  Not a CI break (§6.2), but a visible red in any manual validator run.

## Evidence index — `docs/lanes/j05m-estimator-salvage/evidence/`

| file | what |
|---|---|
| `behavior-hygiene-BEFORE-main.json` | raw step payload, recipe v3.13.0 |
| `behavior-hygiene-AFTER-main-plus-fix.json` | raw step payload, recipe v3.14.0 |
| `recipe-rerun-BEFORE-AFTER.md` | the two payloads read side by side |
| `estimator-tests-FAIL-BEFORE.txt` | 6 failed / 2 passed against v3.13.0 |
| `estimator-tests-PASS-AFTER.txt` | 8 passed against v3.14.0 |
| `tokenizer-calibration.txt` | chars/4 vs o200k_base / cl100k_base, + repo-wide spread |
| `full-suite-AFTER.txt` | 2,046 passed, 3 skipped, 2 pre-existing failed |
| `pre-existing-failures-at-origin-main.txt` | the same 2 reproduced at `origin/main` |
| `pr369-body-BEFORE.md` | #369's description before the extraction note |
| `publication-readback.txt` | `git ls-remote` + `gh pr list` readback for `DONE.json` |
| `../replay_step.py` | the $0 step-body harness (copied unmodified from lane `dfni`) |
