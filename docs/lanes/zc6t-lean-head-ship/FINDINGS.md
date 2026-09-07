# zc6t — findings: shipping the lean head in-product

Lane `zc6t-lean-head-ship` · item `model_performance-zc6t` · 2026-09-07
Repo: `microsoft/amplifier-foundation`, branch `lane/zc6t-lean-head-ship`, PR #372

---

## F1 — GOAL DEFECT: 21 of the 23 deliverables have no target inside the paths this lane owns

**This is reported against the goal, not absorbed.** GOAL.md's own clause:

> every option this goal offers you must have at least one target inside the paths
> it says you own. If the only way to satisfy a deliverable is to write a file
> outside your worktree (another repo …), that is a DEFECT IN THIS GOAL, not a
> task. Report it against the goal, ship the patch as an artifact under your
> ARTIFACT ROOT, and resolve — do not edit another repo.

The lane directory contains exactly one checkout:

```
/home/bkrabach/dev/hw-model-performance/lanes/zc6t-lean-head-ship/
├── amplifier-foundation/     <- the only repo
└── lane.log
```

The 23 targets live in **13 different repositories**. Two are in this one:

| Half | Target | Repo | This lane |
|---|---|---|---|
| A | `delegate` (preamble) | amplifier-foundation | **SHIPPED in PR #372** |
| B | span 9 → `anchors/context/system.md` + `anchors-amp-dev/context/amplifier-ecosystem.md` | amplifier-foundation | **SHIPPED in PR #372** |
| A | `read_file` `write_file` `edit_file` `grep` `glob` | amplifier-module-tool-filesystem | patch artifact |
| A | `bash` | amplifier-module-tool-bash | patch artifact |
| A | `todo` | amplifier-module-tool-todo | patch artifact |
| A | `web_fetch` | amplifier-module-tool-web | patch artifact |
| A | `load_skill` | amplifier-bundle-skills | patch artifact |
| A | `recipes` | amplifier-bundle-recipes | patch artifact |
| A | `web_search` | amplifier-module-tool-web | **SKIP — byte-identical** (F2) |
| A | `mode` | amplifier-bundle-modes | **SKIP — byte-identical** (F3) |
| B | spans 0–8 (9 files) | 8 further repos | patch artifacts |

Nine of those twelve out-of-repo repos are **not even cloned on this host**
(`amplifier-module-tool-filesystem`, `-bash`, `-todo`, `-web`,
`amplifier-bundle-modes`, `-recipes`, `-gitea`, `-digital-twin-universe`,
`-amplifier-tester`, `-wayfinder`). Their stock text was read from
`~/.amplifier/cache/` (read-only) to generate the patches.

**Consequence for the census.** The saving this lane can realise on its own is
**614 chars of a measured 320,410-char head** (F5) — 1.7 % of the 36,070-char cut
the item specifies. The other 98 % is unreachable from here by construction, not
by underperformance.

**What would close it:** a multi-repo lane (13 checkouts) or 12 sibling lanes,
each with the corresponding repo. The patches in
`patches/` are drop-in for whoever gets them.

---

## F2 — `web_search` is already byte-identical to the v1 text (verified, skipped)

The item lists 13 tool descriptions. `web_search` needs **no change**:

```
stock:  'Search the web for information'   (30 chars)
v1:     'Search the web for information'   (30 chars)
identical: True
```

Verified by direct comparison of `probes/bji-lean-head/head/tools_stock.json`
against `v1_tools.json`. An unchanged file in a PR is noise; a silently skipped
one is a gap — so it is named here and in the PR body.

---

## F3 — `mode` is ALSO byte-identical, and the goal did not say so

The goal explicitly flagged `web_search` and asked for it to be verified and
skipped. It did **not** flag `mode`, but `mode` is in exactly the same state:

```
stock:  "Manage runtime modes. Operations: 'set' (activate a mode), 'clear'
         (deactivate), 'list' (show available), 'current' (show active). Mode
         transitions may require confirmation depending on gate policy."
                                                                (198 chars)
v1:     identical                                               (198 chars)
```

So **11 tool descriptions actually change**, not 13. Anyone reconciling the
census against "13 tool-description replacements" will otherwise look for two
diffs that do not exist.

---

## F4 — fidelity: nothing was traded away for bytes (4 flags reviewed, 3 false, 1 weakening)

`tools/fidelity_diff.py` extracts every rule-shaped token from the stock text of
all 23 targets — backticked code spans, command-shaped lines, `@mentions` and
`namespace:refs`, URLs, and ALL-CAPS imperatives — and asserts each still
appears in the lean text. Full table in the script's output; raw data in
`patches/fidelity-report.json`.

**Expected: none. Found: 4 flags, all reviewed by hand.**

| Target | Flagged token | Verdict |
|---|---|---|
| `modes-instructions.md` | `mode(operation="list")`, `("current")`, `("clear")` | **FALSE POSITIVE** — lean collapses them to `mode(operation="set"\|"clear"\|"list"\|"current")`. All four operations stated. |
| `skills-instructions.md` | `load_skill(skill_name="...")` | **FALSE POSITIVE** — lean writes `load_skill(skill_name="…")` with U+2026. Same rule. |
| `recipes` | `@recipes:examples/my-recipe.yaml` | **FALSE POSITIVE** — an invented filename in a second usage example. The real pointer, `@recipes:examples/code-review.yaml`, is preserved. |
| `edit_file` | `ALWAYS` | **REAL WEAKENING, not a loss.** Stock: *"ALWAYS prefer editing existing files in the codebase. NEVER write new files unless explicitly required."* Lean: *"Prefer editing existing files over creating new ones."* The rule is still stated; its imperative force and the `unless explicitly required` qualifier are gone. Recommendation for the `tool-filesystem` lane: restore the qualifier at a cost of ~35 chars against that description's 458-char saving. **Out of this repo — advisory only.** |

**In-repo restoration actually performed (span 9).** The v1 span was authored
against an older, smaller stock text. Today's
`anchors-amp-dev/context/amplifier-ecosystem.md` carries three things v1 never
saw, all of which are rules/commands/pointers and all of which are **restored**
in the shipped text:

1. the pointer to `context-intelligence:session-navigator`
2. the fallback pointer to `context-intelligence:graph-analyst`
3. the command `python scripts/amplifier-session.py {diagnose,repair,rewind,info,find} <session-dir>`
   together with the correction *"there is no `amplifier session repair` subcommand"*

**Byte delta of the restoration: +450 chars** (169 + 281) against what a v1-faithful rewrite
would have produced. Fidelity beats compression at every point of conflict, so
that cost is paid and stated rather than hidden. It is pinned by
`TestFidelityBeatsCompression` so a later compression pass cannot quietly drop it.

---

## F5 — the item's 48,249-char threshold is composition-specific and does NOT transfer to this host

Measured on the wire, `$0` (the census hook dumps the fully-composed Anthropic
`params` and calls `os._exit()` **before** the HTTP call — no tokens purchased):

```
bundle: anchors-amp-dev        model: claude-opus-5        (this host, 2026-09-07)

              instr      tools       head    context_file blocks
BEFORE       99,557    220,853    320,410           24
AFTER        99,209    220,587    319,796           24
delta          -348       -266       -614
```

The item's guardrail (a) says *"wire head <= 48,249 chars"*. **This host's
`anchors-amp-dev` head is 320,410 chars — 3.8x that number** — because it
composes 86 tools and 24 context files where the eval container composed 14
tools and ~11. The 48,249 figure describes the **eval container's specific
bundle composition**, not the product.

**This is why guardrail (a) is implemented as per-artifact char budgets rather
than one whole-head absolute.** A whole-head threshold is not portable across
bundle sets; a per-artifact budget is, and it fails on exactly the regression the
absolute was meant to catch. The measured number is stated per the item's
instruction to "adjust to the MEASURED composed head … and state the measured
number."

Real-session sanity checks, both required by GOAL.md:

* **`"You are Amplifier"` at char 248 → 247** — well within the first ~1,500.
* **24 `context_file` blocks before and after.** GOAL.md says 23; this host
  composes 24. Reported as measured, not adjusted to match the expectation. The
  count is unchanged by this PR, which is the property that matters.

Cache was **not** edited. `~/.amplifier/settings.yaml` was **not** left modified —
see F6.

---

## F6 — near-miss on a shared host: `amplifier source add` ignores `AMPLIFIER_HOME`

While setting up the census, `AMPLIFIER_HOME=/tmp/... amplifier source add
foundation <worktree>` wrote the override to the **real**
`~/.amplifier/settings.yaml`, reporting `Scope: global (~/.amplifier/settings.yaml)`
— on a host GOAL.md states the manager and other lanes share.

Detected immediately from the command's own output and reverted with
`amplifier source remove foundation`; `amplifier source list` then reported
`No source overrides configured`, and a grep of the real settings file for the
worktree path returns nothing. No other lane observed it (elapsed < 60 s), and
the census was redone with a `PYTHONPATH`-only hook that writes nothing.

Worth filing separately against the CLI: an `AMPLIFIER_HOME` that redirects
reads but not writes is a foot-gun on any shared or CI host.

---

## F7 — the stock `delegate` preamble shipped the self-delegation line twice

Not part of the brief; found while reproducing the preamble from source. The
stock text emitted `- agent="self": Spawn yourself as a sub-agent (maximum token
conservation)` **twice** — once hardcoded in `base_description` and again from
the `self_delegation` row of `_build_feature_registry()`. Visible in the captured
stock head (`head/tools_stock.json`) and in any live session.

Cost: 76 chars on every request of every session, on both wires, for as long as
it has been there. Removed by this PR as a side effect of folding `"self"` into
the single `- agent:` bullet.

---

## Scope boundaries observed

* **Spans 10 and 11 are hook-generated** (`<system-reminder source="routing-matrix">`,
  `<system-reminder source="hooks-skills-visibility">`). No file produces them; a
  renderer emits them per-turn. Not edited. Their lean blocks would be 861 and
  5,350 chars — that 6,211-char share of the shim's saving is unreachable without
  the renderer change, which has its own follow-up item.
* **The `delegate` agent catalog is appended at runtime** and was not touched.
  Stock catalog 12,904 chars, v1 catalog 3,597 — a 9,307-char difference that is
  **not** available from this repo's source and is excluded from every number in
  this lane's PR.
* **No `$/task` re-measurement.** Decided by `g7h3`: −13.57 %, CI [−22.27 %, −4.86 %].

## Reproducing

```bash
python docs/lanes/zc6t-lean-head-ship/tools/fidelity_diff.py \
       --write-patches docs/lanes/zc6t-lean-head-ship/patches
uv run pytest tests/test_lean_head_guardrail.py -q
```
