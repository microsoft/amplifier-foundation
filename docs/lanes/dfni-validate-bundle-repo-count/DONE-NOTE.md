# Lane dfni — validate-bundle-repo reported "0 errors" because it could not see the error class

**Item:** `model_performance-dfni` (project `model_performance`)
**Branch:** `lane/dfni-validate-bundle-repo-count`, branched from `origin/main` (head `e495755`, lane 39z0)
**Outcome:** **A — RESOLVED.** Every deliverable is DONE. Nothing is NOT-POSSIBLE; the cap did not bind.
**Recipe:** `recipes/validate-bundle-repo.yaml` v3.12.0 → **v3.13.0**

---

## Headline

The count was the symptom. **The real defect is that this step could not fail.**

`extract_description()` returned an **empty string** for *six different failures* — unreadable
file, absent frontmatter, unparseable YAML, non-mapping frontmatter, non-mapping `meta:`,
absent `description:` — and for a genuinely empty description too. All seven collapsed into
one indistinguishable value, and `""` then passed **every** check cleanly: 0 tokens is under
budget, and an empty string contains no `<example>` and no `<commentary>`.

So a **real agent whose frontmatter was missing or malformed was reported as checked and
clean**. Measured, on a five-file fixture where four files are broken in four different ways:

```
BEFORE  v3.12.0 :  agents_checked: 5,  errors: 0,  passed: True
AFTER   v3.13.0 :  agents_checked: 4,  errors: 3,  passed: False
                   [agent_frontmatter_invalid] agents/broken-yaml.md      (unparseable_frontmatter)
                   [agent_description_missing] agents/no-description.md   (no_description_key)
                   [agent_description_missing] agents/empty-description.md (description_empty)
                   NON-AGENT                   agents/no-frontmatter.md   (no_frontmatter)
```

That is why `agents_checked: 32, errors: []` was worth nothing as evidence on this class of
problem: **the number could not have been anything else.**

---

## The measurements

All numbers come from executing the recipe's **own step body** verbatim through bash, at
**$0.00 API spend** — no LLM step is involved in this phase. `replay_step.py` extracts the
heredoc; `measure.py` runs both versions side by side. Full output:
`evidence/deterministic-before-after.txt`.

### Measurement 1 — this repository (the count)

| | recipe | candidates scanned | `agents_checked` | non-agents | ERR | verdict |
|---|---|---|---|---|---|---|
| before (`e495755`, origin/main) | v3.12.0 | not reported | **32** | not reported | 0 | clean |
| **after (this branch)** | **v3.13.0** | **32** | **28** | **4** | **0** | clean |

**`errors: []` is preserved** — the deliverable's requirement. No newly-visible real defect
appeared in this repo, so there is nothing to leave unfixed. The four excluded files are named
with their reason:

| File | Reason |
|---|---|
| `context/agents/delegation-instructions.md` | `no_frontmatter` |
| `context/agents/multi-agent-patterns.md` | `no_frontmatter` |
| `context/agents/session-repair-knowledge.md` | `no_frontmatter` |
| `context/agents/session-storage-knowledge.md` | `no_frontmatter` |

These are context documents loaded via `context:` by `behaviors/agents.yaml` and
`behaviors/tasks.yaml`. Nothing spawns them; they carry no frontmatter because they are not
agents. **28 is the true agent count** — the same number 39z0 measured, reached independently
here by the same classifier.

### Measurement 2 — the synthetic repo (the silent hole)

Quoted in the Headline above. This is the measurement that matters: measurement 1 only shows
a count moving, and a count moving is not proof that a validator can now fail.

---

## Defect 1 — the extractor could not distinguish "broken" from "short"

Every failure path now names itself. `extract_description()` returns `(status, description)`,
and `status` is `"ok"` **only** when a non-empty string description was actually read:

| status | error type |
|---|---|
| `unreadable`, `no_frontmatter`, `no_yaml_module`, `unparseable_frontmatter`, `frontmatter_not_a_mapping`, `meta_not_a_mapping` | `agent_frontmatter_invalid` (ERROR) |
| `no_description_key`, `description_not_a_string`, `description_empty` | `agent_description_missing` (ERROR) |

Two properties worth naming:

1. **A non-ok file never reaches the budget or example checks.** Running them against `""`
   would re-manufacture the exact clean pass being removed. `description_tokens` is `null`
   for such a file, not a passing `0` — and a test asserts that specifically.
2. **`no_yaml_module` is an ERROR, not a silent skip.** Without PyYAML the description cannot
   be read at all; reporting that file as clean would be the same defect in a new costume.

---

## Defect 2 — the count, and the classifier that fixes it

`context/agents/` merely **collides on the word "agents"**. v3.12.0 treated every
`*/agents/*.md` as an agent; the fix classifies on **`meta:` presence** — the loader's own
contract, quoted from `docs/AGENT_AUTHORING.md:5`:

> "Agents ARE bundles. They use the same file format and are loaded via `load_bundle()`. The
> only difference is the frontmatter key (`meta:` vs `bundle:`)."

**This is a classifier, not an exclusion.** `EXCLUDED_DIRS` is untouched (the parity guard's
documented `{"docs"}` delta still holds), every candidate is still walked, and
`candidates_scanned` is still 32.

**Nothing is dropped silently** — 39z0's F4 property is preserved and extended: every
non-agent is reported by name and reason in `non_agents_found`, and the report step is now
*required* to print a NON-AGENTS table. A real agent that lost its `meta:` block appears in
that table instead of vanishing from the run.

---

## Deliverable: "the SAME classifier, not a copy" — how single-sourcing was achieved

**I reached the same conclusion `xe1u` did, for the same reason, and took the acceptable
substitute the goal names — then made it stronger than behavioural parity alone.**

A literally shared implementation is not available: recipe steps are self-contained heredocs
interpolated as strings, with no import mechanism between two `.yaml` recipes, and a Python
helper in `amplifier_foundation/` is explicitly allowed to be un-importable in the
third-party repos these recipes run against (`validate-agents`' own `structural-validation`
step documents that). Making the *scope* depend on an import allowed to be missing would
trade a visible under-count for an invisible one.

**So: the classifier block is copied BYTE-FOR-BYTE from `validate-agents.yaml` v1.7.0, and
that byte-identity is enforced by a test.** `_classifier_source()` extracts the span from
both recipes and compares them character for character
(`test_both_recipes_carry_the_same_classifier_source`). Two copies that must agree byte for
byte cannot drift into two different answers.

Textual identity is pinned **in addition to** behavioural parity, not instead of it:
`test_validate_bundle_repo_classifies_exactly_the_guards_agent_files` runs the recipe's real
step and compares both the **scanned candidate set** and the **classified agent set** against
the guard's `_agent_candidate_files()` / `_agent_files()`. The scan is checked separately on
purpose — 39z0's reasoning, kept: a classifier that quietly narrowed the walk would still
satisfy an agent-set comparison if the guard narrowed with it.

---

## Defect 3 — the Windows lesson, applied to ALL TEN Python steps

`39z0` proved the failure is **not** a path-separator problem: a repo path interpolated into
Python *source* is escape-processed, so `D:\a\<repo>\<repo>` becomes `D:\x07...`, does not
exist, and the walk silently returns nothing **with exit 0**.

**I checked, as the deliverable asks. Every one of this recipe's ten Python steps still had
the defect** — `packaging-check`, `module-dep-resolvability-check`, `build-check`,
`repo-discovery`, `behavior-hygiene-validation`, `behavior-reference-hygiene`,
`mode-validation`, `agent-description-validation`, `readme-install-convention-check`,
`body-instruction-check`. All ten now take the path from `$VALIDATE_BUNDLE_REPO_PATH`, which
has no escape semantics.

**This recipe is worse-affected than `validate-agents` was**, and that is why all ten were
fixed rather than only the one this item names: every step here carries `on_error: continue`,
so a path that silently resolved to nothing produced a *clean payload*, not a visible failure.

Two guards pin it — `test_no_validate_bundle_repo_step_interpolates_the_repo_path_into_source`
(the cause, across every step) and
`test_validate_bundle_repo_survives_a_repo_path_with_escape_sequences` (reproduced on every
platform with a directory literally named `a\test\nested`).

**A missing repo path is now an ERROR in the payload, deliberately NOT a non-zero exit.**
`validate-agents`' discovery exits 1; here that would be *wrong*, and this is the one place I
depart from 39z0's shape on purpose. This step is `on_error: continue`, and
quality-classification treats an unparseable payload as `{"passed": True, "skipped": True}` —
so exiting non-zero would **hide** a bad repo path behind a clean skip, reaching the same
vacuous PASS through a different door. The ERROR travels in the payload, where it reaches
`critical_count` and moves the verdict. `test_validate_bundle_repo_reports_a_missing_repo_path_as_an_error`
pins that.

---

## The cross-check — "one instance is a bug; the pattern is the finding"

Every extractor in this recipe, with a verdict:

| Extractor | Step | Verdict |
|---|---|---|
| `extract_description()` | `agent-description-validation` | **FIXED** — the item's subject |
| `extract_frontmatter_yaml()` | `behavior-reference-hygiene` | **FIXED — same defect, silent PASS** |
| `extract_frontmatter()` | `mode-validation` | **FIXED — same shape, silent DROP** |
| `parse_yaml_bundle()` | `standalone-completeness-validation` | **SAFE** — returns an explicit `{"error": "No YAML frontmatter found"}`; unchanged |
| `parse_yaml_section()` | tool-placement analysis | **SAFE** — extracts a *section*, and its callers already branch on emptiness; absence of a `tools:` section is a real, meaningful state, not a failed read |
| `get_bundle_details()` | `repo-discovery` | **SAFE** — carries an explicit `error` field on failure |

**`behavior-reference-hygiene` — the same defect, and it did falsely pass.** Every failure
returned a bare `{}`, and `{}` satisfies *both* of that step's checks vacuously:
`get_bundle_name({})` is `None`, `{}.get("includes", [])` is empty. A behavior file with
broken frontmatter counted toward `behaviors_checked` and reported clean. Now
`behavior_frontmatter_invalid` (ERROR), plus `root_bundle_frontmatter_invalid` for the root
bundle — because a root bundle that exists but cannot be read is *not* "no root bundle", and
the name-collision check silently degrades to a no-op when the root name is `None`.

**`mode-validation` — the same shape, different symptom: it dropped rather than passed.**
`if "mode" not in fm: continue` discarded an unreadable file, an absent frontmatter block and
an unparseable one alike, with nothing in the output to say so. A mode whose frontmatter broke
simply stopped being validated and `modes_checked` shrank without explanation. Now:
`mode_frontmatter_invalid` (ERROR) for a file that is plainly trying to be a mode, and
`non_modes_found` names every file classified out — 39z0's F4 mitigation, applied to modes.

**A fourth finding, not an extractor: a DEAD ERROR CHANNEL.** `quality-classification` read
`behavior_reference_hygiene.warnings` but **never its `errors`**. The step emitted none before
this version, so nothing was ever lost — but a validator with an unread error channel is one
`results["errors"].append(...)` away from losing a real finding silently, which is this
version's entire subject. Both channels are now read, at their own severities;
`behavior_reference_hygiene_issues` is filtered by severity everywhere it is counted, instead
of the old `# all are WARNING` assumption.

---

## Tests — nine new guards, all failing before

Added to `TestDiscoveryScopeParity` (**not deleted, not relaxed — 7 tests → 16**):

| test | what it pins |
|---|---|
| `test_both_recipes_carry_the_same_classifier_source` | byte-identity of the classifier across both recipes |
| `test_validate_bundle_repo_classifies_exactly_the_guards_agent_files` | scan **and** classify parity against the guard |
| `test_validate_bundle_repo_names_every_non_agent_it_excludes` | 39z0 F4 — no silent drops |
| `test_validate_bundle_repo_fails_an_agent_whose_description_cannot_be_read` | **the defect itself**, four broken agents, plus `description_tokens is None` |
| `test_validate_bundle_repo_fails_a_behavior_whose_frontmatter_cannot_be_read` | cross-check instance 2 |
| `test_validate_bundle_repo_never_drops_a_mode_file_silently` | cross-check instance 3 |
| `test_no_validate_bundle_repo_step_interpolates_the_repo_path_into_source` | the Windows defect at its cause, across **every** step |
| `test_validate_bundle_repo_survives_a_repo_path_with_escape_sequences` | the Windows defect reproduced on every platform |
| `test_validate_bundle_repo_reports_a_missing_repo_path_as_an_error` | never a clean skip over zero files |

**Fail-before / pass-after**, both recorded, both against the same test file:

- Against `origin/main`'s recipe: **9 failed, 7 passed** (`evidence/parity-guards-FAIL-BEFORE.txt`)
- Against this branch: **16 passed** (`evidence/parity-guards-PASS-AFTER.txt`)

Every new guard runs the recipe's **real step body**, never a re-implementation — a test that
reimplements the walk agrees with itself while the recipe drifts, which is the failure mode
being guarded.

---

## Suite

`uv sync --extra grpc-adapter && uv run pytest tests/ -q` → **1 failed, 1843 passed, 3 skipped**
(`evidence/full-suite.txt`).

The single failure is the **known pre-existing**
`tests/test_sources.py::TestFileSourceHandler::test_resolve_existing_file`, named in the item
as reproducing at `origin/main`. Independently confirmed as not mine:
`git diff origin/main -- tests/test_sources.py amplifier_foundation/sources` is **empty**.
39z0 pinned the cause (a `/tmp` symlink that `resolve()` collapses in this worktree's
environment) and recorded CI green on all ubuntu legs. The second known failure,
`test_grpc_adapter_main`, is among the 3 skipped here rather than failing.

`recipes(operation="validate")` on the edited recipe: **`status: valid`, no warnings.**

**Three tests in `tests/test_module_dep_resolvability_check.py` needed updating, and that is a
real consequence of my change, not a workaround:** its harness interpolated `{{repo_path}}`
into the step's source — the very defect being removed — so it now passes the path through the
environment, exercising the real mechanism. The interpreter assertion was widened to allow the
env prefix while still pinning `${AMPLIFIER_PYTHON:-python3}`, and the version pin moved to
`3.13.0`. **No assertion was weakened.**

`git status` is clean of `bundle.dot` / `bundle.png` — the `6phe` F1 regen side effect never
fired, because the full recipe was never executed (see *Spend*).

---

## Spend — $0.00, and the cap did not bind

**Nothing was purchased. Zero API spend, zero DTU, zero infrastructure rows** — so no
`infra_ledger.sh` registration and no teardown were required.

The item's authority is **`$0 for code and tests`, with recipe runs carved out**. Its stated
preference is to *"reproduce the counts deterministically by executing the recipe's own phases
with zero API spend, as 39z0 did"* — which is exactly what was done, and it was sufficient for
**every** deliverable. **No deliverable is NOT-POSSIBLE; there is no unspendable residue to
report and no arithmetic that fails to close.**

**Executed at $0.00:** four full replays of `agent-description-validation` (two recipe versions
× two repos), plus replays of `mode-validation`, `behavior-reference-hygiene`,
`body-instruction-check` and `repo-discovery`; a 26-heredoc compile sweep of the whole recipe;
a 9-test fail-before run; a 16-test pass-after run; a 1847-test suite run; and a recipe schema
validation.

**Deliberately NOT purchased:** a full paid `validate-bundle-repo` execution carrying its own
`run-id`. It was not needed — this item's deliverables are counts and verdicts from a
**deterministic** phase, all reproduced exactly. Not running it also avoided the `6phe` F1
`bundle.dot`/`bundle.png` regen side effect entirely, which is the safer outcome for a draft
PR. **39z0's open note still stands and I did not close it:** no lane has yet recorded a
measured dollar cost for a run of either recipe, so any future authority that *does* require a
paid run still cannot be sized from evidence. Capturing one remains the cheapest thing a
future lane could do.

---

## Deliverables

| # | deliverable | state |
|---|---|---|
| 1 | `extract_description()` distinguishes "frontmatter absent" from "description empty" and **fails** the file; a malformed-frontmatter agent is now detectable | **DONE** — measured on a 4-broken-agent fixture: 0 errors → 3 errors, `passed: True` → `False` |
| 2 | Fail-before: `agents_checked` **32 → 28** on the same repo state, `errors: []` preserved; both runs quoted | **DONE** — both quoted above and in `evidence/deterministic-before-after.txt`. `errors: []` holds; no newly-visible defect to leave unfixed |
| 3 | The **SAME** classifier as `validate-agents`, not a copy — or the reason, pinned by a parity test | **DONE** — byte-identical copy (engine has no import mechanism; `xe1u`'s reason re-reached), pinned by byte-identity **and** scan/classify parity in `TestDiscoveryScopeParity` |
| 4 | The Windows lesson applied — check for path-into-Python-source, route through the environment | **DONE** — found in **all ten** Python steps, all ten fixed, two guards; plus the `on_error: continue` interaction handled explicitly |
| 5 | A test that pins the new behaviour, failing against the current recipe | **DONE** — 9 new tests, **9 failed / 7 passed** against `origin/main`; **16 passed** here |
| 6 | *(OPTIONAL-IF-CAP-PERMITS)* cross-check the same pattern in other extractors | **DONE** — all six extractors adjudicated; 2 more instances fixed, 3 confirmed safe with reasons, plus a dead error channel found and closed |
| 7 | Suite green, DRAFT PR, do **not** merge | **DONE** — 1 known pre-existing failure, confirmed not mine; draft PR; not merged |
| 8 | DONE-NOTE at the lane artifact root, never the repo root | **DONE** — this file |

---

## Deviations and choices recorded

1. **Fixed all ten path interpolations, not only the one step this item names.** One root
   cause, ten call sites, and every step here carries `on_error: continue`, so a silent
   empty walk reads as a clean result rather than a failure. Leaving nine behind would have
   left the recipe able to report PASS over zero files.
2. **A missing repo path is an ERROR in the payload, not a non-zero exit** — the opposite of
   `validate-agents`' choice, and correct here for the reason given above. Documented in the
   step itself so the divergence is not mistaken for an oversight.
3. **Fixed the two other unsafe extractors rather than only listing them.** The item's
   acceptance criterion allows "listed with the reason it is safe"; neither was safe.
4. **Wired `behavior-reference-hygiene`'s errors into quality-classification.** The channel
   emitted nothing before this version, so this changes no existing verdict — but an unread
   error channel is the same silence in a different place.
5. **Did not purchase a paid recipe run.** The deterministic replay satisfied every
   deliverable, and skipping the full run also avoided the `bundle.dot`/`bundle.png` regen
   side effect. 39z0's per-run-cost gap is left open and named.
6. **Did not touch `validate-agents.yaml`.** Its classifier is the source of the byte-identical
   copy; editing it here would have made "which one is canonical" ambiguous.
7. **Did not fix any agent, behavior, mode or context document.** This lane fixes the checker.
   The repo produced zero new findings under the fixed checker, so there was nothing to leave
   unfixed — but the rule was applied, not merely unneeded.

---

## Files

```
recipes/validate-bundle-repo.yaml            v3.12.0 -> v3.13.0
    agent-description-validation             classifier + (status, description) extractor + env path
    behavior-reference-hygiene               (status, data) extractor + two new ERRORs + env path
    mode-validation                          (status, fm, content) extractor + non_modes_found + env path
    packaging-check, module-dep-resolvability-check, build-check,
    repo-discovery, behavior-hygiene-validation,
    readme-install-convention-check, body-instruction-check
                                             env path (Windows defect)
    quality-classification                   reads behavior-ref-hygiene errors; severity-filtered counts
    synthesize-report                        NON-AGENTS + NON-MODES tables required; new error types
tests/test_anchors_bundles_dry.py            TestDiscoveryScopeParity 7 -> 16 tests; _step_body/
                                             _python_step_bodies/_classifier_source/_run_repo_recipe_step
tests/test_module_dep_resolvability_check.py harness now passes the repo path via env; version pin 3.13.0
docs/lanes/dfni-validate-bundle-repo-count/
    DONE-NOTE.md                             this file
    replay_step.py                           $0.00 step-replay harness
    measure.py                               the two before/after measurements
    evidence/deterministic-before-after.txt
    evidence/parity-guards-FAIL-BEFORE.txt
    evidence/parity-guards-PASS-AFTER.txt
    evidence/full-suite.txt
```
