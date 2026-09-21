# DONE-NOTE — lane bp0, `model_performance-bp0`

**W3-PREREQ (1 of 2, CONSUMER): tool-delegate per-delegate timeout must RETURN
partial results instead of discarding the sub-session's work.**

- Item: `model_performance-bp0` (project `model_performance`)
- Repo: `microsoft/amplifier-foundation`, branch `lane/bp0-delegate-timeout-partial-consumer`
- Parent commit: `5ebf1dab1fba33539e15499698f33cb3b9fc2b78`
- Outcome: **branch A — RESOLVED.** All deliverables DONE. No deliverable
  recorded NOT-POSSIBLE.
- Spend: **$0.00 of $0.00 authorized.** No API calls, no DTU, no infrastructure
  created, no ledger rows opened. The authority was $0 for a pure code change
  and the work needed none, so the arithmetic-closure rule in the goal never
  binds here — there is no run-buying deliverable to price. Residue: $0, and
  the smallest useful purchase it could not buy is *not applicable* (nothing in
  this item is purchasable).

---

## 1. Deliverable status

| # | Deliverable | Status |
|---|---|---|
| 1 | Siblings survive the straggler (k64 gate G-D1) | **DONE (pinned) — but it was ALREADY TRUE on the parent.** See §3, finding F-1. The fail-before test does NOT show siblings being discarded, because they are not. Reported honestly rather than staged. |
| 2 | Timed-out delegate's own result: `status: "timeout"`, no `"response"` key, `partial_available` boolean | **DONE.** Fail-before proves `partial_available` absent and `status == "timed_out"` on the parent. |
| 3 | Normal completions byte-identical | **DONE — shown, not asserted.** `diff` + `sha256` of the same probe section across the two trees; identical. §5. |
| 4 | Full foundation suite green | **DONE.** 1905 passed / 1 skipped / 1 pre-existing warning. Module suite 189, not APPLY.md's 59 — reconciled in §4. |
| 5 | Re-targeting recorded | **DONE.** §2, hunk by hunk. |
| 6 | Fail-before evidence committed + in PR body | **DONE.** `fail-before-probe.txt`, `fail-before-tests.txt`, `patch-apply-check.txt`. |
| 7 | Draft PR on origin | **DONE.** See `DONE.json` `publication` block (remote read-back). |
| 8 | This DONE-NOTE in the PR body | **DONE.** |

---

## 2. Re-targeting record

**The patch did not apply. This is the literal result, not a paraphrase**
(`patch-apply-check.txt`):

```
$ git apply --check PATCH-foundation-tool-delegate.diff
error: patch failed: modules/tool-delegate/amplifier_module_tool_delegate/__init__.py:20
error: modules/tool-delegate/amplifier_module_tool_delegate/__init__.py: patch does not apply
exit=1
```

**All 10 source hunks failed. Zero applied.** The base moved much further than
PR #350 alone: 37n verified at `cc7e23aa`, **which is not even an object in
this repository** (`git cat-file -t cc7e23aa` → `Not a valid object name`), and
the file grew from ~1400 lines at 37n's base to **2533** at `5ebf1da`. Nothing
was force-applied. Every hunk below was re-targeted by hand against the current
file.

### 2.1 Hunk-by-hunk

| 37n hunk | Target | Applied? | How re-targeted |
|---|---|---|---|
| H1 `@@ -20,6 +20,26 @@` module docstring | settings list + contract block | no | Re-anchored after main's much longer `settings.timeout` entry (which now documents the 14400 s Layer-3 backstop). Contract block reworded to state the RETURN-not-raise invariant explicitly. |
| H2 `@@ -35,7 +55,9 @@` imports | `import inspect`, `import time` | no | `inspect` **already present** — PR #350 added it. Only `import time` was needed. Adding `inspect` again would have been a duplicate import. |
| H3 `@@ -44,6 +66,71 @@` constants + `_build_incomplete_result()` | module level | no | Constants kept (`DEFAULT_PARTIAL_MAX_CHARS`). `_build_incomplete_result()` **replaced** by two additive helpers, `_partial_output_fields()` and `_partial_event_fields()` — see RT-1. Added `TIMEOUT_STATUS` (RT-2) and split the guidance string (RT-4). |
| H4 `@@ -129,10 +216,78 @@` settings + `_collect_partial()` | `__init__` + new method | no | `self.timeout` line differs entirely (main: `_validate_timeout(settings.get("timeout", 14400))`); left untouched, `partial_max_chars` added beside it. `_collect_partial()` ported **verbatim** and placed next to `_await_child_with_deadline` / `_cancel_and_detach_child`, the helpers it serves. |
| H5 `@@ -959,6 +1114,8 @@` `started_at` | `_spawn_new_session` | no | Renamed `leg_started_at` and placed immediately after `parent_session_id`, with a comment saying it is read only on the timeout path. |
| H6+H7 spawn timeout handler | `except _DelegateTimeoutExpired:` | no | Main's handler is structurally different from 37n's base (`_DelegateTimeoutExpired`, not `TimeoutError`; already returns a structured `output`; carries `recovery_msg` / `resumable` / `resume_status`). **Merged additively** rather than replaced — see RT-3. |
| H8 `resume_started_at` | `_resume_existing_session` | no | Same rename/placement as H5. Placed before `resume_agent` derivation, which #350-era code hoisted above the `try`. |
| H9+H10 resume timeout handler | `except _DelegateTimeoutExpired:` | no | Same merge as H6+H7. Main's version also conditionally omits `agent` when unresolvable; that behaviour preserved. |
| H11 new test file (289 lines) | `tests/test_delegate_timeout_partial.py` | n/a | Rewritten (12 tests, 341 lines) against main's `MagicMock` harness — 37n's `FakeCoordinator` lacks `session_state`, `_tool_dispatch_context`, `_tool_dispatch_contexts`, and `get`, all of which main's code paths now touch. |

### 2.2 Re-targeting decisions

**RT-1 — `_build_incomplete_result()` dropped; two additive helpers instead.**
37n's helper *constructs the whole* `ToolResult`, replacing main's timeout
output wholesale. That would have silently deleted `metadata.resumable`,
`metadata.resume_status`, and `metadata.recovery_message` — the contract
`14d5a52` deliberately shipped. `_partial_output_fields()` /
`_partial_event_fields()` return only the *new* keys, spliced into main's
existing dicts with `**`. Nothing incumbent was removed.

**RT-2 — `status` changed `"timed_out"` → `"timeout"`. THE ONE BREAKING
CHANGE IN THIS PR; flag it in review.**
k64's gate G-D4 is quoted verbatim in the item: *"every observed timeout
result carries status `"timeout"`"*. `"timed_out"` is not that string and is
not a substring of it, so k64 would fail G-D4 on a string mismatch even after
both halves land. Blast radius measured before changing it:

```
$ grep -rn "timed_out" . | grep -v modules/tool-delegate     # foundation repo
(no matches)
$ grep -rn "timed_out" <installed amplifier_app_cli>
(no matches)
```

Only this module's own code, its own README, and its own tests. Changed on
**both** channels (tool result output and the `delegate:error` payload) so the
two never disagree, via a single constant `TIMEOUT_STATUS` — reverting is a
one-line change if a reviewer or the k64 harness prefers the incumbent string.
10 assertions in `test_delegate_timeout.py` and one README line updated.

**RT-3 — additive merge, not replacement, in both handlers.** New top-level
keys: `completed`, `partial_available`, `partial_response`, `partial_segments`,
`partial_source`, `partial_truncated`, `partial_chars_total`, `guidance`. New
metadata key: `elapsed_s`. Everything already there is unchanged.

**RT-4 — 37n's `_INCOMPLETE_GUIDANCE` rewritten because it CONTRADICTED main.**
37n's text says *"Either resume the sub-session with the 'session_id' above"*.
Main's `recovery_message` says *"do not resume this session until cleanup and
persistence complete"* and sets `resumable: false`. Shipping both would have
handed the model two opposing instructions in one payload. The guidance was
rewritten to cover only what the partial text *is* (unfinished work, not a
result) and to defer to `metadata.recovery_message` on resumption. Split into
`_PARTIAL_GUIDANCE` / `_NO_PARTIAL_GUIDANCE` so the no-partial case does not
point at a `partial_response` that is `None`.

**RT-5 — 37n's top-level `timeout_s` dropped.** Main already carries
`metadata.timeout_seconds`. A second key with the same value under a different
name is a trap for consumers. `elapsed_s` is genuinely new, so it was added
(to `metadata`, beside `timeout_seconds`, and to the event payload, where
gate G-D3 needs it).

**RT-6 — 37n's event key `reason: "timeout"` dropped.** Main already emits
`error_type: "delegate_timeout"` plus `status`. The test was re-targeted to
assert those instead.

**RT-7 — 37n's `test_timeout_default_remains_disabled` NOT ported.** It asserts
`tool.timeout is None`. `14d5a52` ("bound delegated sessions by default, Layer 3
wall-clock backstop, 14400s") deliberately changed that default *after* 37n's
base. Porting the assertion would have silently reverted that commit. Replaced
with `test_partial_max_chars_default_is_shipped_not_swept`, which pins only
what this change introduces and asserts `tool.timeout == 14400` to make the
non-regression explicit. The timeout default remains owned by
`test_delegate_timeout.py::test_timeout_defaults_only_when_key_is_absent`.

**RT-8 — one test added beyond 37n's 11.**
`test_resume_timeout_carries_the_same_partial_contract`. Main has *two* timeout
call sites; 37n's tests only covered spawn. An uncovered second call site is
exactly how the two paths drift apart.

---

## 3. Findings

### F-1 (material, for k64) — gate G-D1 was ALREADY satisfied on the parent commit

The item and goal describe two harms. **Only one of them still existed.**

`14d5a52` (landed after 37n's base) already made the timeout handler *return* a
structured `ToolResult` instead of raising. Measured on `5ebf1da`, unmodified
(`fail-before-probe.txt`, probe C — the real `asyncio.gather` shape, no
`return_exceptions`):

```
--- C. G-D1 sibling survival ---
gather returned; completed siblings surviving = 2 of 2;
discarded-completed-sibling count = 0
```

So: **there is no fail-before test proving siblings are discarded, because on
`5ebf1da` they are not.** Writing one that "failed" would have required
staging a defect that no longer exists. What this PR contributes to G-D1 is a
**regression pin** —
`test_straggler_returns_rather_than_raises_so_siblings_survive` — so the
property cannot silently regress.

Note the trap in the raw pytest output: that test *does* appear in the
fail-before FAILED list (`fail-before-tests.txt`). It fails on its
`status == "timeout"` assertion (deliverable 2), **not** on sibling survival.
The probe is the evidence for G-D1; the pytest run is not. k64's measured
1.2% (4/533) harm rate for discarded siblings predates `14d5a52` and should be
re-derived before it is quoted as current.

### F-2 — the real remaining gap was deliverable 2, and it was fully unmet

On `5ebf1da`, both timeout paths: `status == "timed_out"`, `partial_available`
**absent entirely**. `"response"` was already correctly absent. 11 of 12 new
tests fail on the parent; the 1 that passes is
`test_success_result_carries_no_partial_keys` — which is the byte-identity
guard and is *supposed* to pass on both sides.

### F-3 — `partial_available` is `false` for every timeout in this repo, by design

Nothing registers a `session.partial` capability in amplifier-foundation, so
`_collect_partial()` always degrades to no-partial. This is the documented,
correct state until the app-cli producer half (`model_performance-9w0`) lands.
**k64 will hit gate G-D4's stop condition — `PARTIAL-PATH-NOT-EXERCISED` — if
it runs with only this half merged.** Not faked, per the goal's explicit
instruction.

### F-4 — CI does not run these tests (pre-existing, not this lane's to fix)

`.github/workflows/ci.yml` runs `uv run pytest tests/ -q --tb=short`.
`pyproject.toml` sets `testpaths = ["tests", "modules/tool-delegate/tests"]`,
but the explicit `tests/` argument overrides it. **Every test in this PR, and
all 177 pre-existing module tests, are invisible to CI.** Verified locally
instead; the exact commands are in §4.

---

## 4. Verification

```
$ uv run pytest -q                       # patched tree
1905 passed, 1 skipped, 1 warning in 21.33s

$ git stash -u && uv run pytest -q       # parent 5ebf1da
1893 passed, 1 skipped, 1 warning in 21.91s

$ uv run pytest modules/tool-delegate/tests -q
189 passed in 0.74s

$ uv run ruff check modules/tool-delegate/ docs/lanes/bp0-.../
All checks passed!
```

Delta is exactly **+12**, the 12 new tests. The 1 warning is the pre-existing
`RuntimeWarning` in `tests/test_subprocess_runner.py`, present on both sides.

**Reconciliation of APPLY.md's "59 passed (48 pre-existing + 11 new)".** Both
numbers are stale. The module suite has **177** pre-existing tests at `5ebf1da`,
not 48 — it grew by ~129 across the return-contract, call-budget,
spawn-matrix-provenance and resume-routing work that landed after 37n's base.
12 new tests (11 ported + RT-8) gives **189**, and 189 is what runs. The
predicted 59 was never reachable at this base.

## 5. Byte-identity for normal completions — shown, not asserted

Two independent lines of evidence.

**Empirical** (`byte-identity-normal-completion.txt`). The probe's section D
runs a *normal* delegate completion and prints both the output dict and the
exact serialized string the model receives. Same script, parent tree vs
patched tree, `diff`ed verbatim:

```
$ diff <(sed -n "/^--- D./,$p" fail-before-probe.txt) \
       <(sed -n "/^--- D./,$p" after-probe.txt)
(no output -- IDENTICAL)

18dfd629b68861f7c11e2182a6d730f5e26cef39d48b8d4a9396b63b587a0ab1  D-before.txt
18dfd629b68861f7c11e2182a6d730f5e26cef39d48b8d4a9396b63b587a0ab1  D-after.txt
```

**Structural.** `git diff` on the source file removes exactly **6** lines, and
every one of them is a `"timed_out"` status literal or a timeout message
f-string:

```
-                f"(delegate tool session-level timeout). {recovery_msg}"
-                        "status": "timed_out",
-                    "status": "timed_out",
-                f"(delegate tool session-level timeout). {recovery_msg}"
-                    "status": "timed_out",
-                "status": "timed_out",
```

Zero lines removed from the success-return path. `partial_max_chars` and
`_collect_partial()` are reachable only from inside
`except _DelegateTimeoutExpired:`. `leg_started_at` is one `time.monotonic()`
call per leg, read only on timeout.

Also pinned as a test:
`test_success_result_carries_no_partial_keys` asserts no key starting with
`partial` and no `completed` key on a success result — and it is the one test
that passes on **both** the parent and the patched tree.

## 6. Deviations from the goal

1. **Deliverable 1's fail-before could not be produced as specified**, because
   the harm it describes no longer exists on the parent commit (F-1). Recorded
   as a finding with the measurement, not worked around.
2. **RT-2 is a breaking change to a shipped string.** The goal did not ask for
   one; it asked for `status: "timeout"`, which on this base *is* one. Called
   out here and in the PR body so a reviewer can push back explicitly rather
   than discover it.
3. **One test beyond 37n's 11** (RT-8), covering the resume timeout path.

## 7. What remains open

- **k64 stays blocked.** The producer half, `model_performance-9w0` in
  `amplifier-app-cli`, must land before `partial_available` can ever be `true`
  and before G-D4 can pass. Not touched by this lane (explicit scope-out).
- **Do not enable `settings.timeout` sweeps or run k64's eval yet** — separate,
  separately funded items.
- **F-4 (CI excludes `modules/tool-delegate/tests`)** is unfiled here; it is a
  pre-existing gap in another owner's scope.
- **RT-2 needs a decision** from whoever owns k64's harness: keep
  `status: "timeout"` (this PR) or revert `TIMEOUT_STATUS` to `"timed_out"` and
  teach the harness the incumbent string. One line either way.

## 8. Artifacts in this directory

| File | What |
|---|---|
| `DONE-NOTE.md` | this note |
| `probe_timeout_contract.py` | the behavioural probe; runs against any importable tree |
| `fail-before-probe.txt` | probe output on `5ebf1da`, unmodified |
| `after-probe.txt` | probe output on the patched tree |
| `fail-before-tests.txt` | `pytest` on `5ebf1da`: 11 failed, 1 passed |
| `byte-identity-normal-completion.txt` | the `diff` + `sha256` of §5 |
| `patch-apply-check.txt` | `git apply --check` refusing 37n's diff |
