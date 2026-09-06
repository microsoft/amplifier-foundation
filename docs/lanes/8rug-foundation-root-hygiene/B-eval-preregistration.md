# (B) Pre-registration — delegation context split, before/after eval

**Written 2026-09-06, BEFORE any file in the split was edited.** Committed on its own, ahead of
the implementation commit, so the decision rule provably predates the numbers. Nothing in this
document may be edited after results exist (goal SCOPE-OUT: "Do NOT edit the decision rule after
seeing results").

Work item: `model_performance-8rug` part (B). Branch: `lane/8rug-foundation-root-hygiene-b`.

---

## 1. The treatment

`behaviors/agents.yaml` and `behaviors/tasks.yaml` each `context.include`
`delegation-instructions.md` + `multi-agent-patterns.md` — **6,276 tokens (len//4) / 5,402 tokens
(o200k_base)** measured on disk at `a0decc6`, loaded into the root system prompt of every
foundation-root session.

The split (owner-approved shape, from the work item):

- `context/agents/delegation-core.md` — **awareness only**, target **<500 tokens on disk**:
  the delegation imperative, immediate triggers, basic `delegate` usage, the two context
  parameters. **The root session IS the delegator and needs these.** Stays in
  `behaviors/agents.yaml`.
- `context/agents/delegation-depth.md` — **depth**: session-resumption mechanics, wave discipline,
  reading a structured agent return, large session-file handling, scrutinising an agent's "N/A",
  the context-sink pattern itself. Loaded from the **bodies of the agents that need it**, not from
  the behavior.
- `context/agents/multi-agent-patterns.md` — same triage: its awareness line ("batch independent
  work into one turn") folds into the core; the rest stays a depth file loaded from agent bodies.

Depth is deferred to exactly the agents whose work requires it:

| Agent | Why it needs depth |
|---|---|
| `foundation-expert` | The **only** foundation agent that declares `tool-delegate` (the behavior sets `exclude_tools: [tool-delegate]`, so no other spawned agent can delegate at all). It is the only sub-agent that ever fans out, so wave discipline and the two context parameters are live for it. |
| `session-analyst` | The **only** agent that resumes sessions by `session_id` and reads large session files — the two depth sections written for exactly that. |

No other agent in `agents/` can delegate or resume, so loading depth into them would be paying for
instruction they cannot act on.

`behaviors/tasks.yaml` (legacy `tool-task` compatibility) references **the same core file**, not
the full list. Justification: `tool-task` *is* a delegation-shaped tool — it spawns sub-agents — so
the imperative and the triggers apply to it; and pointing both behaviors at one file makes drift
between them impossible. It does not get depth, because `tool-task` exposes neither
`context_depth`/`context_scope` nor session resume.

---

## 2. Instrument repair (part of the treatment, per the work item)

The validator's behavior-hygiene Rule 4 (`recipes/validate-bundle-repo.yaml:1960-2000`) reports
**~1,000 tokens** for these two includes. The true on-disk figure is **6,276**. The understatement
is **6.28×**, and its mechanism is now identified exactly:

```python
if include_ref.startswith("@"):
    total_context_tokens += 500          # "Estimate ~500 tokens per awareness file"
else:
    include_path = behavior_path.parent / include_ref
    if not include_path.exists():
        include_path = path / include_ref
    ...
    else:
        total_context_tokens += 500      # Default estimate
```

`foundation:context/agents/delegation-instructions.md` is a **`<namespace>:<path>` reference**. It
does not start with `@`, so it takes the relative-path branch; neither
`behaviors/foundation:context/...` nor `<repo>/foundation:context/...` exists, so it falls through
to the flat **500-token default**. Two includes × 500 = **exactly 1,000** — and the ERROR gate is
`> 1000`, so the behavior lands precisely **on** the boundary and is reported as a WARNING rather
than the ERROR it actually is.

Reproduced verbatim against this repo at `a0decc6`:

```
behaviors/agents.yaml: includes=2 context_total_tokens=1000 -> WARNING
behaviors/tasks.yaml:  includes=2 context_total_tokens=1000 -> WARNING
```

**The fix is part of (B)**: resolve `<namespace>:<path>` references against the repo root when the
namespace is the repo's own bundle, so the estimator reads the real file. Without it the
measurement that justifies the split is taken with a ruler already known to be wrong by 6×.

Measured token figures used throughout, all from the same script, both methods reported because
`len//4` is the validator's own estimator and `o200k_base` is a real tokenizer:

| file | chars | `len//4` | o200k_base | cl100k_base |
|---|---|---|---|---|
| `delegation-instructions.md` | 16,897 | 4,224 | 3,628 | 3,664 |
| `multi-agent-patterns.md` | 8,210 | 2,052 | 1,774 | 1,802 |
| **total** | 25,107 | **6,276** | **5,402** | 5,466 |

---

## 3. The eval — design, fixed before any result exists

| | |
|---|---|
| **Task set** | S3-class multi-step scenarios, reusing the program's existing S3 scenarios in `amplifier-bundle-evaluation`. No new scenarios. |
| **Bundle** | foundation as ROOT, pinned per arm by source override, run in DTUs. |
| **Arms** | **A = `main`** (`a0decc6`) · **B = `lane/8rug-foundation-root-hygiene-b`** |
| **Providers** | both — anthropic **opus-5** root, openai **gpt-5.6-terra** root |
| **n** | **≥3 valid runs per arm per provider** → **12 valid runs minimum** |
| **Primary metric** | task success **and** delegation-count sanity |
| **Secondary** | root-prompt tokens, $/task |
| **Arm-purity rule** | a mixed arm-pair is VOID, no rescue argument (program convention, §(n) of `00-what-we-know.md`) |

### Decision rule (frozen)

> **SHIP if — and only if — success LB ≥ −5 pp AND delegation count is within ±30 % of main.**

Both conditions must hold. Either one failing means **B does not merge**: the branch stays, the
finding is written, and A and C are unaffected.

Delegation count is two-sided **on purpose**. Moving depth out of the root prompt could plausibly
fail in either direction — suppressing legitimate delegation (the root forgets to fan out) *or*
causing over-delegation (the root delegates without the wave discipline that told it to batch).
A one-sided gate would score one of those failures as a pass.

### Known evaluability risk, recorded before spending

`otr` (§(m), `00-what-we-know.md`) had a pre-registered gate come back **UNEVALUABLE** because
delegation counts at its cell were `[0,0,1,0,0]` — an ~80 % prior chance of being unevaluable,
readable for $0 beforehand. The same risk applies here: **if the S3 arm-A runs produce a median
delegation count of 0, the ±30 % condition is unevaluable**, and the honest report is
"unevaluable", not a retrofitted substitute gate. This is stated now, before any run.

---

## 4. Price the deliverable BEFORE spending

Goal authority for (B): **$10, eval only.**

Per-run prices, from this program's own observations (`ai-notes/00-what-we-know.md`, and the
goal's own arithmetic block):

| source | figure |
|---|---|
| goal text, program-observed | sonnet **~$2.29/run**, terra **~$4.63/run** |
| §S3 Pareto | opus-5 S3 **$3.53/run**; sonnet5-medium $2.20; terra-medium $1.55 |
| §(m) `otr` | one arm-B run **$2.62** |
| §(l) `5zp` | 2 fresh valid runs for **$10.99** ⇒ **~$5.50 per VALID run** |
| observed validity | **67 %** (`5zp`'s correction of `h6v`: 2 of 3 launches valid) |

Applying the goal's own authoring rule — `runs × arms × per-run estimate / validity rate = cap`:

```
Pre-registered design = 3 runs × 2 arms × 2 providers = 12 VALID runs

launches needed          = 12 / 0.67                       = 18 launches (9 anthropic, 9 terra)
anthropic @ opus-5 $3.53 = 9 × $3.53                       = $31.77
openai    @ terra  $4.63 = 9 × $4.63                       = $41.67
                                                     TOTAL = $73.44   vs $10 authority

Even at 100% validity (12 launches, no failures at all):
   6 × $3.53 + 6 × $4.63 = $21.18 + $27.78                 = $48.96   vs $10 authority
Even at the goal's cheaper sonnet figure, 100% validity:
   6 × $2.29 + 6 × $4.63 = $13.74 + $27.78                 = $41.52   vs $10 authority
```

**The arithmetic does not close, by 4×–7×.** The goal states this itself ("a full 12-run design
would be ~$41 and does NOT fit").

**What $10 could actually buy, and why it is not worth buying.** At the cheapest observed rate
($2.62/run) $10 buys **3 launches ⇒ ~2 valid runs at 67 % validity** — i.e. **n=1 per arm on ONE
provider**. n=1 per arm cannot produce a lower bound on success at all, so it cannot evaluate the
frozen rule's first condition; and a single delegation count per arm cannot support a ±30 %
comparison. Spending it would produce a number with a **0 % chance of satisfying the
pre-registered rule** — the exact failure the goal names against lane `1ru`.

**Therefore: the eval is recorded NOT-POSSIBLE at this authority, stated before spending, and $0
is spent on it.** The authority that WOULD close it is **$73.44** at the pre-registered n≥3 /
2 arms / 2 providers with the observed 67 % validity rate (**$48.96** if every launch is valid;
**~$41.52** if the anthropic arm is run on sonnet rather than the specified opus-5).

Shrinking the design and reporting it as the pre-registered one is the failure mode this
pre-registration exists to prevent, so it is not done.

### What IS delivered at $0

The code half of (B) — the split, the depth routing, the `tasks.yaml` decision, and the estimator
repair — is code and on-disk measurement, costs nothing, and is delivered. **PR 2 ships as a DRAFT
and is NOT marked ready**: this pre-registration's rule has not been satisfied, so B does not
merge. A and C (PR 1) are unaffected.
