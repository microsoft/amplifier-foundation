# (B) Measurements — what the split actually did, and what remains unmeasured

Companion to `B-eval-preregistration.md` (committed first, before any of this existed).

**Status: the code half is DONE and measured. The EVAL half is NOT-POSSIBLE at the $10
authority, priced before spending. PR 2 stays a DRAFT and is not marked ready.**

---

## 1. What was executed

- The split: `delegation-core.md` (new) + `delegation-depth.md` (renamed from
  `delegation-instructions.md`, awareness removed) + `multi-agent-patterns.md` (unchanged,
  now agent-body-loaded).
- `behaviors/agents.yaml` and `behaviors/tasks.yaml` rewired to the core alone.
- Depth @-mentioned from `agents/foundation-expert.md` and `agents/session-analyst.md`.
- The validator's context-token estimator repaired (`validate-bundle-repo` v3.14.0).
- 33 new tests in `tests/test_behavior_context_budget.py` (12 of them fail on `main`).
- 3 real foundation-root sessions ($0.20 total, `haiku`) measuring the root prompt
  before/after.
- Full suite: **2,064 passed**, 2 known pre-existing failures.

## 2. On-disk token measurement

The validator's own unit is `len(content) // 4`; `o200k_base` is a real tokenizer, reported
alongside it so the estimator's bias is visible rather than assumed.

| file | chars | `len//4` | o200k_base |
|---|---:|---:|---:|
| **before** `delegation-instructions.md` | 16,897 | 4,224 | 3,628 |
| **before** `multi-agent-patterns.md` | 8,210 | 2,052 | 1,774 |
| **before — loaded by the behavior** | 25,107 | **6,276** | **5,402** |
| **after** `delegation-core.md` (loaded by the behavior) | 1,951 | **487** | **464** |
| **after** `delegation-depth.md` (agent bodies only) | 12,786 | 3,196 | 2,722 |
| **after** `multi-agent-patterns.md` (agent bodies only) | 8,210 | 2,052 | 1,774 |

The core is **under 500 tokens by both measures**, which is the goal's stated target.

## 3. The estimator was wrong by 6.28×, and the fix is what makes the fail-before honest

`recipes/validate-bundle-repo.yaml` behavior-hygiene Rule 4 resolved a `context.include`
two ways only: a leading `@` was charged a flat 500 tokens; anything else was tried as a
relative path. A `<namespace>:<path>` reference matches **neither** — it does not start with
`@`, and neither `behaviors/foundation:context/...` nor `<repo>/foundation:context/...`
exists — so it fell through to the 500-token default.

Two includes × 500 = **exactly 1000**, against a `> 1000` ERROR gate. A behavior carrying
6,276 real tokens was reported as 1000 and graded **WARNING — one token below the ERROR that
was true.**

```
### MAIN a0decc6 (fail-before)
  behaviors/agents.yaml: includes=2
      OLD estimator:  1000 tokens -> WARNING
      NEW estimator:  6276 tokens -> ERROR
  behaviors/tasks.yaml: includes=2
      OLD estimator:  1000 tokens -> WARNING
      NEW estimator:  6276 tokens -> ERROR

### BRANCH (pass-after)
  behaviors/agents.yaml: includes=1
      OLD estimator:   500 tokens -> ok
      NEW estimator:   487 tokens -> ok
  behaviors/tasks.yaml: includes=1
      OLD estimator:   500 tokens -> ok
      NEW estimator:   487 tokens -> ok
```

The fix also records any include it still cannot resolve, in
`context_unresolved_includes` + `context_tokens_is_estimate`. A flat guess folded silently
into a number that reads as measured is the defect; the fallback itself is fine as long as
it is visible.

## 4. Real-session effect — foundation as ROOT

Project-scope source override; `~/.amplifier/cache` untouched.

```
### BEFORE (main a0decc6)  session 5dfe9c07-d95d-41a8-815b-962c0ce5a0dd
    raw.system[0].text chars = 130,055
      PRESENT  core: 'You are an ORCHESTRATOR, not a worker.'
      PRESENT  depth: '## Wave Discipline'
      PRESENT  depth: '## The Context Sink Pattern'
      PRESENT  depth: 'Reading a Structured Agent Return'
      PRESENT  depth: 'Multi-Agent Patterns'
### AFTER  (branch B, split)  session eb36571b-b9c4-4667-a833-6990fb349e51
    raw.system[0].text chars = 106,655
      PRESENT  core: 'You are an ORCHESTRATOR, not a worker.'
      ABSENT   depth: '## Wave Discipline'
      ABSENT   depth: '## The Context Sink Pattern'
      ABSENT   depth: 'Reading a Structured Agent Return'
      ABSENT   depth: 'Multi-Agent Patterns'

Delta: -23,400 chars in the root system prompt
Provider-reported input tokens: 52,741 -> 47,014  (-5,727)
```

Depth stays reachable from the agents it was routed to:

```
### agents/foundation-expert.md
    RESOLVES  @foundation:context/agents/delegation-depth.md  (13,378 bytes)
    RESOLVES  @foundation:context/agents/multi-agent-patterns.md  (8,272 bytes)
### agents/session-analyst.md
    RESOLVES  @foundation:context/agents/delegation-depth.md  (13,378 bytes)
```

## 5. What is NOT measured — and why nothing was bought

**−5,727 root-prompt tokens is a SECONDARY metric.** The pre-registered PRIMARY metric is
task success + delegation-count sanity, and it is **not measured**. Moving wave discipline
and the context-sink pattern out of the root prompt could plausibly suppress legitimate
delegation or cause over-delegation, and nothing here tests either.

The design that would test it — n≥3 valid runs per arm per provider, 2 arms, 2 providers,
12 valid runs — prices at **$73.44** at the observed 67 % validity rate (18 launches:
9 × $3.53 opus-5 + 9 × $4.63 terra), **$48.96** even at 100 % validity. The authority is
**$10**. What $10 buys — ~2 valid runs, n=1 per arm on one provider — cannot produce a
success lower bound, so it cannot evaluate the frozen rule at all: a **0 % chance** of
landing the deliverable. Priced on first read of the goal; **$0 spent on the eval**.

**Therefore B is not shippable on this branch's evidence.** PR 2 is a draft and is not
marked ready. The branch and the finding stand; PR 1 (A + C) is unaffected and independent.

## 6. Note for whoever funds the eval

Recorded before results exist, so it cannot be read as an excuse afterwards: `otr`'s
pre-registered gate came back **UNEVALUABLE** because delegation counts at its cell were
`[0,0,1,0,0]`. If the arm-A S3 runs here produce a median delegation count of 0, the ±30 %
condition is likewise unevaluable and the honest report is "unevaluable" — not a substitute
gate invented after the fact.

## 7. One compatibility note

`context/agents/delegation-instructions.md` **no longer exists** — it is
`delegation-depth.md`. Any bundle outside this repo that `@`-mentions the old path will stop
resolving. In-repo references were all updated. This is a real consequence of the treatment
and is called out here rather than discovered downstream.
