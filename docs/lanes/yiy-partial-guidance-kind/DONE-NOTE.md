# DONE-NOTE — `model_performance-yiy`

**Lane:** `yiy-partial-guidance-kind` · **Repo:** `microsoft/amplifier-foundation` ·
**Branch:** `lane/yiy-partial-guidance-kind` · **Parent commit:** `5d8db2fa4715e4dfe7d3b6604d725de4c291ec9d`

**Outcome: branch A — RESOLVED.** Every deliverable is DONE. Nothing was
NOT-POSSIBLE; nothing was dropped. Spend: **$0.00** against an authority of
**$0.00** (see §Spend).

---

## What was wrong, in one paragraph

`modules/tool-delegate` picked its timeout guidance string from `bool(text)`
alone, so a partial recovered from the **reasoning** channel received the
sentence written for unfinished **prose**: *"the text in 'partial_response' is
unfinished work salvaged from the agent mid-flight — it has NOT been checked,
concluded, or self-reviewed by that agent."* That is true of prose the agent was
writing for a reader and did not finish. It is false of raw private reasoning,
which was never addressed to a reader at all — and framing it as unreviewed
draft output invites the calling model to read it as a draft answer.

This became reachable only after app-cli `8c83a9b` (PR #298) widened the
accumulator to recover `thinking` + `tool_call` traces when no assistant text
exists. Before that commit the case could not occur; after it, it is the common
one (k64's 18 legs: recoverable window 0.05% → 82.2% of a leg).

## The change

`modules/tool-delegate/amplifier_module_tool_delegate/__init__.py`, **+64 / −2**,
one file, no other file in the repo touched:

- new constant `_REASONING_PARTIAL_GUIDANCE`
- new constant `_REASONING_PARTIAL_SOURCES = frozenset({"spawn-accumulator:reasoning"})`
- new selector `_guidance_for(text, source)` — three cases, in order:
  no text → `_NO_PARTIAL_GUIDANCE`; recognised reasoning source →
  `_REASONING_PARTIAL_GUIDANCE`; everything else → `_PARTIAL_GUIDANCE`
- `_partial_output_fields` reads `source` once and calls the selector

eem scoped this as *"two lines and a constant"*. The executable change is
exactly that; the remaining lines are the two guidance/selection docstrings and
the comment recording **why** exact-match was chosen (below).

### The one judgement call, and why

**Exact match on `"spawn-accumulator:reasoning"`, not a `:reasoning` suffix
test.** The deliverable requires that an unknown or absent `partial_source`
*must not* silently receive the reasoning frame. The producer is a separate repo
on its own release cadence, so exact match is the reading that satisfies that
literally: an unrecognised value degrades to the incumbent behaviour rather than
inheriting a frame that may be wrong for it. **The cost is stated rather than
hidden:** if app-cli ever renames the source, the reasoning frame silently
reverts to today's prose frame — i.e. back to this defect. Recorded here and in
a source comment so the next reader sees the tradeoff rather than rediscovering
it. No human decision was waited on.

`source` is typed `Any` and compared, never parsed, so a non-string value
(`None`, `int`, `list`, `dict`) cannot raise on the timeout path — the one path
where raising would discard every completed sibling in a parallel batch.

## Deliverables

| # | Deliverable | State | Evidence |
|---|---|---|---|
| 1 | Reasoning-kind partial gets a guidance string describing what it actually is | **DONE** | `_REASONING_PARTIAL_GUIDANCE`; `after.txt`; 3 tests |
| 2 | Text case BYTE-IDENTICAL to today | **DONE** | `byte-identity.txt` — sha256 `b1d9796d1a9adf29`, len 416, parent == now |
| 3 | No-partial case unchanged | **DONE** | `byte-identity.txt` — sha256 `d73f51f164c545d3`, len 245, parent == now |
| 4 | Fail-before test, run against the parent, both outputs pasted | **DONE** | `fail-before.txt` (3 failed / 16 passed) → `after.txt` (19 passed) |
| 5 | Unknown/absent `partial_source` does not crash, does not get the reasoning frame | **DONE** | 11 parametrised cases + absent-key case, all green |
| 6 | Full foundation suite green, before/after counts stated | **DONE** | 1939 → **1958** passed, 1 skipped both sides |
| 7 | DRAFT PR on origin naming app-cli `8c83a9b` | **DONE** | see `publication` in `DONE.json` |
| 8 | DONE-NOTE at the lane artifact root | **DONE** | this file |

## Fail-before → after (deliverable 4)

Run on the parent commit `5d8db2f` with the module **unchanged** and only the new
test file present. Full transcripts in `fail-before.txt` and `after.txt`.

```
=== FAIL-BEFORE: parent commit 5d8db2fa4715e4dfe7d3b6604d725de4c291ec9d, module UNCHANGED ===
$ uv run pytest tests/test_partial_guidance_kind_yiy.py -q
FAILED tests/test_partial_guidance_kind_yiy.py::test_reasoning_partial_is_not_framed_as_unfinished_prose
FAILED tests/test_partial_guidance_kind_yiy.py::test_reasoning_guidance_says_what_the_payload_actually_is
FAILED tests/test_partial_guidance_kind_yiy.py::test_reasoning_kind_is_honoured_on_the_resume_path_too
3 failed, 16 passed in 0.31s

=== AFTER: with the fix applied ===
$ uv run pytest tests/test_partial_guidance_kind_yiy.py -q
19 passed in 0.26s
```

**The 16 that passed on the parent are the point of the design.** They are the
byte-identity, no-partial, and unknown-source pins — they must pass on *both*
sides, or the file would merely be asserting the new behaviour everywhere and
would prove nothing about what stayed still. Exactly the 3 reasoning-kind tests
move.

## Byte-identity, checked honestly (deliverables 2 and 3)

Not asserted from the module's own constants (that would be tautological). The
parent blob was extracted with `git show HEAD:…`, its two constants parsed out
with `ast.literal_eval`, and compared byte-for-byte against this build —
**and** the parent's selector was re-implemented and run against this build's
actual output for every case meant to be unchanged. `byte-identity.txt`:

```
_PARTIAL_GUIDANCE     parent sha256=b1d9796d1a9adf29 len=416 | now sha256=b1d9796d1a9adf29 len=416 | BYTE-IDENTICAL = True
_NO_PARTIAL_GUIDANCE  parent sha256=d73f51f164c545d3 len=245 | now sha256=d73f51f164c545d3 len=245 | BYTE-IDENTICAL = True

runtime: this build's guidance vs what the PARENT selector would return
  text partial (spawn-accumulator)     unchanged-vs-parent = True
  text partial (source absent)         unchanged-vs-parent = True
  text partial (unknown source)        unchanged-vs-parent = True
  text partial (non-str source)        unchanged-vs-parent = True
  no partial                           unchanged-vs-parent = True
  reasoning partial (CHANGES)          unchanged-vs-parent = False
```

Exactly one case changed. That case is the deliverable.

The test file additionally spells both incumbent strings out as **literals**
rather than importing the constants, so a future reword of a constant fails the
test instead of silently redefining "unchanged". app-cli's
`test_guidance_string_is_unchanged_for_the_text_case` pins the same bytes from
the producer side; the two now fail together.

## Test suite (deliverable 6)

| Run | Command | Result |
|---|---|---|
| Before (parent `5d8db2f`, clean tree) | `uv run pytest -q` | **1939 passed, 1 skipped** |
| After (this branch) | `uv run pytest -q` | **1958 passed, 1 skipped** |
| After, CI's own subset | `uv run pytest tests/ -q --tb=short` | **1769 passed, 1 skipped** |

+19 = exactly the 19 new tests. Zero pre-existing tests changed state. The
before figure matches the goal's KNOWN section (1939/1 at `5d8db2f`).

**Test placement is deliberate.** The new file is `tests/test_partial_guidance_kind_yiy.py`,
not `modules/tool-delegate/tests/`. CI runs `uv run pytest tests/ -q --tb=short`
(`.github/workflows/*.yml:35`), which excludes the module's own test directory —
a test placed only there would never run in CI. Same reasoning 67u used. The
local `testpaths` covers both, which is why the full-suite figure is larger than
the CI figure.

## Spend

**$0.00 spent against an authority of $0.00.** The goal's arithmetic —
`0 runs × 0 arms × $0 / 1.00 = $0.00`, slack $0.00 — closes trivially and
correctly: this is a pure source change with no container, no DTU, and no eval
spend. No API calls were bought. No infrastructure was created, so no row was
added to the infra ledger and no teardown was run. The cap never bound and no
deliverable was reduced by it.

## Deviations and scope decisions

1. **The `timeout_msg` error string was left alone.** Both timeout call sites
   (spawn `:2276`, resume `:2676`) also branch on `partial.get("text")` to
   compose the human/log message *"Partial output was preserved … it is
   UNFINISHED, not a result."* That sentence is **true of reasoning too** and
   does not contain the offending "not checked, concluded, or self-reviewed"
   clause, so it is not the defect. Changing it would have exceeded eem's scope
   and put the byte-identity pin at risk in passing. Left as-is, deliberately.
2. **`amplifier-app-cli` was not modified**, per scope-out. Its half is merged.
   Note for the record: the app-cli copy installed on this host predates
   `8c83a9b` (its `get_partial_output` returns only `"spawn-accumulator"`), so
   the producer values used here are the ones the item and goal specify, not
   ones read off this host's installed copy. This is a consumer-side change and
   is fully exercised by its own tests; no cross-repo runtime check was in
   scope or was performed.
3. **No repo-root `DONE-NOTE.md`** was created or modified (item kez). All lane
   artifacts are under `docs/lanes/yiy-partial-guidance-kind/`.
4. Nothing was merged; nothing outside this lane's owned paths was touched.

## Artifacts in this directory

| File | What it is |
|---|---|
| `DONE-NOTE.md` | this note |
| `fail-before.txt` | verbatim pytest output on the parent commit, module unchanged |
| `after.txt` | verbatim pytest output with the fix applied |
| `byte-identity.txt` | verbatim output of the parent-vs-now constant and selector comparison |
