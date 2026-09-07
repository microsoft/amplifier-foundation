# Lane 6f80 — tool-delegate strips `<example>`/`<commentary>` at catalog-render time

**Item:** `model_performance-6f80` · **Repo:** `amplifier-foundation` · **Branch:**
`lane/6f80-delegate-example-strip` · **Merge-base:** `5f0f04b`

**OUTCOME: A (RESOLVED)** for the engineering deliverables — every one is DONE.
One deliverable's *prediction* did not hold and the reason is a measurement
error in the goal, not in the patch; that is recorded below as a finding, not
absorbed.

---

## Deliverables

| # | Deliverable | State |
|---|---|---|
| 1 | Render-time strip of `<example>`/`<commentary>` in `tool-delegate` | **DONE** |
| 2 | FAIL-BEFORE test, both counts quoted | **DONE** |
| 3 | Boundary test proving nothing outside the blocks is consumed | **DONE** |
| 4 | Debug log line naming every stripped agent | **DONE** |
| 5 | Before/after delegate-catalog bytes on the owner's app list | **DONE** (9,474 chars, not the goal's predicted ~20,000 — see F1) |
| 6 | CI green (6 legs); suite failure set ⊆ main's | **DONE** — 6/6 pass + CLA |
| 7 | DRAFT PR; the manager merges | **DONE** — [#376](https://github.com/microsoft/amplifier-foundation/pull/376), **DRAFT**. **Not merged.** (briefly marked ready in error — see F4) |

**Spend: $0.00 of $0.00 authority.** No API call, no DTU, no infrastructure
registered. The measurement is a pure offline render over the host's existing
read-only bundle cache. `~/.amplifier/` was never written.

---

## 1. The change (67 insertions, 5 deletions, one file)

`modules/tool-delegate/amplifier_module_tool_delegate/__init__.py`

```python
_DESCRIPTION_EXAMPLE_BLOCK = re.compile(
    r"(?:^[ \t]*)?<(example|commentary)\b[^>]*>.*?</\1\s*>(?:[ \t]*\n)?",
    re.DOTALL | re.IGNORECASE | re.MULTILINE,
)
```

Applied in `_get_agent_list()`, which is the tool's ONE catalog-render path
(`description` is its only caller — verified by grep across the repo). The
mounted config is read, never written: `test_description_on_disk_is_not_mutated`
pins that the bundle author's own text is unchanged after a render.

**Why the renderer and not more content edits.** `kp79`/`kv98` stripped example
blocks by hand from eight repos. That fixes today's catalog and holds until
someone composes a bundle this program does not own. The 25 blocks still
rendering into this host's catalog came from exactly such repos. The renderer
declines to pay for anyone's examples, forever.

## 2. FAIL-BEFORE — both counts quoted

Same test file, same command, merge-base `5f0f04b` vs branch:

```
# at main (implementation stashed)
4 failed, 7 passed in 0.07s
    FAILED TestExampleBlocksAreStripped::test_example_block_absent_from_rendered_entry
    FAILED TestExampleBlocksAreStripped::test_example_body_text_absent_from_rendered_entry
    FAILED TestNothingOutsideTheBlocksIsConsumed::test_text_sharing_a_line_with_the_block_survives
    FAILED TestStrippedAgentsAreNamedInDebugLog::test_debug_line_names_the_stripped_agent

# on the branch
11 passed in 0.05s
```

Verbatim from main's run — the fixture's example payload *in the rendered
catalog entry*:

```
AssertionError: assert 'How does auth work here?' not in '  - fixture...NE SURVIVES.'
  'How does auth work here?' is contained here:
    user: 'How does auth work here?'
    assistant: 'I'll use the explorer to map the auth flow.'
    <commentary>
```

Evidence: `evidence/fail-before.txt`, `evidence/pass-after.txt`.

## 3. Fidelity — the boundary is the closing tag

Five boundary tests, all in `TestNothingOutsideTheBlocksIsConsumed`:

1. Every line of surrounding prose survives — trigger, USE WHEN, DO NOT USE
   WHEN, "Authoritative on", and a final line after the last block.
2. `BEFORE <example>eaten</example> AFTER` → `BEFORE ` and ` AFTER` both
   survive byte-for-byte. `.*?` is non-greedy and the optional trailing
   `[ \t]*\n` only fires when it ends the line, so an inline block never eats
   the sentence around it.
3. A description with **no** blocks renders byte-identical (`entry == "  - name: " + clean`).
   Guarded twice: the regex, and an early-return in `_strip_example_blocks`.
4. An **unpaired** `<example>` matches nothing and is left alone. Swallowing the
   rest of a description because its author forgot a close tag would delete
   exactly the routing facts the catalog exists to carry — that is the named
   failure mode, and it is tested rather than assumed.
5. Other agents in the same catalog are untouched.

Independent corpus check, all 68 discovered agents: **0 surviving lines missing
from the render** (`measure_catalog_bytes.py` re-derives the strip and asserts
every non-blank out-of-block line still appears in the rendered catalog).

## 4. The debug line names them

```
DEBUG amplifier_module_tool_delegate: delegate catalog: stripped
<example>/<commentary> blocks from 10 agent description(s) at render time
(the descriptions on disk are unchanged): amplifier-online:deployment-guide,
amplifier-tester:setup-digital-twin, amplifier-tester:validator,
app-cli:cli-expert, digital-twin-universe:dtu-profile-builder,
infographic-builder:infographic-builder, notify:notify-expert,
terminal-tester:terminal-debugger, terminal-tester:terminal-operator,
terminal-tester:terminal-visual-tester
```

Names, not a count — a bundle author whose examples vanish can find out why.
`test_no_log_line_when_nothing_was_stripped` pins that a no-op strip is silent.
Evidence: `evidence/debug-log-line.txt`.

## 5. Measured saving on the owner's app list

Rendered offline from this host's own read-only bundle cache, both arms through
the *same* renderer — the BEFORE arm neutralises `_strip_example_blocks` to the
identity function, which is byte-for-byte what tool-delegate main does.

| | chars |
|---|---:|
| delegate description **before** | **49,954** |
| delegate description **after** | **40,480** |
| **saved** | **9,474 (18.97%)** |
| `<example` tags before → after | **25 → 0** |
| `<commentary` tags before → after | **13 → 0** |
| agents in catalog | 68 (10 stripped) |

Per agent (chars saved):

```
2371  amplifier-online:deployment-guide           4004 -> 1633   6 examples
1380  app-cli:cli-expert                          2659 -> 1279   3
 969  notify:notify-expert                        1825 ->  856   2
 918  amplifier-tester:setup-digital-twin         1565 ->  647   2
 837  amplifier-tester:validator                  1342 ->  505   2
 717  digital-twin-universe:dtu-profile-builder   1468 ->  751   2
 605  infographic-builder:infographic-builder     1639 -> 1034   2
 600  terminal-tester:terminal-debugger           1270 ->  670   2
 565  terminal-tester:terminal-visual-tester      1222 ->  657   2
 512  terminal-tester:terminal-operator           1109 ->  597   2
```

Evidence + reproducer: `evidence/measure_catalog_bytes.py`,
`evidence/catalog-bytes-before-after.json`. Re-runs at $0.

### Calibration — checked against values already known

The goal's census of this host says **25 `<example>` blocks are still being
rendered**. The reconstruction independently finds **exactly 25**. That is the
check that makes the rest of the table quotable.

Reconstruction differences, stated rather than smoothed: 68 agents discovered
against 65 in a live session's catalog (the goal says 66). The three extras —
`context-intelligence:context-intelligence-design-facilitator`,
`context-intelligence:context-intelligence-tool-designer`,
`work-tracker:feedback-triage` — are present in the cached bundles but not
mounted by their behavior files. **None of the three carries an example block**,
so they move the agent count and not one byte of the saving.

---

## FINDINGS

### F1 — the goal's "~20,000+ chars" prediction is measuring whole catalog entries, not example payload. The patch is not underperforming.

Measured saving **9,474**, against the goal's expected order of "~20,000+".
The goal's own per-bundle table is the source of the gap, and it reconciles
exactly once you notice what it counts:

| bundle | goal's figure | this lane: description + `"  - name: "` prefix + `\n` |
|---|---:|---|
| terminal-tester (3 agents) | 3,725 | 3,601 + 121 + 3 = **3,725** ✓ |
| app-cli | 2,684 | 2,659 + 24 + 1 = **2,684** ✓ |
| notify | 1,852 | 1,825 + 26 + 1 = **1,852** ✓ |
| infographic | 1,685 | 1,639 + 45 + 1 = **1,685** ✓ |
| amplifier-online | 4,007 | 4,004 + 39 + 1 = 4,044 (off by 37 — a slightly different cached revision) |

Four of five match **to the character**. So the goal's table is the **total
catalog-entry size of every bundle that contains an example block** — not the
size of the example blocks in them. Summing it predicts what deleting those
agents entirely would save, which is ~2.1× what deleting only their examples
saves. The blocks are 9,474 of the 19,103 chars those ten entries occupy.

**This is a defect in the goal's arithmetic, not a shortfall in the patch**, and
it is reported rather than absorbed (the goal's own authoring rule). 9,474 chars
off every request of every session on this host, for a 67-line diff, at $0.

### F2 — CI does not run the module's own test directory. My test is in `tests/` for that reason.

`pyproject.toml` sets `testpaths = ["tests", "modules/tool-delegate/tests"]`, but
`.github/workflows/ci.yml` runs `uv run pytest tests/ -q --tb=short` — an
explicit path argument, which **overrides `testpaths` entirely**. Every test
under `modules/tool-delegate/tests/` (17 files) is therefore invisible to all six
CI legs today.

This item exists because a rule with no enforcement point decays. A fail-before
test that CI never executes is that same failure wearing a test's costume, so
`tests/test_delegate_example_strip.py` lives in the directory CI actually runs —
following the precedent of `tests/test_named_delegate_matrix_67u.py`.

**The gap itself is out of scope here and not fixed by this PR.** Filed as a
finding for the manager: either add `modules/tool-delegate/tests` to the CI
command, or drop the path argument and let `testpaths` do its job.

### F3 — a sibling lane's GOAL.md carries THIS item's id. It claimed 6f80 for ~2.5 minutes.

`work_claim(model_performance-6f80)` was refused at the first call of this
session: *"issue already claimed by agent-spark-1-105059"*. That PID was a live
`amplifier run /goal` whose cwd is
`lanes/hd-work-tracker/amplifier-work-tracker` — a **different lane, on a
different repo, doing different work**: *"21 tool descriptions + a 7,481-char
awareness file"*. Its `GOAL.md` OUTCOME branch A names
**`model_performance-6f80`** verbatim, so it claimed this item on startup.

The three lanes launched together at 13:24:06–07 (`6f80`, `hd-browser-bridge`,
`hd-work-tracker`); only `hd-work-tracker`'s goal carries the wrong id.

**It resolved itself without intervention.** The sibling released the item at
`20:26:49Z` — about 2.5 minutes after claiming it — and this lane claimed it
cleanly at `20:35`. So the collision cost nothing here: the engineering work
proceeded in parallel with the refusal (the deliverables live entirely inside
this worktree and never needed the claim), and the item was held by its own
lane before resolution.

**The goal-file defect is still real and still unfixed**, and it is the second
lane in this batch to be pointed at another lane's item id. Two costs it can
still impose on a future run: (1) if the sibling had *resolved* instead of
releasing, 6f80's public record would now describe work-tracker's tool
descriptions; (2) whatever item `hd-work-tracker` was *supposed* to resolve is
not named anywhere in its goal, so it has no correct id to resolve at all.

Reported, not absorbed. This lane does not edit another lane's goal file, and
the goal's own rule says a defect in a goal is reported against the goal rather
than worked around.

### F4 — the goal says both "DRAFT PR" and "mark ready when green". I followed the wrong one; the manager caught it.

Three places say DRAFT and one says ready:

| where | text |
|---|---|
| DELIVERABLES | "**DRAFT PR; the manager merges.**" |
| Procedure 4 | "open a DRAFT PR with `gh pr create --draft`" |
| acceptance criteria | "the PR is DRAFT until green, and the manager merges it" |
| KNOWN (twice) | "**Do NOT merge.** DRAFT PR, mark ready when green, stop." |

I read "DRAFT until green" + "mark ready when green" as authorising the
transition and ran `gh pr ready 376`. **That was wrong**, and the goal itself
says so twice over: DELIVERABLES is the deliverable list, and the goal's own
tie-break rule — *"If two objectives here read as equally required, treat the
FIRST as the objective"* — puts DELIVERABLES ahead of KNOWN. The correct
reading is that green is what makes the PR *mergeable by the manager*, not what
authorises the lane to change its state.

**Corrected**: `gh pr ready --undo 376` → `isDraft: true`, verified by a fresh
`gh pr view`. The item was **reopened** rather than given an erratum, because a
wrong PR state is wrong *work*, not a wrong sentence; `closed_at` was cleared
and the item re-resolved, which moves this batch's throughput by one item —
stated here rather than hidden.

**The goal-text defect stands and is reported, not absorbed.** Two adjacent
sentences authorise opposite end states for the same deliverable, and the only
thing that resolved it was a human reading the PR. Suggested fix for the next
goal of this shape: delete "mark ready when green" and say *"leave the PR in
DRAFT; the manager marks it ready and merges."*

---

## Suite and lint

| | main (`5f0f04b`, patch stashed) | branch |
|---|---|---|
| full suite (`uv run pytest -q`) | 4 failed, 2095 passed, 4 skipped | **2099 passed, 4 skipped, 0 failed** |
| ruff check | — | All checks passed |
| ruff format --check | — | 2 files already formatted |

Main's four failures are precisely this lane's four fail-before assertions.
Branch failure set is **empty**, so ⊆ main's holds trivially.

**CI: 6/6 legs pass** on `cff5225` (run
[34159936813](https://github.com/microsoft/amplifier-foundation/actions/runs/34159936813)),
plus `license/cla`:

```
Tests (ubuntu-latest,  Python 3.11)  pass  50s
Tests (ubuntu-latest,  Python 3.12)  pass  49s
Tests (ubuntu-latest,  Python 3.13)  pass  42s
Tests (windows-latest, Python 3.11)  pass  1m21s
Tests (windows-latest, Python 3.12)  pass  1m12s
Tests (windows-latest, Python 3.13)  pass  1m29s
license/cla                          pass
```

## Publication (publication/v1, read back from the remote)

```
repo      microsoft/amplifier-foundation
branch    lane/6f80-delegate-example-strip
PR        #376  https://github.com/microsoft/amplifier-foundation/pull/376
state     open, DRAFT (CI 6/6 + CLA green on the head)
head_sha  see the lane's DONE.json
```

`head_sha` is deliberately NOT quoted here. This note is itself committed and
pushed, so any sha written in it is the sha of the commit *before* the one that
carries it — exactly the stale-abbreviation failure `publication/v1` exists to
prevent (lane 74w claimed `039eb32` while the PR carried `88a62eb`). The
authoritative value is read back from the remote with `publication_readback.sh`
**after** the final push and recorded, full 40-hex, in
`lanes/6f80-delegate-example-strip/DONE.json`.

**The PR is NOT merged — the manager merges.**

## Deviations

- **Test location** — module tests would have been the conventional home;
  `tests/` was chosen because CI cannot see the module directory (F2).
- **Measurement method** — the goal says "render the delegate catalog from a
  scratch session". A scratch *session* means an API call, and the authority is
  $0. The renderer was driven directly instead, over the same bundle cache a
  session mounts, with both arms through the same code path. The 25→25 example
  count is what validates that substitution.
- **`amplifier source add` was never run** — `zc6t` F6 records that it ignores
  `AMPLIFIER_HOME` and writes the real settings file. Nothing under
  `~/.amplifier/` was written by this lane.
