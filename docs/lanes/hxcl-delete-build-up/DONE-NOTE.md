# Lane `hxcl` — delete `experiments/build-up`, retarget the one live docstring

**Item:** `model_performance-hxcl` · **Branch:** `lane/hxcl-delete-build-up` ·
**Base:** `aaa5c47` (origin/main) · **Outcome:** **A — RESOLVED**, every deliverable DONE.

**Spend: $0.00.** No API calls, no DTU, no infrastructure created, nothing to tear down.
Both validation recipes were reproduced deterministically by executing their own step
bodies verbatim (the `39z0`/`dfni` technique). The cap did not bind, so branch B does not
apply.

---

## Deliverables

| # | Deliverable | State |
|---|---|---|
| 1 | `experiments/build-up/` deleted; `git grep` re-run **on the branch** | **DONE** (with a goal-defect finding — F1) |
| 2 | `_dataclass.py:52-56` docstring retargeted or rewritten, with reasoning | **DONE** — self-contained rewrite |
| 3 | Nothing else references it (pyproject force-includes, tests enumerating `experiments/`, `bundle.dot`) | **DONE** |
| 4 | Suite green + packaging builds | **DONE** — identical to baseline; wheel unaffected |
| 5 | Both validation recipes re-run on the branch, verdicts quoted | **DONE** — `agent_description_validation` **0 errors** |
| 6 | `bundle.dot` regenerated + committed *if the validator produces it* | **DONE — provably not needed; deliberately NOT touched** (F2) |
| 7 | Other four experiments untouched; `docs/lanes/**` untouched | **DONE** |
| 8 | Draft PR, never merged | **DONE** |

---

## 1. The deletion, and the reference check re-run on the branch

9 files removed (`git rm -r experiments/build-up`):

```
experiments/build-up/README.md
experiments/build-up/agents/{coder,explorer,planner,tester}.md
experiments/build-up/behaviors/build-up-foundation.yaml
experiments/build-up/build-up-foundation.md
experiments/build-up/context/{delegation-mechanics,system-base}.md
```

`git grep -n "build-up"` **on the branch**, repo-wide match lines: **108 → 63**.
Full output: `evidence/reference-check-AFTER.txt`. The non-`docs/lanes/` remainder:

```
experiments/minimal-delegate/behaviors/minimal-delegate-foundation.yaml:25:      0.2.0 — Refactored to mirror build-up v3 structure: thin pointer +
experiments/minimal-delegate/behaviors/minimal-delegate-foundation.yaml:108:# Agents available for delegation: broader pool than build-up (which ships
experiments/minimal-delegate/minimal-delegate-foundation.md:10:    Where build-up uses 4 purpose-built agents (explorer/planner/coder/tester),
modules/tool-delegate/tests/test_delegate_self_delegation_disabled.py:175:    file (e.g. delegation-mechanics.md in the build-up bundle), not in
tests/test_registry.py:394:        The concrete production failure: build-up:coder was loading foundation:explorer's
tests/test_registry.py:395:        tool list because source_base_paths['build-up'] pointed at the foundation checkout root
tests/test_registry.py:397:        experiments/build-up/ (where build-up:explorer.md lives at agents/explorer.md).
tests/test_registry.py:414:            # Sub-bundle directory — simulates experiments/build-up/
tests/test_registry.py:435:            # Sub-bundle main file — simulates build-up-foundation.md
tests/test_registry.py:452:            # Behavior YAML — simulates build-up-foundation.yaml with bundle.name: build-up.
```

`amplifier_foundation/**` is now clean — **zero** `build-up` matches in shipped source.

### F1 — FINDING: the goal under-counted the live references

> **The work item and GOAL.md both state the live references are (1) `_dataclass.py:52-56`
> and (2) `docs/lanes/`. That is wrong. There are 10 more match lines in 4 more files**,
> and they were already visible in `6phe`'s own evidence file
> (`docs/lanes/6phe-experiments-example-sweep/evidence/reference-check-git-grep.txt:75-84`)
> — the spec quoted a subset of a check that had already been run correctly.

Deliverable 1 as literally written ("returning **only** the historical `docs/lanes/`
evidence") is therefore **unreachable without violating this goal's own SCOPE-OUTs**.
Reported, not absorbed. Each remainder, and why it stays:

1. **`experiments/minimal-delegate/*` (3 lines)** — prose comparison, not a path
   dependency. Explicitly SCOPE-OUT: *"Do NOT touch the other four experiments"*.
   Editing them would break the stated scope to satisfy a mis-stated deliverable.
2. **`tests/test_registry.py` (6 lines)** — comments narrating the **historical
   production failure** the test regression-guards (`build-up:coder` loading
   `foundation:explorer`'s tool list). This is the same category the goal protects in
   `docs/lanes/`: *"they reference `build-up` because they describe the past"*. The test
   body itself builds its fixture in a `tempfile.TemporaryDirectory()` and never touches
   `experiments/` — verified; the suite passes with `build-up` gone.
3. **`modules/tool-delegate/tests/test_delegate_self_delegation_disabled.py:175`
   (1 line)** — one comment giving an illustrative example. Same category.

**None is a path dependency.** Nothing resolves, imports, globs, or loads
`experiments/build-up/`. Deliverable 1's *intent* — the deletion breaks nothing and
leaves no dangling reference — holds, and is proven by the green suite, the clean
packaging build, and both recipe verdicts below.

---

## 2. The docstring: **self-contained rewrite**, not a retarget

Full evaluation: `evidence/docstring-candidate-evaluation.txt`.

The docstring teaches exactly one thing — `namespace_root`: **a bundle YAML living in
`behaviors/` whose `agents/` sit one level above it.** A valid retarget needs *both*
halves. Measured, not assumed:

| Candidate (named in the item) | `behaviors/` ? | sibling `agents/` ? | uses `namespace_root` ? | Verdict |
|---|---|---|---|---|
| `experiments/exp-lean` | yes | **no** (`behaviors/` + `context/` only) | no | Does **not** have the shape |
| `bundles/amplifier-dev.yaml` | **no** (flat file at `bundles/`) | no | no | Does **not** have the shape |
| `bundles/anchors/` | no (`bundle.md` at dir root) | yes | **no — unnecessary there** | Would teach the *default*, not this field |
| `experiments/minimal-delegate` | yes | **no** (`context/` only) | yes | Wrong shape **and** SCOPE-OUT |

Exhaustive check — `git grep -n namespace_root -- '*.yaml' '*.yml' '*.md'` returns exactly
**two** users repo-wide: `experiments/build-up` (being deleted) and
`experiments/minimal-delegate`.

**So after this deletion, no surviving path in the repo has the shape the docstring
teaches.** Pointing at one that merely *exists* would teach the wrong thing — and pointing
at another deletable experiment would rebuild the exact fragility that created this item.
The example is therefore rewritten to depend on no directory at all:

```diff
-            Example — YAML at ``experiments/build-up/behaviors/build-up-foundation.yaml``
-            with agents at ``experiments/build-up/agents/``::
+            Example — a bundle whose YAML sits in a ``behaviors/`` sub-directory
+            while its resources live at the bundle root::
 
+                my-bundle/
+                  agents/                # resources live here ...
+                  context/
+                  behaviors/
+                    my-bundle.yaml       # ... but the YAML lives one level down
+
+                # inside behaviors/my-bundle.yaml
                 bundle:
-                  name: build-up
+                  name: my-bundle
                   namespace_root: ..   # agents/ lives one level above this file
```

The rewrite also **teaches more than the original did**: the old version named two paths
and left the reader to infer the layout; the new one draws it.

---

## 3. Nothing else references it

- **`pyproject.toml`** — no force-includes at all.
  `[tool.hatch.build.targets.wheel] packages = ["amplifier_foundation"]`; `experiments/`
  was never in the wheel.
- **Tests enumerating `experiments/`** — `test_anchors_bundles_dry.py`,
  `test_common_agent_base_md.py`, `test_bundle_to_dot.py`, `test_registry.py`. None
  hard-codes an agent count; the discovery-parity guards compare **sets**, dynamically.
  All pass (§4).
- **`bundle.dot`** — 0 occurrences of `build-up`, before and after. See F2.

---

## 4. Suite and packaging

Full output: `evidence/suite-and-packaging.txt`.

| | branch | baseline (`git worktree` at `origin/main`, same host/venv) |
|---|---|---|
| `uv run pytest -q` | `1 failed, 2032 passed, 3 skipped` | `1 failed, 2032 passed, 3 skipped` |
| the failure | `tests/test_sources.py::TestFileSourceHandler::test_resolve_existing_file` | **same test, same assertion** |

**Identical.** The one failure is pre-existing (`/tmp` vs `/tmp/tmpXXXX` `source_root`
resolution) and reproduces at `origin/main`. Not caused by this change.

> **Correction to the goal's KNOWN section:** it names *two* known pre-existing failures.
> The second —
> `tests/test_grpc_adapter_main.py::TestVerifyModuleType::test_non_isinstance_object_with_mount_passes`
> — **does not reproduce on this host.** It PASSES at `origin/main` and on the branch.
> Only one pre-existing failure exists here.

Packaging:

```
$ uv build
Successfully built dist/amplifier_foundation-1.0.0.tar.gz
Successfully built dist/amplifier_foundation-1.0.0-py3-none-any.whl

wheel entries: 78 | experiments entries: [] | build-up entries: []
top-level dirs: ['amplifier_foundation', 'amplifier_foundation-1.0.0.dist-info']
```

`ruff check`: 1 error (`amplifier_foundation/updates/__init__.py:34` unused `ParsedURI`) —
**pre-existing, identical at baseline**. Left unfixed per *"if a newly-visible finding
appears, REPORT it and leave it unfixed"* (and it is not even newly-visible).

---

## 5. Both validation recipes, re-run on the branch — $0.00

Full output: `evidence/validation-recipes-BEFORE-AFTER.txt`.
Drivers execute each step's own `command:` block **verbatim through bash**, exactly as the
recipe engine would — never re-implemented, because a re-implementation agrees with itself
while the recipe diverges (which is what `aaa5c47` was fixing).

### `validate-agents` v1.7.0 — **VERDICT: PASS WITH WARNINGS**

Driver: `docs/lanes/39z0-agent-classifier-scope/replay_deterministic_phases.py` (reused).

```
                            BEFORE (origin/main)                AFTER (branch)
candidates scanned    :     32                                  28
agents discovered     :     28                                  24
locations             :     5 {..., 'experiments/build-up/agents/': 4}
                                                                4 {'agents/': 16,
                                                                   'bundles/anchors-amp-dev/agents/': 1,
                                                                   'bundles/anchors/agents/': 6,
                                                                   'examples/agents/': 1}
classified non-agents :     4                                   4
structural errors     :     0                                   0
structural warnings   :     1                                   1
quality breakdown     :     {'total': 28, 'good': 27,           {'total': 24, 'good': 23,
                             'needs_work': 1, 'critical': 0}     'needs_work': 1, 'critical': 0}
VERDICT               :     PASS WITH WARNINGS                  PASS WITH WARNINGS
```

**24 agents, exactly as the goal predicted (28 − 4 `build-up` agents).** The single warning
is `examples/agents/file-responder.md` → `NO_TOOLS_SECTION`: a fixture demonstrating
capability inheritance, where adding `tools:` would destroy what it demonstrates. It is
**pre-existing and unchanged** — present in the BEFORE column too. Not caused by this
deletion, and deliberately left alone.

### `validate-bundle-repo` v3.13.0, `root_bundle_repo=true` — **VERDICT: PASS WITH SUGGESTIONS**

Driver: `replay_validate_bundle_repo.py` (written by this lane, committed alongside).
20 of 22 steps executed; the 2 skipped are `set-default-build-check` (condition false) and
`validate-recipes` (`type: recipe`, guarded by `validate_recipes: "false"`, whose $0
sibling `set-default-recipe-validation` supplies `recipe_validation`).

```
BEFORE (origin/main) and AFTER (branch) — BYTE-IDENTICAL:

bundles discovered          : {'root': 1, 'behaviors': 12, 'standalone': 6,
                               'experiments': 3, 'providers': 5, 'agents': 16, 'other': 0}
  agent_description_validation_results   errors=0  warnings=0     <-- the deliverable
  behavior_hygiene_results               errors=0  warnings=2
  behavior_reference_hygiene_results     errors=0  warnings=0
  body_instruction_check_results                   warnings=1
  context_sink_results                             warnings=0
  experiments_validation_results         errors=0  warnings=1
  module_dep_resolvability_check         errors=0  warnings=0
  readme_install_convention_results      errors=0  warnings=0
  standalone_completeness_results        errors=0  warnings=4
  tool_placement_results                           warnings=0
quality summary             : {"total": 43, "good": 43, "polish": 0, "needs_work": 0, "critical": 0}
quality_level               : polish
VERDICT                     : PASS WITH SUGGESTIONS
```

**`agent_description_validation`: 0 errors, 24 agents checked, 28 candidates scanned.**
This count is honest for the first time — `aaa5c47` fixed `extract_description()`, which
previously returned an empty string that satisfied every check. Both recipes now agree by
construction: **24 agents / 28 candidates / 4 non-agents**, on both sides.

---

## F2 — FINDING: `bundle.dot` must NOT be regenerated here

Evidence: `evidence/bundle-dot-freshness.txt`.

```
structural DOT, BEFORE (origin/main)  : 18061 chars
structural DOT, AFTER  (branch)       : 18061 chars
path-normalised byte-identical        : True
'build-up' present in regenerated DOT : False
'build-up' present in committed dot   : False
committed bundle.dot == raw structural: False
```

`build-up` was **never** in the DOT: `bundle_to_dot.py:126-136` collects
`experiments/*.yaml`, `experiments/*.md`, and `experiments/*/bundle.md` — and
`experiments/build-up/` had no root `bundle.md` (its entry point was
`build-up-foundation.md`). `validate-bundle-repo`'s own `repo-discovery` agrees:
`experiments: 3` on both sides.

So regenerating would land the **un-enhanced structural DOT over the committed
human-readable (LLM-enhanced) one** — precisely `6phe` finding F1
(`bundle-overview-regen-write` rewrites unconditionally; only its *input* is gated on
`enhance_diagrams`). The replay driver **stops at `quality-classification`** for that
reason, and `git status` was checked after every recipe run:
**no unintended `bundle.dot`/`bundle.png` drift.**

---

## Spend

| Item | Cost |
|---|---|
| API / model calls | **$0.00** — no LLM step executed |
| DTU / infrastructure | **$0.00** — none created; nothing registered in the ledger; nothing to tear down |
| **Total** | **$0.00** |

The goal's authority for this item is *"$0 for code and tests; recipe runs are CARVED OUT
and recorded, not capped at zero."* No recipe run was bought — both verdicts were
reproduced deterministically at zero spend, which is the goal's own stated preference.
**The cap never bound**, so no deliverable is NOT-POSSIBLE and branch B does not apply.
No arithmetic was required because nothing purchasable was purchased.

## Deviations

1. **Deliverable 1's literal wording is unsatisfiable** under this goal's own SCOPE-OUTs —
   reported as **F1** above rather than absorbed by editing scoped-out files.
2. **`bundle.dot` deliberately not regenerated** — proven unnecessary, and regenerating
   would have caused a regression. Reported as **F2**.
3. **The goal's second "known pre-existing failure" does not reproduce on this host** —
   corrected in §4 rather than restated.
