# zc6t — DONE-NOTE

Item `model_performance-zc6t` · lane `zc6t-lean-head-ship` · 2026-09-07
Repo `microsoft/amplifier-foundation` · branch `lane/zc6t-lean-head-ship` · **PR #372**

**TERMINAL OUTCOME: RESOLVED (branch A).**
The lane's two in-repo deliverables are DONE and shipped as a draft PR. The other
21 are recorded NOT-POSSIBLE-IN-THIS-LANE for a **goal-defect** reason — GOAL.md's
own clause directs the lane to report it, ship the patch as an artifact, and
*resolve* — and their patches are shipped under this artifact root. **The cap was
never the binding constraint: $0 of $15 was spent.** Not branch C: nothing here
is blocked by a missing prerequisite, a refused claim, or a broken dependency.

---

## 1. What was executed

* Read the spec item in full; read `00-what-we-know.md`; read
  `leanhead_shim.py`, `v1_tools.json` (13 tool descriptions), `v1_instructions.json`
  (12 spans) and the captured stock head (`head/instructions_stock.txt`,
  `head/tools_stock.json`).
* Verified the goal's anchor→file mapping for all 12 spans rather than
  re-deriving it; confirmed span 9's target was split by ux32 and span 10/11 are
  hook-generated.
* **Applied both in-repo rewrites**, byte-verified against v1.
* **Diffed stock vs lean for all 23 targets** with a purpose-built rule
  extractor; hand-reviewed all 4 flags it raised (F4).
* **Generated drop-in patches for all 19 out-of-repo targets that change** (10
  tool descriptions + 9 context files; plus 2 out-of-repo no-ops and 2 in-repo).
* **Built the CI guardrail and proved it RED then GREEN** on real GitHub Actions
  runs across 6 OS/Python legs.
* **Took a real-session head census on the wire, before and after**, at $0.

## 2. Deliverables

### HALF A — 13 tool descriptions

| Tool | Repo | Verdict |
|---|---|---|
| `delegate` (**preamble only**) | amplifier-foundation | **DONE** — 1,190 → **938 chars, byte-identical to v1**, shipped in PR #372 |
| `web_search` | amplifier-module-tool-web | **DONE (verified no-op)** — stock IS the v1 text, 30 chars, identical. Skipped, per goal. |
| `mode` | amplifier-bundle-modes | **DONE (verified no-op)** — also byte-identical, 198 chars. **The goal did not flag this one** (F3). |
| `read_file` `write_file` `edit_file` `grep` `glob` | amplifier-module-tool-filesystem | **NOT-POSSIBLE IN THIS LANE** — patch shipped |
| `bash` | amplifier-module-tool-bash | **NOT-POSSIBLE IN THIS LANE** — patch shipped |
| `todo` | amplifier-module-tool-todo | **NOT-POSSIBLE IN THIS LANE** — patch shipped |
| `web_fetch` | amplifier-module-tool-web | **NOT-POSSIBLE IN THIS LANE** — patch shipped |
| `load_skill` | amplifier-bundle-skills | **NOT-POSSIBLE IN THIS LANE** — patch shipped |
| `recipes` | amplifier-bundle-recipes | **NOT-POSSIBLE IN THIS LANE** — patch shipped |

### HALF B — 10 context files

| # | File | Verdict |
|---|---|---|
| 9 | `bundles/anchors/context/system.md` + `bundles/anchors-amp-dev/context/amplifier-ecosystem.md` | **DONE** — 2,790 → 2,442 chars, shipped in PR #372 |
| 0–8 | gitea, dtu, amplifier-tester, modes, app-cli, skills, wayfinder ×2, routing-matrix | **NOT-POSSIBLE IN THIS LANE** — patches shipped (7,812 chars of saving, ready to apply) |

### Guardrail + census

| Item | Verdict |
|---|---|
| Byte-pinning test (cannot drift back) | **DONE** — `tests/test_lean_head_guardrail.py`, fixture vendored |
| Guardrail (a) head census | **DONE** — per-artifact char budgets; **shown RED then GREEN in CI** |
| Guardrail (b) one cold head write | **DONE for the precondition; the live-wire half is NOT asserted** — see §5 |
| Guardrail (c) non-inferiority | **DONE (cited, not measured)** — 5zp LB −7.15 pp vs frozen −10 pp |
| Real-session head census before/after | **DONE** — §6 |
| Full fidelity diff, all 23 targets | **DONE** — 4 flags, 3 false positives, 1 weakening (out-of-repo, advisory) |

**NOT-POSSIBLE reason, stated once and in full** (it is the same reason for all 21):
*21 of the 23 targets live in 13 repositories that are not in this lane. The lane
directory contains exactly one checkout, `amplifier-foundation`.* ***The binding
constraint is GOAL.md's prohibition, not availability**: 11 of the 13 are not
cloned on this host, but the other 2 (`amplifier-bundle-skills`,
`amplifier-app-cli`) ARE present here and were still not touched, because
Procedure 4 says "Never touch other repos" and those are other sessions' working
checkouts on a shared host.* *GOAL.md classifies this as a defect in the goal
rather than a task — so the work that could be done here was done (2 targets
shipped, all 23 diffed, all 19 changing out-of-repo targets patched), and the
defect is reported as F1.*

## 3. Spend

**$0.00 of the $15.00 authority. Nothing was purchased. No per-run price was
observed because no run was bought.**

The goal authorised `2 × ~$4.72 (g7h3 stock mean) = ~$9.44, slack to $15` for
"the GUARDRAIL RUNS ONLY". **That purchase turned out to be unnecessary, and
naming why is a finding, not a saving:**

* Guardrail **(a)** — the only one of the three that can go RED against the stock
  head — is a **deterministic character count**. It needs no model, no API key
  and no network. It went red and then green on GitHub Actions at $0.
* Guardrail **(c)** is **cited from 5zp**, which the goal itself states requires no
  new measurement.
* Guardrail **(b)**'s live half cannot be bought for $9.44 anyway (see §5) — two
  single-turn runs cannot demonstrate behaviour *at a compaction boundary*, which
  is what the assertion is about. Buying them would have produced a receipt, not
  evidence.
* The head census, which the goal placed under the same authority, was taken on
  the **fully-composed real `params`** by a hook that calls `os._exit()` before
  the HTTP call — a genuine wire head for $0.

On the goal's AUTHORING RULE (a cap must be stated as
`runs × arms × per-run estimate / validity rate = cap`): this goal's cap gives
`runs × per-run` but no validity divisor. For an S7-17 measurement that omission
would be the 1ru failure mode. **Here it is harmless** — a CI run has no validity
gate, so no divisor applies — but the arithmetic is only *safe by accident*, and
a future goal reusing this phrasing for a measurement lane would repeat 1ru.

**Residue:** $15.00 unspent. Smallest useful purchase it could not buy: nothing —
there is no remaining purchasable work in this lane's scope.

**No DTU or other infrastructure was created**, so nothing was registered in the
infra ledger and there is nothing to tear down.

## 4. The guardrail: RED before GREEN

Both runs are on PR #372, same workflow, same 6 legs (ubuntu + windows × Python
3.11/3.12/3.13).

**RED — guardrail present, stock head:**
`5833f84f843912f2e5bb696225a4d63fafc98256`
<https://github.com/microsoft/amplifier-foundation/actions/runs/34143330945>

```
4 failed, 1869 passed, 4 skipped
FAILED TestBytePinsAgainstTheV1Head::test_delegate_preamble_is_byte_identical_to_v1
       - 1190 chars vs the pinned 938
FAILED TestGuardrailAHeadCensus::test_context_file_within_budget[.../amplifier-ecosystem.md]
       - 1432 chars, over its pinned lean budget of 1300 (+132)
FAILED TestGuardrailAHeadCensus::test_context_file_within_budget[.../system.md]
       - 1358 chars, over its pinned lean budget of 1142 (+216)
FAILED TestGuardrailAHeadCensus::test_delegate_preamble_within_budget
       - 1190 chars, over its pinned lean budget of 938 (+252)
```

Exactly 4 failures, all of them the guardrail, on all 6 legs. Nothing else in the
suite moved.

**GREEN — guardrail present, lean head:**
`46fbd454c99e90662aee3447efff7d23115969c0`
<https://github.com/microsoft/amplifier-foundation/actions/runs/34143774767>

```
conclusion: success   (all 6 legs: ubuntu + windows x Python 3.11/3.12/3.13)
```

Same workflow, same legs, same guardrail — the only difference between the two
runs is the head text. **The guardrail failed before it was allowed to pass.**

## 4b. PRs

| PR | SHA | State |
|---|---|---|
| [#372](https://github.com/microsoft/amplifier-foundation/pull/372) | `46fbd454c99e90662aee3447efff7d23115969c0` (guardrail-green head commit) | draft → ready when its own CI is green. **Not merged — the manager merges.** |

Commits on the branch, in order:

| SHA | CI | What |
|---|---|---|
| `5833f84f843912f2e5bb696225a4d63fafc98256` | **RED (4 failed)** | guardrail only, stock head |
| `46fbd454c99e90662aee3447efff7d23115969c0` | **GREEN** | the lean head + artifacts |

## 5. What the guardrail does NOT prove — stated on purpose

Guardrail (b) is *"cache_read returns to exactly the head at every compaction
boundary"*. A static repo test cannot observe `cache_read` on a live Anthropic
response, and pretending otherwise is how a decorative check gets mistaken for a
measured one.

What is asserted here is the **repo-side precondition**: the owned head text is
deterministic — no timestamp, no absolute path, no sha, no session id, and it
composes identically twice. Those are the concrete things that make turn N's head
differ from turn N−1's and force a second cold write. The live-wire half remains
**measured, not asserted** — `probes/bji-lean-head/head_stability.py` and the 98
g7h3 runs.

Guardrail (c) is likewise a **citation pin**, not a measurement: it fails if the
frozen −10 pp margin is loosened or the cited −7.15 pp lower bound is edited to
one that no longer clears it.

## 6. Real-session check (from the composed wire head, $0)

```
bundle anchors-amp-dev · model claude-opus-5 · this host · 2026-09-07

              instr      tools       head    context_file blocks
BEFORE       99,557    220,853    320,410           24
AFTER        99,209    220,587    319,796           24
delta          -348       -266       -614
```

* `"You are Amplifier"` at char **248 → 247** — well inside the first ~1,500. ✔
* **24** `context_file` blocks before and after. GOAL.md expects 23; this host
  composes 24. **Reported as measured, not adjusted to the expectation.** The
  count is unchanged by this PR, which is the property that matters.
* `~/.amplifier/cache` **not edited**. `~/.amplifier/settings.yaml` **not left
  modified** — but see F6 for a near-miss worth reading.

**On the 48,249-char threshold (guardrail (a) as written).** This host's
`anchors-amp-dev` head measures **320,410 chars — 3.8× that number** — because it
composes 86 tools and 24 context files where the eval container composed 14 tools.
48,249 describes **the eval container's bundle composition**, not the product.
Per the goal's instruction to "adjust to the MEASURED composed head … and state
the measured number", guardrail (a) is implemented as **per-artifact char
budgets**, which are portable across bundle sets and still fail on exactly the
regression the absolute was meant to catch.

## 7. Corrections and deviations, each stated per the goal

1. **The item's spec path is stale.** Used
   `/home/bkrabach/dev/openai-evals-team-ci/.amplifier/evaluation/probes/bji-lean-head/`,
   **not** the `ai-notes/` path the item names. Confirmed all four files present
   at the byte sizes the goal quotes.
2. **Span 9's target no longer exists as one file.** ux32 split it; the
   compression is applied across **both** halves and stated in the PR body.
3. **Fidelity restoration costs +450 chars** against a v1-faithful rewrite: the
   `session-navigator`/`graph-analyst` pointers and the
   `scripts/amplifier-session.py` command with its "there is no
   `amplifier session repair` subcommand" correction. All three post-date v1.
   Fidelity beats compression; the cost is paid and pinned.
4. **Spans 10 and 11 are hook-generated and were NOT hand-edited.** Their lean
   blocks would be 861 + 5,350 = **6,211 chars** — that share of the shim's 48,249
   is unreachable without the renderer change, which has its own follow-up item.
5. **The `delegate` agent catalog was not touched** (appended at runtime). Only
   252 of `delegate`'s 9,559-char total v1 saving is in scope; the other 9,307 is
   catalog.
6. **One existing test was updated, not deleted.**
   `test_delegate_self_delegation_disabled.py`'s `FEATURE_LINE` literal changed
   because `agent="self"` moved into the `- agent:` bullet. The invariant is
   unchanged and a second, **literal-independent** test was added so a future
   rewording cannot re-open the gap.
7. **Default-mode byte identity does not hold, by design.** This change exists to
   move head bytes; claiming byte-identity would be false. What is verified is
   that nothing *else* moved: 1,872 passed locally and 1,869/1,864 on CI, with
   only the known-pre-existing `test_sources.py::test_resolve_existing_file`
   failing locally (confirmed failing on clean `HEAD` too; it passes on CI).
8. **First-objective rule.** The goal's objectives did not conflict, so no
   first-objective tie-break was needed.

## 8. Follow-ups this lane is handing over

1. **The 19 out-of-repo patches** in `patches/` — 10 tool descriptions, 9 context
   files, 11,738 chars of saving, drop-in. **Filed as `model_performance-hyid`**
   (follow-up-of this item), so they are queued work rather than orphaned
   artifacts. That item carries the per-repo char table, the two verified no-ops,
   the `edit_file` fidelity debt, and the requirement that each repo's guardrail
   be shown red before green.
2. **F6 — `amplifier source add` ignores `AMPLIFIER_HOME` and writes to the real
   `~/.amplifier/settings.yaml`.** A foot-gun on any shared or CI host.
3. **F4 — `edit_file`'s lean text weakens "NEVER write new files unless
   explicitly required"** to "Prefer editing existing files over creating new
   ones". ~35 chars to restore the qualifier, against a 458-char saving.
4. **F7 — the stock `delegate` preamble emitted the self-delegation line twice**
   (76 chars per request, every session, both wires). Fixed here; worth knowing it
   shipped.

## 9. The out-of-repo remainder is queued work, not an orphaned artifact

Filed after review, because *"needs a lane with those repos"* is a hope and a
queued item with acceptance criteria is work. `model_performance-hyid` is a
**blocked tracker** (do not claim); the 13 children below are independently
claimable, one per repo — the parent goal's own unit, *"One PR per repo"* — so
13 lanes can run in parallel instead of one agent serialising 13 PRs. These are
leaf text edits with no cross-repo code dependency, so nothing needs sequencing
between them.

| # | item | repo | targets | ch |
|---|---|---|---|---:|
| 1 | `model_performance-oth3` | `amplifier-module-tool-filesystem` | read_file, write_file, edit_file, grep, glob | 1,783 |
| 2 | `model_performance-i11t` | `amplifier-module-tool-bash` | bash | 679 |
| 3 | `model_performance-o53s` | `amplifier-module-tool-todo` | todo | 141 |
| 4 | `model_performance-4qg2` | `amplifier-module-tool-web` | web_fetch (+ web_search NO-OP, verify & state) | 83 |
| 5 | `model_performance-1q1f` | `amplifier-bundle-skills` | load_skill + skills-instructions.md | 3,164 |
| 6 | `model_performance-va53` | `amplifier-bundle-modes` | modes-instructions.md (+ mode NO-OP, verify & state) | 2,467 |
| 7 | `model_performance-d8s3` | `amplifier-bundle-recipes` | recipes | 561 |
| 8 | `model_performance-lkf7` | `amplifier-bundle-gitea` | gitea-awareness.md | 280 |
| 9 | `model_performance-aj2j` | `amplifier-bundle-digital-twin-universe` | dtu-awareness.md | 407 |
| 10 | `model_performance-mvc8` | `amplifier-bundle-amplifier-tester` | amplifier-tester-awareness.md | 826 |
| 11 | `model_performance-w2cj` | `amplifier-app-cli` | cli-awareness.md | 60 |
| 12 | `model_performance-y16x` | `amplifier-bundle-wayfinder` | wayfinder-voice.md + propose-and-ack.md | 874 |
| 13 | `model_performance-7kxh` | `amplifier-bundle-routing-matrix` | routing-instructions.md | 413 |
| | | | **TOTAL** | **11,738** |

Each child repeats the rules rather than referencing the tracker, so no child
depends on reading another item: edit the real source (a tool module's OWN
description string, never a shim and never the call site; a bundle's own context
file) · add a guardrail and **show it RED before GREEN**, quoting both run URLs ·
pin **per artifact**, never to a whole-head absolute · draft PR, ready on green,
**do not merge** · $0 spend, ships on g7h3 and 5zp, re-buy neither.

Two children (`4qg2`, `va53`) additionally require their **no-op** target
(`web_search`, `mode`) to be verified and **stated** — an unchanged file in a PR
is noise, but a silently skipped one is a gap.

## Addendum — 2026-09-08

The current static census for `bundles/anchors/context/system.md` is 1,154
chars, +12 over its recorded 1,142-char lean budget. The required `@AGENTS.md`
rule auto-loads session-CWD `AGENTS.md`, so the budget is pinned at 1,154 rather
than compressing unrelated prompt text. The owned-head total is now 3,392 chars
against the unchanged 3,980-char stock: a current saving of 588 chars versus the
original 600. Historical experimental results above are unchanged; no vendor
cache metrics were rerun.
