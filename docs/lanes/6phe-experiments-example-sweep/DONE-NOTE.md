# Lane 6phe -- `experiments/` example sweep + repo-wide guards

**Item:** `model_performance-6phe` (project `model_performance`)
**Branch:** `lane/6phe-experiments-example-sweep` off `origin/main` @ `f1a3781`
**Outcome:** **A. RESOLVED** -- every deliverable DONE. Nothing was cut by the cap.
**Landing stage:** draft PR only. Per the goal's LANDING STAGE clause, "the live
system now behaves X" is satisfied here as "X is demonstrated fail-before /
pass-after and shipped for landing"; the merge is the manager's next stage.

---

## Deliverables

| # | Deliverable | State |
|---|---|---|
| 1 | `git grep` reference check quoted in full, before any deletion | **DONE** |
| 2 | `experiments/behavioral-anchor{,-amplifier-dev}/` deleted, index updated | **DONE** |
| 3 | `experiments/build-up/agents/*` `<example>` blocks stripped (not deleted) | **DONE** |
| 4 | Guard (c) extended to the whole repo except `test-fixtures/`/`docs/` | **DONE** |
| 5 | New guard (d'): one copy of each ecosystem doc repo-wide incl. `experiments/` | **DONE** |
| 6 | Both validation recipes re-run, verdicts quoted | **DONE** |
| 7 | The surviving emoji footer at `dev-workflows.md:155` | **DONE** (see below) |
| 8 | Suite green modulo the two known pre-existing failures | **DONE** |
| 9 | Draft PR, not merged | **DONE** |

---

### 1. The reference check (gate before any deletion)

Full verbatim command output:
[`evidence/reference-check-git-grep.txt`](evidence/reference-check-git-grep.txt).

Three findings drove everything else:

- **`recipes/` and `behaviors/` returned ZERO matches** for `behavioral-anchor`.
  Nothing composed at runtime referenced either tree.
- **The only live reference was one provenance sentence**,
  `bundles/anchors/README.md:100` ("Promoted from `experiments/behavioral-anchor`
  to a published bundle"). Per the goal, the index was *updated to point at
  `bundles/` and the promoting commits* (70a84d0 #259, 78d0abe #273) rather than
  deleted blindly. It now also names
  `git log --diff-filter=D -- experiments/behavioral-anchor` so a reader can
  reach the originals.
- **`experiments/README.md` DOES NOT EXIST.** That is decisive for build-up
  (below), and it means there was no index of experiments to update.

`docs/lanes/*/DONE-NOTE.md` hits are frozen lane records that quote the
violation *as evidence*; rewriting them would destroy the record. Left untouched
by design, and that is why `docs/` is in guard (c)'s exclusion set.

### 2. `experiments/behavioral-anchor{,-amplifier-dev}/` -- deleted

22 files, 1535 deletions. These were the pre-promotion originals of
`bundles/anchors` and `bundles/anchors-amp-dev`: 7 agents carrying `<example>`
blocks, a `context/system.md` still carrying the OLD 4-principle text including
the deleted "Delegate complex work", a THIRD copy of the three ecosystem docs
still carrying the "Always delegate" line, and two READMEs. Git history
preserves them.

### 3. `experiments/build-up/` -- KEPT, examples stripped

The goal authorises deletion **only** on evidence from `experiments/README.md`.
**I cannot quote that line, because the file does not exist:**

```
$ ls experiments/README.md
(experiments/README.md DOES NOT EXIST)
```

So deletion was not authorised, and stripping is the correct branch. It would
have been wrong anyway: `build-up` is referenced LIVE from outside its own tree
by `amplifier_foundation/bundle/_dataclass.py:52-56` (a docstring example),
`tests/test_registry.py:394-452` (a regression test named after a concrete
production failure, `build-up:coder` loading `foundation:explorer`'s tool list),
`modules/tool-delegate/tests/test_delegate_self_delegation_disabled.py:175`, and
`experiments/minimal-delegate/`. Four `<example>` sections removed across
`coder.md`, `explorer.md`, `planner.md`, `tester.md`; each description keeps its
`USE WHEN` / `DO NOT USE WHEN` decision rules, which is where V6 says the
trigger belongs.

### 4 + 5. The guards -- fail on main, pass on the branch

`tests/test_anchors_bundles_dry.py`.

**FAIL-BEFORE** ([`evidence/guard-tests-FAIL-BEFORE.txt`](evidence/guard-tests-FAIL-BEFORE.txt))
-- guards written first, run against the un-swept tree: `2 failed, 4 passed`.

Guard (c) named all **11** violators, exactly the set the item predicted:

```
AssertionError: Agent descriptions must contain no <example>/<commentary> blocks
(description-authoring-principles.md V3, #340). Offenders:
experiments/behavioral-anchor-amplifier-dev/agents/amplifier-dev-expert.md (<example>);
... architect.md; builder.md; debugger.md; explorer.md; git-ops.md; researcher.md;
experiments/build-up/agents/coder.md; explorer.md; planner.md; tester.md
```

Guard (d'):

```
AssertionError: Expected exactly one copy of ecosystem-map.md anywhere in the
repo, found 2: ['context/amplifier-dev/ecosystem-map.md',
'experiments/behavioral-anchor-amplifier-dev/context/amplifier-dev/ecosystem-map.md']
```

**PASS-AFTER** ([`evidence/guard-tests-PASS-AFTER.txt`](evidence/guard-tests-PASS-AFTER.txt)):
`6 passed in 0.14s`.

**Guard (c) is scoped to the validator's own scope, proven rather than asserted.**
`_agent_files()` now globs `rglob("*/agents/*.md") + glob("agents/*.md")` and
drops the validator's own excluded parts (`test-fixtures`, `tests`,
`node_modules`, `.git`, `.venv`) plus `docs`.
[`evidence/scope-parity.txt`](evidence/scope-parity.txt) runs the recipe's
discovery code and the guard's side by side:

```
validator scope (validate-bundle-repo agent-description-validation): 32 agents
guard (c) scope (_agent_files):                                      32 agents
identical sets: True
in validator not guard: []
in guard not validator: []
```

Guard (d') deliberately does NOT inherit ux32's `experiments/` exclusion. ux32's
guard (d) excluded `experiments/` and merely *named* the third copy in its
failure message -- which is precisely why that copy survived. Frozen is not a
safety property: a stale copy is still the second answer a `grep` returns.

### 6. Both recipes re-run

[`evidence/validation-recipes-AFTER.md`](evidence/validation-recipes-AFTER.md)
-- verdicts quoted verbatim, plus a provenance check that the recipe files the
runner actually loaded (from the foundation cache) are byte-identical to this
branch's copies.

- `validate-agents` v1.5.1: **PASS**, 23 agents, `0 errors, 0 warnings`,
  `"requires_llm_analysis": false`.
- `validate-bundle-repo` v3.12.0 `agent_description_validation`:
  `"agents_checked": 32, "errors": [], "passed": true`. Every row
  `"example_count": 0`.

### 7. The emoji footer, joined up with lane `ezze`

`ezze` (`f1a3781`) named -- rather than swept -- the surviving emoji footer at
`experiments/behavioral-anchor-amplifier-dev/context/amplifier-dev/dev-workflows.md:155`,
because that directory belonged to this lane.

**It is resolved by deletion.** The file no longer exists; the canonical copy at
`context/amplifier-dev/dev-workflows.md` was already swept by `ezze`. Nothing is
left for a follow-up lane to sweep, and the two lanes' records join here.

### 8. Test suite

[`evidence/full-suite-AFTER.txt`](evidence/full-suite-AFTER.txt):
`1 failed, 2016 passed, 3 skipped in 22.99s`.

The single failure is `tests/test_sources.py::TestFileSourceHandler::test_resolve_existing_file`
-- one of the two documented pre-existing failures, **not absorbed on trust**. I
reproduced it on a detached worktree at `origin/main` on this host:

```
$ git worktree add --detach /tmp/6phe-main-check origin/main
$ pytest tests/test_sources.py::TestFileSourceHandler::test_resolve_existing_file \
         tests/test_grpc_adapter_main.py::TestVerifyModuleType::test_non_isinstance_object_with_mount_passes -q
E  AssertionError: assert PosixPath('/tmp') == PosixPath('/tmp/tmp11_emrx5')
1 failed, 1 passed in 0.12s
```

The second known failure (`test_grpc_adapter_main.py::TestVerifyModuleType::
test_non_isinstance_object_with_mount_passes`) **passed** in both the full run
and in isolation at `origin/main`, so it is order- or environment-dependent
rather than a hard failure on this host. Reported, not claimed as fixed --
nothing in this change touches it.

No `skipif` was needed: the change is pure-Python, filesystem-only, uses
`Path.parts` rather than string separators, and adds no shell or platform
dependency, so it runs identically on the Windows CI runner.

---

## Spend

**Authority: $0 -- arithmetic `0 runs x 0 arms x $0 / 1.00 = $0.00`, slack $0.00.**

That arithmetic closes: this item buys **no measurement runs at all**. It is
deletions, a guard test, and two recipe runs. There is no run-count/price pair
to mis-size, so the authoring-rule defect the goal warns about (a price quoted
for a smaller run set reused for a larger one) cannot arise here. **Nothing was
dropped for want of budget; branch B was never reached.**

**API spend actually incurred, recorded rather than treated as free** (the goal
carves recipe runs out of the $0 cap explicitly):

| Run | Recipe | LLM steps executed | Metered cost |
|---|---|---|---|
| `run-aed6a9fb36a9` | `validate-agents` v1.5.1 | 2 (`quick-approval`, `synthesize-report`); `description-quality-check` and `tool-access-analysis` **skipped** -- all agents cleared the deterministic gate | not separately metered by the runner |
| `run-864d8d1c009a` | `validate-bundle-repo` v3.12.0 | 3 (`composition-analysis`, `repo-conventions`, `synthesize-report`); `bundle-overview-regen-enhance` **skipped** via `enhance_diagrams: "false"` | not separately metered by the runner |

Five LLM steps total across two runs. The runner reports no per-run cost figure,
so I am recording the executed step count and the skips rather than inventing a
dollar amount. No DTU, no container, no other infrastructure was created, so
there is nothing in the infra ledger for this lane and nothing to tear down.

---

## Findings

**F1 -- `validate-bundle-repo` silently degrades two committed artifacts.**
Its `bundle-overview-regen-write` step (`recipes/validate-bundle-repo.yaml:4944`,
writing at `:4992` and `:5004`) rewrites `bundle.dot` and `bundle.png`
**unconditionally**; only its *input* is gated on `enhance_diagrams`. Run with
`enhance_diagrams: "false"`, it overwrote the committed human-readable DOT with
the un-enhanced structural one -- 82 insertions / 80 deletions, `bundle.png`
914KB -> 690KB, e.g.
`label="Foundation Bundle (v2.1.2)\nThe shared starter kit..."` became
`label="foundation v2.1.2 — bundle repo"`. Reverted with `git checkout --`;
**neither file is in this PR.** `grep -c behavioral-anchor bundle.dot` -> `0`, so
the deletion introduced no staleness a regeneration was needed to fix. Not fixed
here: changing a recipe's write behaviour is a separate change with its own blast
radius.

**F2 -- four files outside `test-fixtures/`/`docs/` still contain `<example>`,
and none is a violation.** Reported rather than carved out, per the goal's
"do NOT weaken guard (c)" rule:

```
context/shared/description-authoring-principles.md   (4)  <- states the prohibition
context/shared/common-system-base.md                 (2)
context/shared/common-agent-base.md                  (1)
agents/bundle-design-expert.md                       (2)  <- BODY, lines 232/244
```

Guard (c) checks `meta.description` only -- exactly what the validator's
`example_block_present` check reads. The three `context/shared/` files are policy
prose *documenting* the ban, and `bundle-design-expert.md`'s two hits are in the
agent **body**, teaching authors not to use them ("no `<example>` or
`<commentary>` blocks"). Widening the guard to whole-file content would fail the
document that defines the rule. The scope is the description, and it is the
validator's scope, not a convenience boundary.

**F3 -- the two validators' scope gap is now closed on one side only.**
`validate-agents` still discovers 23 agents and still never walks `experiments/`;
`validate-bundle-repo` discovers 32 (was 45). The gap that hid these 11 violators
is now covered by guard (c) in the test suite, which runs on every CI job --
but `validate-agents` itself is unchanged, and a repo that runs only that recipe
would still miss an `experiments/` violator. Out of scope here (the goal pins
guard (c) to the *validator's* scope, which I matched exactly); worth an item.

---

## Deviations from the goal

1. **`experiments/README.md` does not exist**, so deliverable 3's "quote the line
   you relied on" is satisfied by quoting its *absence* plus the four live
   references that independently forbid deletion. Recorded, not absorbed.
2. **`docs/` added to guard (c)'s exclusion set** on top of the validator's own
   five. The goal names `test-fixtures/` and `docs/` as the exclusions, so this
   matches the goal; the reason is stated in the code comment (frozen lane
   records quote violations verbatim as evidence). It excludes zero real agents
   today -- there is no `docs/**/agents/` directory -- so it cannot be masking
   anything.
3. **`enhance_diagrams: "false"`** passed to `validate-bundle-repo` to avoid
   spending on LLM diagram-label enhancement that this item does not need. It
   turned out to have the side effect in F1, which is why F1 is recorded.
