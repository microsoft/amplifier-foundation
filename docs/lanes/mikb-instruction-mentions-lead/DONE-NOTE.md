# DONE-NOTE — `model_performance-mikb`

Lane: `mikb-instruction-mentions-lead`
Repo: `microsoft/amplifier-foundation`, branch `lane/mikb-instruction-mentions-lead`
Draft PR: <https://github.com/microsoft/amplifier-foundation/pull/359>
Base: `origin/main` @ `e681764`
Date: 2026-09-06

## OUTCOME: **A — RESOLVED.** All deliverables DONE. Nothing NOT-POSSIBLE. Cap did not bind.

**Landing stage.** This lane's final state is the draft PR, marked ready. Procedure 4
forbids merging; the manager re-runs the fail-befores and merges. The live installed
CLI on this host still imports the unpatched `amplifier_foundation` — that is the
landing stage, not an incomplete deliverable.

---

## Deliverables

| # | Deliverable | State |
|---|---|---|
| 1 | GATE: verify `format_context_block` emits in INSERTION order **by reading it** | **DONE** |
| 2 | Fail-before unit tests | **DONE** (3 fail-before + 1 no-regression guard — see the honesty note) |
| 3 | Do NOT inline mention content into `main_instruction` | **DONE** |
| 4 | `mentions:resolved` semantics unchanged; order change noted explicitly | **DONE** |
| 5 | REAL-SESSION CHECK on this host (source override, no cache edits) | **DONE** |
| 6 | ONE amplifier-tester DTU smoke, foundation branch pinned; DTU destroyed, ledger 0 open | **DONE** |
| 7 | DRAFT PR, marked ready, with a verification comment | **DONE** |
| 8 | DONE-NOTE.md at the lane artifact root | **DONE** (this file) |

---

## 1. THE GATE (done first, as instructed) — the approach is valid

`format_context_block` does **not** sort. Read, not assumed:

- `amplifier_foundation/mentions/loader.py:42` — `unique_files = deduplicator.get_unique_files()`,
  then `for cf in unique_files:` at `:56`, joined `"\n\n"` at `:75`. No `sorted()`, no key.
- `amplifier_foundation/mentions/deduplicator.py:63-70` — `get_unique_files()` is a list
  comprehension over `self._content_by_hash.items()`, a plain `dict` (`:24`). Python dicts are
  insertion-ordered (3.7+), and `add_file` (`:43-47`) inserts on first sight of a content hash.

**Conclusion: emission order == deduplicator insertion order.** The swap is therefore
sufficient, and no explicit ordering fix inside `format_context_block` was required.

## 2. The change

One reordering in `amplifier_foundation/bundle/_prepared.py::_create_system_prompt_factory`:
resolve the instruction's `@mentions` into the `ContentDeduplicator` **before** adding
`captured_bundle.context` includes. A comment marks the ordering as load-bearing so a future
refactor does not silently undo it.

Nothing else moved. `main_instruction` is untouched; no content is inlined; the
`f"{main_instruction}\n\n---\n\n{all_context}"` shape is unchanged.

## 3. Tests — stated honestly

`tests/test_instruction_mentions_lead_context.py`, 4 tests. POSIX + Windows clean
(`tmp_path` + `pathlib` only; no shell, no platform branch, therefore **no `skipif`** —
the gate four PRs in this batch are stuck on does not apply here).

Fail-before proof, produced by stashing **only** `_prepared.py` (the test file stayed):

```
=== ON MAIN CODE ===
FF.F
FAILED test_instruction_mention_is_the_first_context_block
FAILED test_double_referenced_file_emitted_once_mention_leads
FAILED test_mentions_resolved_payload_semantics_unchanged
3 failed, 1 passed in 0.04s

=== ON BRANCH ===
4 passed in 0.23s
```
(raw: `evidence/fail-before-main.txt`)

**3 fail-before, 1 no-regression guard.** The goal asked for three tests that all fail on
main, one of them being "no instruction mentions → byte-identical output vs main". That one
**cannot** fail on main: a test asserting output equals main's output passes on main by
construction. Making it fail would mean pinning the bug rather than the invariant. So it is
shipped as a guard and the byte comparison is carried out separately, below. Reported rather
than fudged.

`test_mentions_resolved_payload_semantics_unchanged` is the useful accident: on main it fails
only on its **last** line (the ordering assertion). Every assertion above it — the resolution
set, the `failed` list, `deduplicated_count` — **passed on main**. That is direct evidence that
the event's *set* semantics are unchanged and only *order* moved.

### Byte-identity, main vs branch (`evidence/byte-identity-main-vs-branch.txt`)

Same script, fixed paths, run against both revisions:

```
### MAIN (e681764, context includes first)
A_no_instruction_mentions:  sha256=d7f616c6a3088e55f2a21372213acd87438025eb51abf849cb94015846635c34 len=247
B_with_instruction_mention: sha256=1b5d114da8f515287d011564fee150c3be2d8792271ab404616d8e8f753458c0 len=379 'You are Amplifier'@offset=333
### BRANCH
A_no_instruction_mentions:  sha256=d7f616c6a3088e55f2a21372213acd87438025eb51abf849cb94015846635c34 len=247
B_with_instruction_mention: sha256=25bcb36828224b93624ecfe9719ee9cd4f32144a69c834b3ee3b68bc2db82fec len=379 'You are Amplifier'@offset=123
```

No-mention case: **identical sha256**. Mention case: **identical length** (379 both) — nothing
added, nothing dropped, only reordered.

### Full suite

`uv run pytest tests/ -q` → **1787 passed, 1 skipped, 1 failed**.

The single failure, `tests/test_sources.py::TestFileSourceHandler::test_resolve_existing_file`
(`assert PosixPath('/tmp') == PosixPath('/tmp/tmpXXXX')`), is **pre-existing on `origin/main`
on this host** — confirmed by re-running that one test with `_prepared.py` stashed, where it
fails identically. It is a file-source-root assertion, untouched by this change.

## 4. `mentions:resolved` — what changed and what did not

Unchanged: the resolution **set**, the `failed` list, `deduplicated_count`, and the event name.
Changed: the **order** of the `resolutions` list — instruction mentions now lead. Called out in
the PR body rather than left for a consumer to discover.

Doubly-referenced file (both a `context:` include and an instruction mention): still emitted
**once**; its `paths=` label now **leads** with the author's explicit `@mention`, with the
include's own attribution trailing. No attribution is dropped — "the mention wins" is
implemented as "the mention leads", which is the minimal change consistent with
*"the blocks stay blocks; only their order changes"*.

## 5. REAL-SESSION CHECK on this host

Method: **`PYTHONPATH` source override**, not a cache edit. `~/.amplifier/cache` was **never
touched**. Verified first that the installed `amplifier_foundation` is **byte-identical to
`origin/main`** except for the single file this lane changed (`diff -rq` → one differing file),
so BEFORE (no `PYTHONPATH`) vs AFTER (`PYTHONPATH=<worktree>`) is an exact one-file A/B.

`amplifier run "hi" --mode single`, cwd `/tmp/mikb-realsession`, Anthropic `claude-opus-5`.
Extraction is jq slices/offsets/tallies only — the ~97k-char line was never loaded
(`evidence/probe_session.sh`, raw output `evidence/real-session-before-after.txt`).

### `anchors-amp-dev`

| metric | BEFORE | AFTER |
|---|---|---|
| `raw.system` chars | 96,898 | **96,898** (identical) |
| `<context_file>` blocks | 23 | **23** (identical) |
| FIRST block `paths=` | `gitea:context/gitea-awareness.md` | **`@anchors-amp-dev:context/system.md`** |
| offset of `"You are Amplifier"` | **72,491** | **225** |
| offset of `configured for development OF` | 72,510 | 244 |
| within first ~1,500 chars | no | **yes** |
| `mentions:resolved` | 23 resolutions / 0 failed | 23 / 0 |
| notify README present | no | no |

### `anchors` (plain)

| metric | BEFORE | AFTER |
|---|---|---|
| `raw.system` chars | 96,344 | **96,344** (identical) |
| `<context_file>` blocks | 23 | **23** (identical) |
| FIRST block `paths=` | `modes:context/modes-instructions.md` | **`@anchors:context/system.md`** |
| offset of `"You are Amplifier"` | **72,467** | **201** |
| `mentions:resolved` | 23 / 0 | 23 / 0 |

The goal's quoted 72,012 (session `7132cbfd`) reproduces here as **72,491** — the small drift is
bundle-content drift between that capture and today, not a different defect. Same block count,
same shape, same 73–75% depth.

### FINDING reported, not absorbed

The goal's premise — *"run the plain `anchors` bundle once to confirm no regression when the
root has an inline body and no mentions"* — **is false on this host.** `anchors`'s instruction
is also `@anchors:context/system.md`, so `anchors` had the **same** defect and gets the **same**
fix. There is no "inline body, no mentions" root bundle among the two named. The
no-mention no-regression case is covered instead by the unit guard and the sha256 comparison
above, which is the stronger evidence anyway.

## 6. DTU smoke (one environment, cap respected)

Delegated to `amplifier-tester:setup-digital-twin` with an explicit ledger (live=0, cap=1,
remaining-after=0). Instance `mikb-foundation-order`. Full report:
`evidence/dtu-smoke-results.md`; profile `evidence/dtu-profile-mikb-foundation-order.yaml`.

The smoke went beyond the ask: it produced a **real before-arm inside the same DTU** by pinning
the mirror to `e681764`, reinstalling, measuring, and restoring.

| `anchors-amp-dev` (DTU) | BEFORE `e681764` | AFTER (branch) |
|---|---|---|
| `raw.system` chars | 43,564 | **43,564** (identical) |
| `<context_file>` blocks | 9 | **9** (identical) |
| instruction's mention position | **block 9 of 9 (LAST)** | **block 1 of 9 (FIRST)** |
| offset of `"You are Amplifier"` | 19,232 | **214** |
| `mentions:resolved` | 9 / 0 failed | 9 / 0 failed |

| `anchors` (DTU) | BEFORE | AFTER |
|---|---|---|
| `raw.system` chars | 40,732 | **40,732** (identical) |
| blocks | 8 | **8** |
| mention position | **8 of 8 (LAST)** | **1 of 8 (FIRST)** |
| offset of `"You are Amplifier"` | 16,930 | **190** |

Both providers wired and exercised (`anthropic/claude-sonnet-5`, `openai/gpt-5.6-sol`), both
bundles on each, all exit 0, no startup regressions, `ready: true`.

**Deviations the sub-agent flagged, adjudicated here and accepted:**
1. **Provider config is not a verbatim host mirror** — the host declares 15 providers, several
   referencing `${ANTHROPIC_BASE_URL}` which is unset here and would render empty. A minimal
   two-provider config was substituted. Accepted: the claim under test is *emission order*, not
   provider fan-out.
2. **App-bundle set is 2, not the host's 19** — so DTU block counts are 8/9 and sizes ~41–44k,
   **not comparable to the host's 23 blocks / ~97k**. Accepted and stated: the DTU proves the
   ordering and the absence of startup regressions; the host run carries the magnitudes.
3. The shared `admin/amplifier-foundation` Gitea mirror was deliberately **not** touched (it
   holds another lane's snapshot); a separate repo was used. Accepted.
4. `amplifier update` alone did **not** downgrade the Python package — it refreshed the bundle
   cache to `e681764` while site-packages stayed newer, a split state that would have produced a
   **false before-arm**. Caught via `direct_url.json` and forced with
   `uv tool install --reinstall --force`. Worth knowing for any future lane pinning a foundation
   ref in a DTU.
5. Three warm-up run costs were not captured verbatim; their ~$0.19 is an estimate. The other
   seven costs are quoted from the CLI's own output.

### Teardown — 0 open rows

```
ADDED:   type=dtu id=mikb-foundation-order status=open
CLAIMED: id=mikb-foundation-order lane=mikb-instruction-mentions-lead
TEARDOWN: lane=mikb-instruction-mentions-lead verified-gone=1 rows-flipped=1
  already-absent=0 failed=0 live-skipped=0 unknown-skipped=0 unverifiable=0 protected-untouched=0
```

`lane_teardown.sh` was used with the **FULL** lane name. `infra_ledger.sh ... sweep` was **never**
run. `PROTECTED — 0` (no foreign rows were in scope). Ledger rows matching `mikb`: `swept`, and
**0 open rows owned by this lane**.

Also cleaned up by this lane, beyond the ledger row:
- Gitea repo `admin/amplifier-foundation-mikb` — deleted (`HTTP 204`); the 8 pre-existing repos,
  including the shared `admin/amplifier-foundation`, are untouched.
- Gitea instance `gitea-55cf7007` — **reused, not created**, therefore left running.
- Credential scratch (`gitea-token.json`, `env.sh`) — removed from the lane directory.
- Two pre-existing DTUs belonging to other sessions (`steward-desk-test`, `wayfinder-scout-eval`)
  — **left alone**.

## 7. SPEND

**Cap: $3.00.** Goal arithmetic: `1 DTU smoke × ~$1.00 / 1.00 valid = $1.00`, plus `$2.00` slack.

**Re-stated at the observed price, before the smoke and again after:**
`1 smoke × $0.74 observed / 1.00 valid (first launch was valid, no retry) = $0.74` — under the
$1.00 estimate. **The arithmetic closes; the cap did not bind and no deliverable was dropped.**

| item | cost |
|---|---|
| host `amplifier run "hi"` × 4 (anchors-amp-dev before/after, anchors before/after) @ $0.27 | $1.08 |
| DTU smoke, 10 sessions (7 observed $0.55 + 3 warm-ups estimated $0.19) | ~$0.74 |
| unit tests, byte-identity probe, code, teardown | $0.00 |
| **total** | **~$1.82** |

Residue **~$1.18** of $3.00. Recorded per procedure 3, which classes the local
`amplifier run "hi"` checks as normal local invocations rather than run-buying purchases; even
counted against the cap in full, the total lands under it.

## 8. Deviations / choices recorded (no human was waited on)

1. **Test 2 ships as a no-regression guard, not a fail-before** — impossible as specified; see §3.
2. **"The mention wins the `paths=` label" implemented as "the mention leads"** — the include's
   attribution still trails, so no information is lost. Chosen because the goal also says only
   the *order* may change.
3. **Source override via `PYTHONPATH`**, not a scratch venv — cheaper, and verified to be an
   exact one-file A/B against the installed package. `~/.amplifier/cache` untouched.
4. **`anchors` was run twice (before and after), not once** — the goal said once, but the before
   arm is what turned "no regression" into a measured claim, and it exposed the false premise in
   §5. Cost of the extra run: $0.27.

## 9. What remains open for the manager

- **Merge.** The PR is ready-for-review, not merged. Re-run the fail-befores against
  `_prepared.py` stashed; expect `3 failed, 1 passed`.
- **The anchors DRY refactor is explicitly out of scope for this lane** and untouched. This lane
  changed foundation only — one file, one reordering — so the two remain attributable.
- The pre-existing `test_sources.py` failure on this host is unrelated and unfixed here.
