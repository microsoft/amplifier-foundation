# Both validation recipes, re-run on the branch

Run 2026-09-06 on `lane/6phe-experiments-example-sweep` at `f2b0708`.

**Recipe-file provenance (checked, not assumed):** the runner resolved
`@foundation:recipes/*.yaml` out of
`~/.amplifier/cache/amplifier-foundation-c909465861f9d6ce/recipes/`, not out of
this checkout. Both files are byte-identical to the branch's own copies, so the
run exercised the same recipe text this PR ships:

```
$ cmp -s recipes/validate-agents.yaml      <cache>/recipes/validate-agents.yaml      && echo BYTE-IDENTICAL
BYTE-IDENTICAL: branch recipes/validate-agents.yaml == cache copy the runner used
$ cmp -s recipes/validate-bundle-repo.yaml <cache>/recipes/validate-bundle-repo.yaml && echo BYTE-IDENTICAL
BYTE-IDENTICAL: branch recipes/validate-bundle-repo.yaml == cache copy
```

`repo_path` pointed at this branch checkout in both runs, so the *target* was
the branch tree either way.

---

## 1. `validate-agents.yaml` v1.5.1 -- stays PASS

`run_id: run-aed6a9fb36a9`, `session_id: 66d624e6196944f1-20260906-115513_recipe`,
`status: completed`.

Quoted from `quality_classification`:

```json
"summary": {"critical": 0, "good": 23, "needs_work": 0, "polish": 0, "total": 23},
"message": "All 23 agents meet quality thresholds. No further analysis needed.",
"quality_level": "good",
"requires_llm_analysis": false
```

Quoted from the recipe's own final report:

```
- **Overall Verdict**: PASS
- **Agents Found**: 23 total (16 in `agents/`, 6 in `bundles/anchors/agents/`,
  1 in `bundles/anchors-amp-dev/agents/`)
- **Quality Breakdown**: 23 good, 0 polish, 0 needs_work, 0 critical
- **Issues**: 0 errors, 0 warnings, 2 optional suggestions
```

and its own scope note, which is the whole point of this item:

```
- **Scope note**: this validator discovers 23 agents across `agents/`,
  `bundles/anchors/agents/`, and `bundles/anchors-amp-dev/agents/`. It does
  **not** discover `experiments/` -- `validate-bundle-repo` covers the wider
  scope.
```

The two remaining suggestions are LOW/optional and pre-date this change
(`modular-builder` has no strong trigger verb; three short-but-valid
descriptions). Neither is an error and neither is in this lane's scope.

---

## 2. `validate-bundle-repo.yaml` v3.12.0 -- `agent_description_validation`: 0 errors

`run_id: run-864d8d1c009a`, `session_id: b76b3e6d384542b6-20260906-115631_recipe`,
`status: completed`. Context: `enhance_diagrams: "false"` (see the side-effect
finding below).

Quoted verbatim from `agent_description_validation_results`:

```json
{
  "phase": "agent_description_validation",
  "agents_checked": 32,
  "errors": [],
  "warnings": [],
  "passed": true,
  "summary": {"agents_checked": 32, "errors": 0, "warnings": 0}
}
```

Every one of the 32 `agent_details` rows carries `"example_count": 0`,
`"commentary_count": 0`, `"issues": []` -- including the four that were
violators before this change:

```json
{"commentary_count": 0, "description_tokens": 159, "example_count": 0, "file": "experiments/build-up/agents/coder.md",    "issues": []},
{"commentary_count": 0, "description_tokens": 250, "example_count": 0, "file": "experiments/build-up/agents/explorer.md", "issues": []},
{"commentary_count": 0, "description_tokens": 262, "example_count": 0, "file": "experiments/build-up/agents/planner.md",  "issues": []},
{"commentary_count": 0, "description_tokens": 151, "example_count": 0, "file": "experiments/build-up/agents/tester.md",   "issues": []}
```

**Before this change the same phase reported 45 agents checked and raised
`example_block_present` (ERROR) on 11 of them.** 45 - 11 deleted-or-swept
`experiments/behavioral-anchor-amplifier-dev` agents (7 files, deleted) =
38; minus the 6 `experiments/behavioral-anchor` agents (also deleted) = 32.
The four `build-up` agents survive in the count because they were kept and
stripped, not deleted.

`yaml_structure_lint` also stayed clean across the deletion:
`"bundles_checked": 43, "errors_total": 0, "passed": true`.

---

## Side-effect finding: this recipe WRITES to the repo

`validate-bundle-repo`'s `bundle-overview-regen-write` step
(`recipes/validate-bundle-repo.yaml:4944`, writes at :4992 and :5004) rewrites
`bundle.dot` and `bundle.png` **unconditionally** -- it is not gated on
`enhance_diagrams`. Only its *input* is: with `enhance_diagrams: "false"` the
LLM-enhancement step is skipped and a `set-default-bundle-overview-enhanced-dot`
step supplies an empty default, so the write lands the **un-enhanced structural
DOT** over the committed, human-readable one.

Observed here: 82 insertions / 80 deletions in `bundle.dot` and a 914KB -> 690KB
`bundle.png`, all of it label degradation, e.g.

```
-    label="Foundation Bundle (v2.1.2)\nThe shared starter kit of AI helpers, safe habits, and toolkit connections"
+    label="foundation v2.1.2 — bundle repo"
```

Both files were reverted with `git checkout --` and are NOT part of this PR.
`bundle.dot` never referenced the deleted trees (`grep -c behavioral-anchor
bundle.dot` -> `0`), so the deletion introduced no staleness that a regeneration
would have needed to fix.

This is worth naming rather than absorbing: **running the repo validator with
`enhance_diagrams: "false"` silently degrades two committed artifacts.** A
caller who runs it and then commits gets a worse `bundle.dot` with no warning.
Not fixed here -- out of this item's scope, and fixing a recipe's write
behaviour is a separate change with its own blast radius.
