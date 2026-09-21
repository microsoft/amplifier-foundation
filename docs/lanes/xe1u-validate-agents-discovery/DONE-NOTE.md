# Lane xe1u -- `validate-agents` discovery widened (23 -> 32)

**Item:** `model_performance-xe1u` (project `model_performance`)
**Branch:** `lane/xe1u-validate-agents-discovery` off `origin/main` @ `5a9e07b`
**Outcome:** **A. RESOLVED** -- every deliverable DONE. Nothing was cut by the cap.
**Landing stage:** draft PR only. Per the goal's LANDING STAGE clause, a deliverable
whose final state needs a merge is satisfied here as *demonstrated fail-before /
pass-after and shipped for landing*; the merge is the manager's next stage.

**Headline:** widening discovery flipped the verdict **PASS -> FAIL**, and the goal
anticipated exactly this. The four newly-surfaced ERRORs are **not dirty agents** --
they are four *context documents* that live in a directory named `agents/`. Reported,
**left unfixed**, per the scope-out. The recipe was **not** weakened to restore PASS.

---

## Deliverables

| # | Deliverable | State |
|---|---|---|
| 1 | Discovery widened to every `**/agents/*.md`, using 6phe's existing exclusion set (quoted) | **DONE** |
| 2 | Before/after discovered counts, both measured by running the recipe | **DONE** -- 23 -> **32** |
| 3 | Verdict stays PASS | **DONE as reported: it did NOT.** PASS -> FAIL, headline finding F1, left unfixed |
| 4 | Report metadata names search locations AND discovered total | **DONE** |
| 5 | Shared-helper question answered either way | **DONE** -- shared implementation **DECLINED with reason**; a cheap *parity guard* implemented instead |
| 6 | Suite green, draft PR, not merged | **DONE** |
| 7 | DONE-NOTE at the lane artifact root | **DONE** (this file) |

---

## 1. Discovery widened -- and where the exclusion set was read from

`recipes/validate-agents.yaml`, step `agent-discovery`, previously walked a
hand-maintained list of three places (`agents/`, `behaviors/*/agents/`,
`bundles/*/agents/`). It now globs the repo and drops excluded parts:

```python
candidates = list(path.rglob("*/agents/*.md")) + list((path / "agents").glob("*.md"))
```

**The exclusion set was copied, not invented.** Quoting the source verbatim --
`tests/test_anchors_bundles_dry.py:52-69` (added by lane 6phe):

```python
# The validator's own agent-discovery scope, copied deliberately rather than
# approximated. `@foundation:recipes/validate-bundle-repo.yaml`'s
# `agent-description-validation` step globs
# `rglob("*/agents/*.md") + glob("agents/*.md")` and drops any path containing
# one of these parts. Two validators disagreeing about which files exist is
# exactly how the 11 `experiments/` violators survived #341 and ux32:
# `validate-agents` discovers 23 agents and never walks `experiments/`, while
# `validate-bundle-repo` discovers 45 and does. This guard follows the wider
# one. `docs` is added on top of the validator's set: `docs/` holds frozen lane
# records that quote violations verbatim as evidence and must never be rewritten.
AGENT_SCAN_EXCLUDED_PARTS = {
    "test-fixtures",
    "tests",
    "node_modules",
    ".git",
    ".venv",
    "docs",
}
```

That six-element set is now `EXCLUDED_PARTS` in the recipe, byte-for-byte, with the
provenance in a comment above it. **No second list was created** -- and
`TestDiscoveryScopeParity` (below) fails the build if one ever appears.

## 2. Before / after -- both measured by running the recipe

| | BEFORE | AFTER |
|---|---|---|
| Recipe version | v1.5.1 (unmodified) | v1.6.0 (this branch) |
| `run_id` | `run-c26b396f2f1e` | `run-67388a546d75` |
| `total_count` | **23** | **32** |
| `search_locations` | 3 | 6 |
| `quality_level` | `good` | `critical` |
| **Verdict** | **PASS** | **FAIL** |

Per-location counts, from the AFTER run's `location_counts`:

| Location | Agents | |
|---|---|---|
| `agents/` | 16 | walked before |
| `bundles/anchors/agents/` | 6 | walked before |
| `bundles/anchors-amp-dev/agents/` | 1 | walked before |
| `context/agents/` | 4 | **newly walked** |
| `examples/agents/` | 1 | **newly walked** |
| `experiments/build-up/agents/` | 4 | **newly walked** |

**32 is measured, not assumed** -- and it is the *same 32 files*, by path, as
6phe's `evidence/scope-parity.txt` ("validator scope ... 32 agents / guard (c)
scope ... 32 agents / identical sets: True"). All three scopes now agree.

Full run records: [`evidence/before-validate-agents-run.txt`](evidence/before-validate-agents-run.txt),
[`evidence/after-validate-agents-run.txt`](evidence/after-validate-agents-run.txt),
and [`evidence/after-deterministic-phases.json`](evidence/after-deterministic-phases.json)
(the discovery/structural/classification output re-derived locally, no LLM step, so
the numbers above are reproducible for $0).

## 3. The verdict did NOT stay PASS -- and that is finding F1

The goal's own words: *"If it does not stay PASS, that is the headline finding:
report exactly which newly-walked agent fails and why, and leave it unfixed."*

`structural summary: total 32, passed 28, errors 4, warnings 1`

```
4x ERROR  NO_FRONTMATTER
    context/agents/delegation-instructions.md
    context/agents/multi-agent-patterns.md
    context/agents/session-repair-knowledge.md
    context/agents/session-storage-knowledge.md

1x WARNING NO_TOOLS_SECTION
    examples/agents/file-responder.md
```

**Zero findings in `experiments/build-up/agents/` (4 files)** -- the tree that
started this whole chain is clean, confirming 6phe fixed the content. That is the
widened scan's good news, and it is only visible because the scan now reaches it.

Details, and why nothing was fixed, in **Findings** below.

## 4. Under-coverage is now visible in the output

`discovery_results` gained three fields -- `location_counts`, `scan_patterns`,
`excluded_parts` -- and `location` is now **derived** from each file's own parent
directory instead of hardcoded, so a new `agents/` tree names itself in the report
the first time it appears rather than the day someone remembers to add it.

The `synthesize-report` prompt now *requires* a Coverage block in both the executive
summary and the metadata: patterns scanned, parts excluded, total, and a row per
location with its count. The `quick-approval` fast path (the one a PASSing repo
actually reads) restates the same coverage line.

The AFTER report emitted it:

```
- **Agents discovered**: 32 total across 6 locations -- agents/ 16,
  bundles/anchors-amp-dev/agents/ 1, bundles/anchors/agents/ 6, context/agents/ 4, ...
- excluded_parts: .git, .venv, docs, node_modules, test-fixtures, tests
```

The BEFORE report's *"Agents Found: 23 total across 3 search locations"* is the
anti-pattern this replaces: complete-looking, and silent about nine files.

## 5. The shared-helper question -- **DECLINED**, with the reason

**A shared discovery implementation is NOT cheap, and I did not build one.**

Two routes exist and both are worse than the divergence they would fix:

1. **A recipe-engine `include` / step-library concept.** Recipe steps are
   self-contained heredocs interpolated as strings; there is no import mechanism
   between two `.yaml` recipes. Adding one restructures the step contract -- which
   the goal names explicitly as the "say so and leave it" case.
2. **A Python helper in `amplifier_foundation/`, imported by both.** This looks
   cheap and is a trap. `validate-agents`' own `structural-validation` step already
   documents why, in code:

   > `amplifier_foundation` is not guaranteed to be importable in every
   > environment this recipe runs in (it is run against third-party bundle
   > repos). Degrade to a no-op rather than killing the whole run.

   Discovery cannot degrade to a no-op -- a discovery that silently finds nothing is
   the exact failure this lane exists to remove. Making the *scope* depend on an
   import that is explicitly allowed to be missing would trade a visible
   under-count for an invisible one.

**Implemented instead (cheap, and it actually closes the hole):
`TestDiscoveryScopeParity`** in `tests/test_anchors_bundles_dry.py` -- two guards
that run on every CI job:

- `test_exclusion_sets_agree_across_both_recipes_and_this_guard` -- parses the set
  literals out of both recipes and compares them to `AGENT_SCAN_EXCLUDED_PARTS`. A
  fourth list, or a drifting third, fails the build by name. The one permitted
  delta (`docs`, present in the guard and `validate-agents`, absent from
  `validate-bundle-repo`) is asserted **explicitly** rather than tolerated.
- `test_validate_agents_discovers_exactly_the_guards_agent_files` -- **executes the
  recipe's own discovery step** and compares its file set to `_agent_files()`. A test
  that re-implemented the glob would keep passing while the recipe diverged, which is
  precisely the failure mode being guarded.

**Fail-before / pass-after** (both committed):

```
$ git stash push -- recipes/validate-agents.yaml   # guards vs the OLD recipe
FAILED ...TestDiscoveryScopeParity::test_exclusion_sets_agree_across_both_recipes_and_this_guard
FAILED ...TestDiscoveryScopeParity::test_validate_agents_discovers_exactly_the_guards_agent_files
2 failed in 0.09s
```

The second failure named all nine missing files:

```
In recipe not guard: []; in guard not recipe:
['context/agents/delegation-instructions.md', 'context/agents/multi-agent-patterns.md',
 'context/agents/session-repair-knowledge.md', 'context/agents/session-storage-knowledge.md',
 'examples/agents/file-responder.md', 'experiments/build-up/agents/coder.md',
 'experiments/build-up/agents/explorer.md', 'experiments/build-up/agents/planner.md',
 'experiments/build-up/agents/tester.md']
```

After: `8 passed in 0.13s` (was 6 tests; +2).
[`evidence/parity-guards-FAIL-BEFORE.txt`](evidence/parity-guards-FAIL-BEFORE.txt),
[`evidence/parity-guards-PASS-AFTER.txt`](evidence/parity-guards-PASS-AFTER.txt).

## 6. Suite

`2 failed, 2017 passed, 3 skipped` -- and the two failures are **exactly the two the
goal names as pre-existing on this host**, neither touched by this branch:
`tests/test_sources.py::TestFileSourceHandler::test_resolve_existing_file` and
`tests/test_grpc_adapter_main.py::TestVerifyModuleType::test_non_isinstance_object_with_mount_passes`.
[`evidence/full-suite.txt`](evidence/full-suite.txt).

`git status` after all runs shows only the two source files plus this lane
directory. **`validate-bundle-repo` was deliberately NOT run in this lane**, so
6phe's F1 regen side effect (`bundle.dot` / `bundle.png` rewritten
unconditionally) could not fire. Verified rather than assumed.

---

## Spend

**Authority: $0 -- arithmetic `0 runs x 0 arms x $0 / 1.00 = $0.00`, slack $0.00.**

**That arithmetic closes, and the authoring rule's failure mode cannot arise here**:
this item buys *no measurement runs at all* -- there is no run-count/price pair to
mis-size. It is a recipe edit, a guard test, and two recipe runs, which the goal
carves out of the cap explicitly ("if a run costs API spend, RECORD it rather than
treating it as free"). **Nothing was dropped for want of budget; branch B was never
reached.**

**API spend actually incurred, recorded rather than treated as free:**

| Run | Recipe | LLM steps executed | Metered cost |
|---|---|---|---|
| `run-c26b396f2f1e` | `validate-agents` v1.5.1 (BEFORE) | 2 -- `quick-approval`, `synthesize-report`; `description-quality-check` and `tool-access-analysis` **skipped** (`requires_llm_analysis == false`) | not separately metered by the runner |
| `run-67388a546d75` | `validate-agents` v1.6.0 (AFTER) | 3 -- `description-quality-check`, `tool-access-analysis`, `synthesize-report`; `quick-approval` **skipped** (same condition, now false the other way) | not separately metered by the runner |

Five LLM steps across two runs. The runner reports no per-run cost figure, so the
executed step count and the skips are recorded rather than a dollar amount invented.

**Note for the manager, since it is a real cost signal:** widening discovery moved
this repo off the quick-approval fast path. A FAILing run costs *more* LLM steps than
a PASSing one (3 vs 2), because the two conditional deep-analysis phases now fire.
That is a consequence of finding F1, and it reverses the moment F1 is resolved.

No DTU, no container, no other infrastructure was created. Nothing in the infra
ledger for this lane; nothing to tear down.

---

## Findings

**F1 (headline) -- `context/agents/` is a path collision, not four broken agents.**
The four `NO_FRONTMATTER` ERRORs are *context documents*, not agent definitions.
Evidence, gathered rather than assumed:

- `behaviors/agents.yaml:44-45` and `behaviors/tasks.yaml:18-19` list
  `delegation-instructions.md` and `multi-agent-patterns.md` under `context:` --
  never under `agents:`.
- `agents/session-analyst.md:369-370` `@`-mentions the two session-knowledge files
  as context.
- `context/agents/` means *context about agents*, not *agents*. Nothing ever spawns
  them, so `NO_FRONTMATTER`'s "agent won't load" describes an event that cannot
  occur.

The defect is in the **discovery predicate's semantics**: `**/agents/*.md` uses the
*directory name* as the type signal, conflating definitions with context that
happens to live beside them. `AGENT_SCAN_EXCLUDED_PARTS` does not exclude `context`,
and correctly so -- excluding it would hide a real agent tree the day someone adds
one.

**Left unfixed, deliberately.** Adding frontmatter would be worse than the finding:
it would make four context docs advertise themselves as spawnable agents. The
architecturally correct fix is a type discriminator matching the loader's own
contract -- *discover by path, classify by `meta:` presence; a `.md` under an
`agents/` directory with no `meta:` is skipped as not-an-agent, not errored*. That
neither weakens a check nor invents a second exclusion list. **It is a separate
item**, and it is the one thing that would restore PASS honestly.

**F2 -- `validate-bundle-repo`'s "32 agents, errors: []" is quietly wrong on the
same 32 files.** The two recipes now agree on *scope* and still disagree on
*content*: `validate-bundle-repo`'s `agent-description-validation` step reads only
`meta.description` (`extract_description()` returns `""` when frontmatter is
absent), so a file with **no frontmatter at all** scores 0 tokens, 0 `<example>`,
0 `<commentary>` and passes clean. It therefore counted the four context docs as
checked agents and reported `errors: []` (6phe, `run-864d8d1c009a`). Two
consequences: its headline count of 32 is inflated by 4 -- **the true agent count in
this repo is 28** -- and it cannot detect a genuinely broken or missing frontmatter
in any agent. Reported, not fixed; it is content-check scope, not discovery scope.

**F3 -- `examples/agents/file-responder.md` has no `tools:` section (WARNING).**
Benign and arguably correct: the fixture exists to demonstrate spawn-capability
*inheritance* from an agent `.md` (`examples/23_spawn_with_agents.py:200` maps the
name to a literal file path). Declaring `tools: []` would erase the behaviour being
demonstrated. Reported, not fixed.

**F4 -- a FAILing repo costs more LLM steps than a PASSing one.** See the spend
note above. Worth knowing before anyone widens discovery on a larger repo.

---

## Deviations from the goal

1. **Deliverable 3 ("the verdict must stay PASS") was not met, by design.** The goal
   predicted PASS on the premise "all agents are clean post-6phe". That premise
   holds for every *agent* -- all 28 are clean, including all 4 in
   `experiments/build-up/`. It does not hold for four *non-agents* the widened glob
   now walks. The goal's own branch for this case was taken: report which files
   fail and why, leave them unfixed, do not weaken anything. Recorded, not absorbed.
2. **The shared-helper question was answered "no", and something cheaper was built
   instead.** The goal accepts a stated "no, because X" as complete. I went slightly
   further and added the parity guards, because they are cheap (two tests, no engine
   change) and they are what actually prevents the divergence from recurring. If the
   reviewer considers the extra test out of scope, it is severable in one commit --
   the recipe change stands alone.
3. **`validate-bundle-repo` was not re-run.** 6phe's `run-864d8d1c009a` already
   records its side of the parity, this lane does not change that recipe, and
   running it risks 6phe's F1 artifact-rewrite side effect for no new information.
