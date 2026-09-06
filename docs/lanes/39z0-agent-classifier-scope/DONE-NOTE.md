# Lane 39z0 — validate-agents: classify by `meta:`, not by directory name; and stop losing the repo path on Windows

**Item:** `model_performance-39z0` (project `model_performance`)
**Branch:** `lane/39z0-agent-classifier-scope`, branched from `lane/xe1u-validate-agents-discovery` (head `038801f`, draft PR #364)
**Outcome:** **A — RESOLVED**, with one sub-deliverable recorded **NOT-POSSIBLE at the cap** (branch B applies to that clause only; see *Spend* below).
**Recipe:** `recipes/validate-agents.yaml` v1.6.0 → **v1.7.0**

---

## Headline

Both defects are fixed. **The FAIL is gone.** The verdict does **not** return to bare
`PASS` — it lands on **`⚠️ PASS WITH WARNINGS`** — and per the item's own instruction that
is the headline finding, named and **left unfixed**:

> `examples/agents/file-responder.md` — `NO_TOOLS_SECTION` (no explicit `tools:` section,
> relies on inheritance). One WARNING, zero ERRORs.

This is a **genuinely new finding surfaced by correct discovery**, and it is exactly the
file the item's KNOWN section flags as *"a fixture demonstrating capability inheritance"*.
Adding a `tools:` block to it would destroy what the fixture demonstrates, so it is
**reported, not fixed here, and not suppressed**. No threshold, severity, skip rule or
exclusion set was touched to soften it.

---

## The three measurements

All numbers below come from executing the recipes' own **deterministic** steps
(`environment-check → agent-discovery → structural-validation → quality-classification`)
verbatim through bash, at **$0.00 API spend** — see *Spend* for why, and
`replay_deterministic_phases.py` for the harness. `quality_classification.quality_level`
**is** the verdict: the recipe's own Verdict Selection maps
`good → PASS`, `polish → PASS WITH SUGGESTIONS`, `needs_work → PASS WITH WARNINGS`,
`critical → FAIL`. The LLM report step renders that decision; it does not make it.

| | recipe | scanned | agents | non-agents | ERR | WARN | `quality_level` | **verdict** |
|---|---|---|---|---|---|---|---|---|
| baseline (`5a9e07b`, pre-xe1u) | v1.5.1 | — | **23** | — | 0 | 0 | `good` | **PASS** |
| before (`038801f`, xe1u head) | v1.6.0 | — | **32** | — | **4** | 1 | `critical` | **FAIL** |
| **after (this branch)** | **v1.7.0** | **32** | **28** | **4** | **0** | 1 | `needs_work` | **⚠️ PASS WITH WARNINGS** |

**The true agent count is 28.** Measured, not assumed: 32 files match the scan, 4 of them
declare no `meta:` and are not agents.

**The replay harness is validated against xe1u's two paid runs.** It reproduces
`run-c26b396f2f1e` (23 agents / PASS) and `run-67388a546d75` (32 agents / FAIL) exactly —
the same counts, the same four `NO_FRONTMATTER` errors, the same verdicts. That agreement
is what licenses the third row as a real measurement rather than an argument.

Evidence: `evidence/deterministic-before-after.txt`,
`evidence/deterministic-baseline-v1.5.1.txt`.

---

## Defect 1 — classification by DIRECTORY NAME

v1.6.0 treated every `agents/*.md` as an agent. `context/agents/` merely **collides on the
word "agents"**: its four files are context documents loaded via `context:` by
`behaviors/agents.yaml:44-45` and `behaviors/tasks.yaml:18-19`. Nothing spawns them, and
they carry no frontmatter **because they are not agents** — so they produced four
`NO_FRONTMATTER` ERRORs and flipped the verdict on a repo with zero real agent defects.

**Fix: classify on `meta:` presence — the loader's own contract**, quoted from this repo's
`docs/AGENT_AUTHORING.md:5`:

> "Agents ARE bundles. They use the same file format and are loaded via `load_bundle()`.
> The only difference is the frontmatter key (`meta:` vs `bundle:`)."

Three properties this fix deliberately has:

1. **It is a classifier, not an exclusion.** `AGENT_SCAN_EXCLUDED_PARTS` is untouched, no
   second exclusion list exists, and **every candidate is still walked**. `candidates_scanned`
   is still 32.
2. **Nothing is dropped silently.** Every non-agent is reported by name and reason in the
   new `non_agents_found` / `non_agent_reasons`, and both the report and the quick-approval
   path are now *required* to print a NON-AGENTS table. A classifier that quietly discarded
   files would reintroduce, in a new place, the exact silence that let 11 `<example>`
   violators survive `#341` and `ux32`. **A real agent that lost its `meta:` block appears
   in that table instead of vanishing from the run.**
3. **Frontmatter that exists but will not parse still counts as an agent**, so Phase 2 gets
   to report the YAML error rather than the file being reclassified out of the run.

---

## Defect 2 — the recipe found NOTHING on Windows (the more dangerous one)

**Cause, pinned:** every Python heredoc wrote `Path("{{repo_path}}")` — a filesystem path
placed **inside a Python string literal**, where each backslash becomes an escape sequence.
A GitHub Actions Windows checkout lives at `D:\a\<repo>\<repo>`, so `\a` was parsed as
`\x07`.

Reproduced on POSIX with a directory literally named `a\test\amplifier-foundation`
(`evidence/windows-path-repro.txt`), running each recipe version's own discovery body:

```
REPO PATH ON DISK : '/tmp/…/a\\test\\amplifier-foundation'   EXISTS: True
AGENT FILES ON DISK: ['agents/probe.md', 'context/agents/notes.md']

--- v1.6.0 (038801f) — path interpolated into Python source ---
exit code: 0
agents_found : []
errors       : [{'type': 'path_error', 'message': "Repository path does not exist: /tmp/…/a\test\x07mplifier-foundation"}]

--- v1.7.0 (this branch) — path via VALIDATE_AGENTS_REPO_PATH ---
exit code: 0
agents_found     : ['agents/probe.md']
non_agents_found : ['context/agents/notes.md']
errors           : []
```

Note the v1.6.0 line: **`agents_found: []` with exit code 0.** Discovery printed a valid,
empty payload and succeeded. Structural validation then saw 0 agents and 0 errors, quality
classification called that `good`, and the run reached the quick-approval path and reported
**PASS having validated nothing** — strictly worse than the 23-of-32 under-count v1.6.0
replaced, because the under-count at least checked 23 files.

**Fix, two parts:**

1. The repo path arrives through the **environment** (`VALIDATE_AGENTS_REPO_PATH`), which
   has no escape semantics, in all three affected steps — `environment-check`,
   `agent-discovery`, `structural-validation`. (The third mattered too:
   `get_bundle_level_tools("{{repo_path}}")` would have silently found no bundle-level
   tools on Windows once discovery started working, producing false WARNINGs.)
2. Discovery now **exits non-zero** on a missing repo path instead of printing an empty
   payload and exiting 0, so no future path defect can reach a vacuous PASS quietly. A bad
   repo path is a run-ending condition, not a finding about the repo. A repo that genuinely
   contains no agents is unchanged — still a non-fatal `no_agents` entry.

**Windows parity is therefore structural, not incidental**: the recipe and the guard reach
the same file set on Windows for the same reason they do on POSIX — neither of them puts a
path through Python's escape parser.

---

## The parity guard SURVIVED and got stronger

`TestDiscoveryScopeParity` is what caught the Windows defect, on Windows 3.11/3.12/3.13,
while every POSIX leg stayed green. **Nothing was deleted or relaxed.** It went from 2 tests
to 6:

| test | what it pins |
|---|---|
| `test_exclusion_sets_agree_across_both_recipes_and_this_guard` | unchanged — one exclusion set, three consumers |
| `test_validate_agents_scans_exactly_the_guards_candidate_files` | **new** — the SCAN reaches the same files, before either side classifies |
| `test_validate_agents_discovers_exactly_the_guards_agent_files` | kept — the CLASSIFIER judges the same subset agents; now also asserts the set is non-empty |
| `test_classifier_keys_on_meta_not_on_the_directory_name` | **new** — `context/agents/*.md` are non-agents AND are named as such |
| `test_discovery_never_interpolates_the_repo_path_into_python_source` | **new** — the Windows defect at its CAUSE |
| `test_discovery_survives_a_repo_path_containing_escape_sequences` | **new** — the Windows defect reproduced on every platform |
| `test_discovery_fails_loudly_on_a_missing_repo_path` | **new** — never an empty payload with exit 0 |

The scan/classify split is deliberate and is the opposite of a relaxation: **a classifier
that quietly narrowed the walk would still satisfy an agent-set comparison if the guard
narrowed with it.** Pinning the candidate set separately makes that unreachable.

**Fail-before / pass-after**, both recorded:

- Against v1.6.0's recipe: **6 failed, 1 passed** (`evidence/parity-guards-FAIL-BEFORE.txt`)
- Against this branch: **13 passed** (`evidence/parity-guards-PASS-AFTER.txt`)

---

## CI — all six legs green, including the three Windows ones

PR #365, run `34057350037` (`evidence/ci-checks.txt`):

```
Tests (ubuntu-latest,  Python 3.11)   pass
Tests (ubuntu-latest,  Python 3.12)   pass
Tests (ubuntu-latest,  Python 3.13)   pass
Tests (windows-latest, Python 3.11)   pass     <-- red on xe1u's #364
Tests (windows-latest, Python 3.12)   pass     <-- red on xe1u's #364
Tests (windows-latest, Python 3.13)   pass     <-- red on xe1u's #364
license/cla                           pass
```

**This is the Windows deliverable proved end to end**, on the real platform, by the same
`TestDiscoveryScopeParity` that failed there with `assert set() == {...}` on #364.

It also settles the local test failure below: **the ubuntu legs are fully green**, so
`test_sources.py::TestFileSourceHandler::test_resolve_existing_file` fails only in this
worktree's environment (a `/tmp` symlink that `resolve()` collapses), not in CI and not
because of this branch.

## Suite

`uv sync --extra grpc-adapter && uv run pytest tests/ -q` → **1 failed, 1834 passed, 3 skipped**
(`evidence/full-suite.txt`).

The single failure is the **known pre-existing** `tests/test_sources.py::TestFileSourceHandler::test_resolve_existing_file`,
named in the item as reproducing at `origin/main`. Independently confirmed as not mine:
`git diff origin/main -- tests/test_sources.py amplifier_foundation/sources` is empty.
(The second known failure, `test_grpc_adapter_main`, is among the 3 skipped in this
environment rather than failing.)

`recipes(operation="validate")` on the edited recipe: `status: valid`, no warnings.

`git status` after all work is clean of `bundle.dot` / `bundle.png` — the `6phe` F1 regen
side effect never fired, because `validate-bundle-repo` was not executed (see *Spend*).

---

## Spend — $0.00 of a $0.00 authority

**Nothing was purchased. Zero API spend, zero DTU, zero infrastructure rows.** The item's
authority is `0 runs x 0 arms x $0 / 1.00 = $0.00`, slack `$0.00`.

**One sub-deliverable is NOT-POSSIBLE at that cap.** Leading with what *was* executed:

> **Executed at $0.00:** three full deterministic replays of `validate-agents` (v1.5.1,
> v1.6.0, v1.7.0) over 32 candidate files each — 12 deterministic step executions in total,
> yielding discovered counts, per-location counts, every structural ERROR and WARNING, and
> the `quality_level` that *is* the verdict; plus a two-version Windows-path reproduction,
> a 6-test fail-before run, a 13-test pass-after run, a 1837-test suite run, and a recipe
> schema validation. **Reproduced xe1u's `run-c26b396f2f1e` (23/PASS) and
> `run-67388a546d75` (32/FAIL) exactly.**
>
> **NOT purchased:** a *new paid recipe execution* carrying its own `run-id`. A full
> `validate-agents` run fires LLM steps (3 here, since `requires_llm_analysis` is `True`
> while the `file-responder` WARNING stands), and `validate-bundle-repo` more still. The
> smallest indivisible purchase that would advance this clause is **one** `validate-agents`
> execution; **$0.00 cannot buy it, and the residue is $0.00 — there is no unspendable
> remainder to report.**

**This is a defect in the goal's authority, not a failure of the lane, and it was knowable
on first read**: the deliverable *"both recipes re-run … with run ids"* requires purchased
runs, while the arithmetic authorising it is `0 runs x $0`. The two clauses cannot both
hold. Per the item's own instruction I said so **before** spending rather than after.

**The authority that WOULD close it:** `2 recipes x 1 run x <measured per-run $> / 1.00 valid`.
I deliberately do not invent the per-run figure — **no prior lane recorded a dollar cost for
these runs** (`xe1u` and `6phe` both quote run ids and step counts, never dollars), so the
figure needed to size this authority does not exist in evidence yet. `xe1u`'s F4 — a FAILing
repo costs 3 LLM steps where a PASSing one costs 2 — is the only cost signal on record.
**Capturing a measured per-run cost is the cheapest thing the next lane could do to make
every future authority here sizeable from evidence.** With the classifier fix in place the
run is now the cheaper 3-step shape rather than v1.6.0's failing shape.

---

## Deliverables

| # | deliverable | state |
|---|---|---|
| 1 | Classifier keys on `meta:`, not directory name; `context/agents/*.md` correctly not-an-agent; **true agent count stated (28, measured)** | **DONE** |
| 2 | Verdict returns to PASS with no threshold/severity/skip/exclusion change | **DONE with the finding reported** — FAIL eliminated (4 ERRORs → 0); lands on `⚠️ PASS WITH WARNINGS`, not bare PASS. Failing agent named: `examples/agents/file-responder.md` (`NO_TOOLS_SECTION`). **Left unfixed.** Nothing weakened. |
| 3 | Windows parity — same file set as POSIX and as the guard, never empty, never a vacuous PASS; **CI green on all three Windows Python versions** | **DONE** — cause fixed (no path in Python source), reproduced and regression-tested on every platform, plus a loud exit on a bad path. **CI run `34057350037`: windows-latest 3.11 / 3.12 / 3.13 all pass**, where #364 was red. |
| 4 | Both recipes re-run, before/after counts and verdicts quoted with run ids | **PARTIAL — see Spend.** Counts and verdicts: **DONE**, reproduced against both prior run ids exactly. A **new** paid `run-id`: **NOT-POSSIBLE at the $0.00 cap.** |
| 5 | The parity guard survives | **DONE** — not deleted, not relaxed; 2 tests → 6, strictly stronger |
| 6 | Any genuinely new finding REPORTED, not fixed and not suppressed | **DONE** — `examples/agents/file-responder.md` `NO_TOOLS_SECTION`, reported above, left unfixed |
| 7 | Suite green, DRAFT PR, do not merge | **DONE** — CI fully green (6/6 legs); the one local failure is environment-specific, not in CI. Draft PR #365; **not merged** |
| 8 | DONE-NOTE at the lane artifact root, never the repo root | **DONE** — this file |

## Deviations and choices recorded

1. **Did not spend to obtain a new run id.** Chosen over exceeding a $0.00 authority.
   Recorded as the finding above.
2. **Fixed the path interpolation in all three affected steps**, not only
   `agent-discovery`. One root cause, three call sites; leaving
   `get_bundle_level_tools("{{repo_path}}")` behind would have produced false WARNINGs on
   Windows the moment discovery started working.
3. **Made discovery exit non-zero on a missing repo path.** This is a discovery guard, not
   a quality threshold — no check, severity or skip rule changed. A repo that genuinely
   contains no agents behaves exactly as before.
4. **Left `validate-bundle-repo.yaml` untouched**, including its own classification of the
   same four files — that is `model_performance-dfni`, sequenced after this item precisely
   because its count depends on this classifier's answer. Not running it also avoided its
   `bundle.dot`/`bundle.png` regen side effect (`6phe` F1) entirely.
5. **Narrowed the guard's `_agent_files()` to classified agents** and introduced
   `_agent_candidate_files()` for the scan. The repo-wide `<example>` guard now runs over
   real agents only; the four context documents have no `meta.description` for it to
   inspect, so its coverage is unchanged in substance.

## Files

```
recipes/validate-agents.yaml         v1.6.0 -> v1.7.0 (classifier + env-var path + report requirements)
tests/test_anchors_bundles_dry.py    _agent_candidate_files/_is_agent_file split; parity guard 2 -> 6 tests
docs/lanes/39z0-agent-classifier-scope/
    DONE-NOTE.md                     this file
    replay_deterministic_phases.py   the $0.00 verdict harness
    evidence/deterministic-before-after.txt
    evidence/deterministic-baseline-v1.5.1.txt
    evidence/windows-path-repro.txt
    evidence/parity-guards-FAIL-BEFORE.txt
    evidence/parity-guards-PASS-AFTER.txt
    evidence/full-suite.txt
    evidence/ci-checks.txt
```
