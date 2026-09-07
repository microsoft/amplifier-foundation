# Description Authoring Principles

> **Canonical source.** This is the ONE place that states how to write a
> *description* field — agent `meta.description`, skill frontmatter
> `description`, mode `description`, and tool description strings. Every
> other doc that touches this topic (AGENT_AUTHORING.md, BUNDLE_GUIDE.md,
> DOMAIN_VALIDATOR_GUIDE.md, skill authoring guides, validator recipes)
> **points here** instead of restating it. If you find yourself copying a
> paragraph from this file into another doc, stop — link instead (see V1/V2
> below, and don't make this file the exception to its own rule).

> **Scope: EVERY bundle, not just this repository.** These rules apply to any
> Amplifier bundle in any repository — foundation, the first-party bundles,
> and third-party ones. They are enforced by `recipes/validate-agents.yaml`
> and `recipes/validate-bundle-repo.yaml`, which take a `repo_path` and run
> against any bundle repo, not only this one.
>
> **The one-line WHY.** A description is not documentation you pay for when
> the capability is used. Agent `meta.description` is concatenated into the
> `delegate` tool's own description, and skill `description` is concatenated
> into the `hooks-skills-visibility` block; both render into the **always-on
> head** and are therefore **paid on every request of every session** whether
> or not the agent is ever delegated to or the skill ever loaded.
>
> The bill is measured, not asserted. In the composition
> `model_performance-zc6t` measured on the wire (bundle `anchors-amp-dev`,
> claude-opus-5), the runtime-appended **agent catalog alone was 12,904 chars
> before that lane's work and 3,597 after** — carried on every request. That
> lane's head reduction (**84,319 → 48,249 chars**) is a *bundle-composition*
> figure and not a universal one: the same commit measured 320,410 chars of
> head on a host composing 86 tools where the eval container composed 14. Cite
> it as a composition, never as "the head is 48k".
>
> **This scope statement exists because the previous one was implicit and
> quietly stopped being true.** PR #341 set the no-`<example>` policy below in
> 2026-08; it was applied inside `amplifier-foundation` and **nowhere else**.
> A batch a year later found **29 files across 6 repos** still shipping
> example blocks in descriptions and swept them by hand (android-tester 8/8 →
> 0, browser-tester 6 → 0, reality-check 10/10 → 0, dot-graph 28/28 → 0
> — its agent descriptions **14,615 → 6,484 chars** — context-intelligence
> 3 → 0, plus two forks). A rule that lives only as a convention is a rule
> that quietly stops being true; that is why the caps in V5 are now
> **validator-enforced** rather than advisory.

Evidence base: a measured eval campaign (A/B testing across paired runs)
found the patterns this file discourages measurably harm compliant modern
models. Citations are inline per principle. The campaign is ongoing — see
"Provisional" markers for findings still being calibrated.

---

## V1 — State each rule once

A mandate stated 4 ways in one system prompt (2 of the 4 contradictory)
measurably destabilized a compliant model's behavior; removing the
duplication was validated in a 12-run A/B (same task, same seed set,
duplication present vs. removed). **One canonical statement, cross-referenced
everywhere else.** A rule repeated "for emphasis" is not redundant safety
margin — it is a second copy that can drift from the first and force the
model to arbitrate between two versions of your own instructions.

## V2 — Delete stale and fabricated content

The "1e2 deletion pass" removed stale/fabricated system-prompt content:
−11.3% system-prompt bytes, quality unchanged across 12/12 paired runs.
Deletion, not archival-in-place — see `CONTEXT_POISONING.md` §"Aggressive
Deletion" (this file does not restate that guidance; go read it there).
Applied to descriptions specifically: an example that no longer matches the
shipped agent, a trigger condition for a capability that was removed, a
"WHY" paragraph justifying a design that changed — all of these are stale
content masquerading as documentation, and cost tokens on every load.

## V3 — Agent-catalog examples are expensive and always visible

Every agent's `meta.description` is concatenated into the delegate tool's
own description, which loads into context on **every turn**, not just when
that agent is used. Policy: **no `<example>` blocks at all**, in any
description surface — agent `meta.description`, skill frontmatter
`description`, mode `description`, or tool description strings. (Superseded:
an earlier version of this policy allowed "at most 2 examples, no
`<commentary>` tags" — the eval evidence below shows the cap was
unnecessarily generous.)

The P2 delegation-wave test (see "Settled" section below) stripped ALL
`<example>` blocks and all `<commentary>` tags at assembly time — delegate
description dropped from 14,562 → 8,060 chars — with **no reduction in
delegation quality** across both providers tested. Full removal costs
nothing measurable in quality and saves real per-turn tokens on every
session that has the agent in its catalog, whether or not it's ever
delegated to. If a trigger condition needs to reach the model, state it as a
decision rule in the WHEN clause (V6) — a worked dialogue example is not
required to communicate it, and a stale one is V2's problem wearing a
WHEN-to-delegate costume.

## V4 — Delegation-pushing scaffolding hurts modern models

Scaffolding designed to push the orchestrator toward delegating (verbose
"why you should use this agent" framing, redundant WHY/WHEN/WHAT/HOW
templates repeated per-agent) measured **−27% tokens and −27% delegate
calls** on deletion, with quality unchanged across 5/5 paired runs. Modern
models do not need to be sold on delegating — they need a clear, minimal
statement of what the agent does and when it applies. Persuasive framing is
overhead, not signal.

## V5 — Description budgets are real and must be enforced

One real tool description was measured at **15,271 characters — 56% of the
entire tool-description budget** for that session, and drove 7-8x
over-delegation on gpt-5.6-sol: 60% of that model's spawns quoted the
tool's own imperative language verbatim back at it. A description that large
is not thorough — it is a second system prompt smuggled into a metadata
field, read on every turn whether or not the tool is ever called.

**Budgets. The enforced unit is CHARACTERS.** Chars are what every head
measurement in this program was taken in, they need no tokenizer, and they
are what a validator can count identically on every platform. Where a token
figure is quoted below it is the same chars/4 estimator the recipes use
(calibrated in `tests/test_context_include_estimator.py`: median 1.128 vs
o200k_base, range 0.940–1.312, i.e. it runs HIGH on prose so a budget gate
fires early, never late).

| Surface | WARN | ERROR | Status |
|---|---|---|---|
| Agent `meta.description` | > 600 chars | > 1,200 chars (2x) | **Enforced** — validate-agents.yaml, validate-bundle-repo.yaml Phase 2.8 |
| Skill frontmatter `description` | > 400 chars | > 800 chars (2x) | **Enforced** — validate-bundle-repo.yaml Phase 2.82 |
| Mode `description` | > 500 tokens | > 800 tokens | Established (validate-bundle-repo.yaml Phase 2.7) |
| Tool description | no fixed ceiling yet | no fixed ceiling yet | Flag any single tool >10% of a typical tool-description budget as a design smell |

**Where 600 / 400 come from — measured, not chosen.** Description lengths
across 9 bundle repositories on one host, 2026-09-07 (`n` = descriptions
carrying a non-empty string):

| Corpus | n | mean | median | p90 | max | over cap | over 2x |
|---|---|---|---|---|---|---|---|
| foundation agents (aligned by #341) | 24 | 410 | 427 | 721 | 761 | 6 | **0** |
| all agents, 8 repos incl. unswept | 54 | 1,084 | 739 | 2,396 | 3,235 | 36 | **17** |
| foundation skills | 3 | 420 | 384 | 566 | 566 | 1 | **0** |
| skills-bundle skills | 31 | 413 | 312 | 814 | 928 | 13 | **4** |

The cap is the value that separates *already-aligned* from *never-aligned*:
the one corpus that had the policy applied (foundation, #341) has **zero**
descriptions over 2x, while unswept repos put 17 of 54 there — dot-graph's
local checkout averages 2,302 chars/agent and puts **12 of 12** over the
ERROR line. WARN at the cap is deliberately noisy on aligned repos (6 of 24
foundation agents warn today); the ERROR line is what has to be a real
defect, and on the aligned corpus it is empty.

Agent tiers are lower than mode tiers because agent descriptions are paid
**by every session that has the agent in its catalog**, regardless of
whether it's ever delegated to — mode descriptions are paid only by sessions
that load that mode. The *token*-tier numbers (WARN 300 / ERROR 600 tokens ≈
1,200 / 2,400 chars) that this table replaces for agents remain provisional
and are superseded as a gate: the 2026-08-30 delegation wave (P1-P3, see
below) reported on delegation *frequency*, not on ceiling calibration, and
its ERROR tier at 2,400 chars sat above the max of every aligned repo, so it
could not fire on the defect this file describes.

**Head cost is a bundle-level number, not only a per-description one.** The
validators report, per bundle, `context.include` chars + agent description
chars + skill description chars, with a **WARNING at 4,000 chars**
(`validate-bundle-repo.yaml` Phase 2.86, which states the attribution rule
for each term). The threshold is measured the same way — every figure below
is that step's own output on 2026-09-07:

| Bundle | head chars | engineered for head cost? |
|---|---|---|
| `bundles/anchors-amp-dev/bundle.md` | 1,760 | yes |
| `bundles/anchors/bundle.md` | 2,483 | yes |
| context-intelligence `bundle.md` | 5,479 | no |
| foundation `bundle.md` (root) | 14,198 | no |
| superpowers `bundle.md` | 15,002 | no |
| foundation `behaviors/agents.yaml` | 26,368 | no |
| dot-graph `bundle.md` | 29,684 | no |
| converge `bundle.md` | 87,555 | no |

4,000 is the smallest round number above every bundle that was explicitly
cost-engineered (1.6x headroom over the largest) and below every bundle that
was not (1.37x below the nearest). It is a WARNING, not an ERROR, because
foundation's own root bundle exceeds it — that is a design conversation, not
a build break.

Note what the anchors row shows: `bundles/anchors` mounts `tool-skills` with
`visibility.enabled: false`, so its 1,261 chars of skill descriptions are
**not** in the head. The check reports that amount separately as
`skill_description_chars_excluded` rather than silently omitting it, because
turning visibility off is a real lever and the reader should see what it
bought.

## V6 — Provider disposition: absolutes for invariants, decision rules for judgment

Compliant models (measured: "sol complies") execute literal imperatives —
MUST, ALWAYS, PROACTIVELY, NEVER — even when a case-by-case judgment call
would serve the task better. Judgment-oriented models (measured: "opus
judges") already weigh trade-offs and don't need to be shouted at; absolutes
aimed at them just add noise. OpenAI's own GPT-5.6 guidance independently
concurs: reserve hard imperatives for things that are actually invariant,
and give everything else a decision rule the model can apply to the
situation in front of it.

**Rule:** Use ALWAYS / MUST / NEVER / PROACTIVELY only for conditions that
are true 100% of the time with no legitimate exception. For anything that
depends on context, state the deciding factor, not the verdict.

**Before / after:**

```
BEFORE (absolute applied to a judgment call):
  "ALWAYS delegate multi-file exploration tasks. NEVER read more than
  2 files yourself — delegate to this agent instead."

AFTER (decision rule):
  "Delegate when the exploration spans more files than you can hold in
  working context at once, or when the caller needs a structured survey
  rather than a single answer. Reading 1-2 files directly to answer a
  narrow question is fine and does not need delegation."
```

The AFTER version gives the model the actual factor to weigh (context cost,
answer shape) instead of a bright line the model must either obey literally
or silently override.

## V7 — Shape: trigger first, then USE WHEN / DO NOT USE WHEN

A description is read by a router deciding *whether this is the thing*, not by
a person learning what it does. Order it accordingly.

1. **Trigger first.** The opening clause states the condition under which this
   capability applies — not its identity, not its history, not its
   architecture. "Use for `type: browser` acceptance tests" routes; "A
   sophisticated browser automation subsystem" does not.
2. **Then USE WHEN.** The positive decision rule, as a rule and not as a
   dialogue (V3, V6).
3. **Then DO NOT USE WHEN.** Explicit, and pointing at the thing that *should*
   handle it. This is the half most often missing, and its absence is what
   produces the silent failure this whole policy exists to prevent: a router
   that picks the nearest-sounding capability, does the wrong work, and gives
   nobody a reason to trace it back. "DO NOT USE for interactive TUIs — use
   `terminal-tester`" costs one clause and removes a whole class of misroute.

Everything else — how it works, what it carries, what it depends on — belongs
in the body, which is read on demand and not on every request.

**The one rule this shape must never lose: fidelity beats brevity.** A
description that got shorter by dropping a trigger has not been improved; it
has been broken in a way that surfaces later as "it didn't use the right
thing", with no trace back to the commit that caused it. Any automated or
assisted shortening pass must therefore show a **fidelity table** — every
routing fact in the before text, and where it went in the after text — which
is why `recipes/refresh-descriptions.yaml` is agent-driven and not a regex.
See `docs/BUNDLE_GUIDE.md` §"Refreshing descriptions".

## Example policy (all description surfaces)

- **No `<example>` blocks.** Not "at most 2" — zero. Trigger conditions
  belong in the WHEN clause as a decision rule (V6), not as a worked
  dialogue example. An example block does not teach the model anything a
  clear WHEN clause can't state directly, and it is paid on every turn
  regardless of whether the agent is ever delegated to.
- **No `<commentary>` tags.** Subsumed by the no-examples rule (commentary
  only ever appeared inside example blocks) — stated separately because a
  stray `<commentary>` tag outside an example block is still a defect.
- **If a worked example is genuinely useful for human authors** (teaching a
  new bundle author how to phrase a description, say), it belongs in a body
  doc — AGENT_AUTHORING.md, a README, or the artifact's own markdown body —
  never in the `description` field itself. The description field is
  metadata sent to the model every turn; a doc file is read on demand.
- **Any example still present in a shipped description is stale content**
  (V2), whether or not it matches current behavior — delete it, don't
  archive it in place.

## Staleness and deletion

Governed by `CONTEXT_POISONING.md` §"Aggressive Deletion" and §"Maximum
DRY" — this file does not restate that guidance. The short version: find the
canonical source, delete the duplicate, update cross-references. Applies to
descriptions exactly as it applies to any other doc content.

---

## Settled — the P1-P3 delegation wave (2026-08-30, 18 runs, pre-registered gates)

- **P1+P3** (tested together): relaxed decision-rule language + 6 negative
  examples did **not** reduce gpt-5.6-sol's root delegation — median 7 (7,
  6, 11) vs control's 6 (8, 6, 6); gate required ≤4. Quality unaffected
  (3/3, both providers); claude-opus unaffected (0 delegates, all arms).
- **P2:** stripping all advocacy and all `<example>` blocks at assembly time
  (delegate description 14,562 → 8,060 chars) also did not reduce sol's
  delegation — median 9, higher than control. Quality unaffected, both
  providers.

**Conclusion:** sol-class over-delegation is model-intrinsic, not a
promptable framing effect — description language is not a delegation-
*frequency* lever. V1-V6 above stand on their original evidence (token cost,
stability, quality-neutrality — real every-turn wire savings) but must not
be sold as delegation-behavior fixes. The lever is mechanical spawn budgets,
not description wording — separate work.

The wave measured delegation counts and wire sizes, not V5's token-ceiling
thresholds — **agent `meta.description` budgets in V5 remain provisional.**
It supplies real distribution data (8,060 / 14,562 / 16,461 chars across
arms) but WARN/ERROR calibration is still open.
