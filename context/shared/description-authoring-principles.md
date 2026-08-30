# Description Authoring Principles

> **Canonical source.** This is the ONE place that states how to write a
> *description* field — agent `meta.description`, skill frontmatter
> `description`, mode `description`, and tool description strings. Every
> other doc that touches this topic (AGENT_AUTHORING.md, BUNDLE_GUIDE.md,
> DOMAIN_VALIDATOR_GUIDE.md, skill authoring guides, validator recipes)
> **points here** instead of restating it. If you find yourself copying a
> paragraph from this file into another doc, stop — link instead (see V1/V2
> below, and don't make this file the exception to its own rule).

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
that agent is used. `<commentary>` blocks inside `<example>` blocks were
validated for removal with no quality loss. Policy: **at most 2 examples**,
**no `<commentary>` tags**, and any example that ships must reflect what the
agent actually does today — an example is a claim about current behavior,
and a stale one is V2's problem wearing a WHEN-to-delegate costume.

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

**Budgets (token count via the same tokenizer the validators use):**

| Surface | WARN | ERROR | Status |
|---|---|---|---|
| Mode `description` | > 500 tokens | > 800 tokens | Established (validate-bundle-repo.yaml Phase 2.7) |
| Agent `meta.description` | > 300 tokens | > 600 tokens | **Provisional** — pending wave calibration |
| Skill frontmatter `description` | see skills authoring guide | see skills authoring guide | Shared cap: `max_skills_visible` bounds total visible-catalog cost |
| Tool description | no fixed ceiling yet | no fixed ceiling yet | Flag any single tool >10% of a typical tool-description budget as a design smell |

Agent tiers are lower than mode tiers because agent descriptions are paid
**by every session that has the agent in its catalog**, regardless of
whether it's ever delegated to — mode descriptions are paid only by sessions
that load that mode. Treat the agent-tier numbers as provisional — the 2026-08-30
delegation wave (P1-P3, see below) reported on delegation *frequency*, not on
token-ceiling calibration; that calibration remains open.

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

## Example policy (all description surfaces)

- **≤2 examples.** A third example is rarely teaching a new case — audit
  whether it's actually a duplicate before adding it.
- **No `<commentary>` tags.** The example itself should be self-explanatory;
  if it needs a footnote explaining why it triggers the agent, the trigger
  condition belongs in the WHEN clause, not in prose bolted onto the example.
- **Examples must match shipped reality.** An example describing a
  capability, tool, or workflow the artifact no longer has is stale content
  (V2) — delete or update it, don't leave it as aspirational documentation.

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
