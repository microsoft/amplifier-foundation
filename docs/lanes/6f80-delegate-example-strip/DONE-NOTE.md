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
| 8 | Regression pin: ordinary prose merely SAYING "example" renders byte-identical | **DONE** (amendment — see §6 and F5) |

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

## 6. Regression pin — ordinary prose that only SAYS "example" (amendment)

The strip is keyed off the TAG in **two** places: the cheap
`"<example" in lowered` guard, and the regex. Nothing in the file tested that.
`test_a_description_with_no_blocks_is_byte_identical` covers the easy half
only — its fixture contains neither the tag nor the word, so it passes
identically whether the strip keys off one or the other. The half that actually
gets damaged, ordinary prose that says "example" and "commentary" out loud
(which is most of the real corpus), had **no fixture at all**.

Added: `ORDINARY_PROSE_SAYING_EXAMPLE` and
`TestOrdinaryProseSayingExampleIsUntouched` (3 tests) in the same file,
`tests/test_delegate_example_strip.py` — the directory CI actually runs (F2).
**Additive only**: no existing test changed, weakened, deleted or moved; the
only edit to prior content is renumbering the observability section 3 → 4.
File count 11 → 14 tests.

```
tests/test_delegate_example_strip.py::TestOrdinaryProseSayingExampleIsUntouched::test_ordinary_prose_renders_byte_identical PASSED [ 33%]
tests/test_delegate_example_strip.py::TestOrdinaryProseSayingExampleIsUntouched::test_every_line_of_the_ordinary_prose_survives PASSED [ 66%]
tests/test_delegate_example_strip.py::TestOrdinaryProseSayingExampleIsUntouched::test_ordinary_prose_is_not_reported_as_stripped PASSED [100%]

3 passed in 0.08s
```

**It is a regression pin, not a fail-before**, and that is said plainly rather
than dressed up: it is green at main too, because main strips nothing at all.
What it buys is that today's correctness stops being accidental. Its value was
therefore measured by mutation instead of by a before/after count — three
mutants of `_strip_example_blocks`, each run against the whole file:

| mutant | result |
|---|---|
| guard word-keyed, regex still tag-keyed | **14/14 still pass** — the widened guard alone is unobservable; the tag-keyed regex refuses the prose anyway |
| regex word-keyed (`^.*\b(example\|commentary)\b.*$`), guard still tag-keyed | **these 3 still pass** — the tag-keyed guard short-circuits before the regex is reached. That short-circuit *is* the protection |
| **both** word-keyed | **these 3 fail.** 2 pre-existing tests fail too, but only on their tagged fixtures; every line of ordinary prose is deleted silently and this class is the only thing in the file that sees it |

The middle row is the honest limit of this pin and is stated rather than
hidden: no single-token mutation of the regex alone is caught, because the
guard absorbs it. The pin is on the guard-plus-regex contract as a pair.

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

### F5 — CI's `-q` prints no node ids, so "quote the named test in a green CI log" is not literally obtainable here. The count identity is the substitute.

The goal for the amendment asked for the named test quoted in a fetched green
CI log. `.github/workflows/ci.yml` runs `uv run pytest tests/ -q --tb=short`;
`-q` prints a dot per test and never a node id. Measured, not assumed —
against the fetched green log of the amendment's own run:

```
$ grep -c "TestOrdinaryProseSayingExampleIsUntouched" <fetched log of run 34163908081>
0
```

Fabricating a quote was not an option and neither was editing the workflow
(that is F2's fix, out of scope here and it would change what all six legs
report). What *is* in the log is an exact count identity, on all six legs,
between the run before the amendment and the run after it:

| leg | run 34163296496 (`5c27510`, before) | run 34163908081 (`12914e7`, after) | Δ |
|---|---|---|---|
| ubuntu 3.11 / 3.12 / 3.13 | `1909 passed, 4 skipped` | `1912 passed, 4 skipped` | **+3** |
| windows 3.11 / 3.12 / 3.13 | `1904 passed, 9 skipped` | `1907 passed, 9 skipped` | **+3** |

The amendment adds exactly three tests and all three are in
`TestOrdinaryProseSayingExampleIsUntouched`. Locally, the identical command
reproduces both sides to the test: `1909 passed, 4 skipped` with the file
stashed, `1912 passed, 4 skipped` with it. So the named class provably executed
in the green CI run — the log states it as arithmetic rather than as a name.

**The remedy is F2's one-line change**, and it now buys two things instead of
one: `modules/tool-delegate/tests` becomes visible, and `-rA` (or `-v`) would
make node ids quotable from CI at all. Filed for the manager; not done here.

---

## Suite and lint

| | main (`5f0f04b`, patch stashed) | branch |
|---|---|---|
| full suite (`uv run pytest -q`) | 4 failed, 2095 passed, 4 skipped | **2102 passed, 4 skipped, 0 failed** |
| CI's command (`uv run pytest tests/ -q --tb=short`) | — | **1912 passed, 4 skipped** |
| `tests/test_delegate_example_strip.py` | — | **14 passed** (was 11) |
| ruff check (changed file) | — | All checks passed |
| ruff format --check (changed file) | — | 1 file already formatted |

Main's four failures are precisely this lane's four fail-before assertions.
Branch failure set is **empty**, so ⊆ main's holds trivially. The branch's
`2099 → 2102` is the amendment's three tests and nothing else.

`ruff check .` across the whole repo reports 19 pre-existing errors, all in
files this lane never touched (`amplifier_foundation/updates/__init__.py`,
`docs/lanes/dfni-.../replay_step.py`, others). They are not introduced here,
and CI has no ruff leg at all — the six legs are pytest only.

**CI: 6/6 legs pass** on `12914e7` (run
[34163908081](https://github.com/microsoft/amplifier-foundation/actions/runs/34163908081)),
verbatim from the fetched log:

```
Tests (ubuntu-latest,  Python 3.11)  success   1912 passed, 4 skipped, 1 warning in 36.58s
Tests (ubuntu-latest,  Python 3.12)  success   1912 passed, 4 skipped, 1 warning in 32.03s
Tests (ubuntu-latest,  Python 3.13)  success   1912 passed, 4 skipped, 1 warning in 37.11s
Tests (windows-latest, Python 3.11)  success   1907 passed, 9 skipped, 1 warning in 56.58s
Tests (windows-latest, Python 3.12)  success   1907 passed, 9 skipped in 59.77s
Tests (windows-latest, Python 3.13)  success   1907 passed, 9 skipped, 1 warning in 65.60s
```

The single warning is pre-existing and unrelated — a `RuntimeWarning: coroutine
'AsyncMockMixin._execute_mock_call' was never awaited` raised by
`tests/test_subprocess_runner.py`, present in the previous run too. The
amendment's own file runs clean: `14 passed in 0.04s`, no warnings.

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
