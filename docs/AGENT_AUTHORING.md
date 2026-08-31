# Agent Authoring Guide

Agents are specialized AI configurations that run as sub-sessions for focused tasks.

**Key insight: Agents ARE bundles.** They use the same file format and are loaded via `load_bundle()`. The only difference is the frontmatter key (`meta:` vs `bundle:`).

→ For file format, tool/provider configuration, @mentions, and composition, see **[BUNDLE_GUIDE.md](BUNDLE_GUIDE.md)**
→ For agent spawning and resolution patterns, see **[PATTERNS.md](PATTERNS.md)**
→ For how the agent discovers and applies per-repo conventions *once it is spawned* (the other half of the delegation loop), see **[PER_REPO_CONVENTIONS.md](PER_REPO_CONVENTIONS.md)**

This guide covers only what's **unique to agents**.

---

## Quick Comparison: Agent vs Bundle

| Aspect | Bundle | Agent |
|--------|--------|-------|
| Frontmatter key | `bundle:` | `meta:` |
| Required fields | `name`, `version` | `name`, `description` |
| Loaded via | `load_bundle()` | `load_bundle()` (same!) |
| Purpose | Session configuration | Sub-session with focused role |

```yaml
# Bundle frontmatter          # Agent frontmatter
bundle:                        meta:
  name: my-bundle                name: my-agent
  version: 1.0.0                 description: "..."
```

---

## The `meta.description` Field: Your Agent's Advertisement

**This is THE critical field for agent discoverability.** The coordinator and task tool see this description when deciding which agent to delegate to.

### What Makes a Good Description

Answer three questions:
1. **WHAT** does it do? (Core capability)
2. **WHEN** should I delegate to it? (The deciding factor -- see
   description-authoring-principles.md V6 for phrasing)
3. **HOW** do I invoke it? (≤2 examples)

### Pattern

```yaml
meta:
  name: my-agent
  description: |
    [WHAT it does - core capability in 1-2 sentences]. [WHEN to delegate -
    the deciding factor, not a bare imperative -- see
    description-authoring-principles.md V6].

    <example>
    user: '[Example user request]'
    assistant: 'I'll use my-agent to [action].'
    </example>
```

See `context/shared/description-authoring-principles.md` for trigger
phrasing (decision rules over bare absolutes), the example cap (≤2, no
`<commentary>`), and the token budget.

### Real Example

Matches the currently shipped `agents/bug-hunter.md` description:

```yaml
meta:
  name: bug-hunter
  description: "Specialized debugging expert focused on finding and fixing
    bugs systematically. Use PROACTIVELY. It MUST BE USED when user has
    reported or you are encountering errors, unexpected behavior, or test
    failures. Examples: <example>user: 'The synthesis pipeline is throwing
    a KeyError somewhere' assistant: 'I'll use the bug-hunter agent to
    systematically track down and fix this KeyError.'</example>"
```

### Anti-Patterns

```yaml
# ❌ Too vague - when would you use this?
meta:
  description: "Helps with code stuff"

# ❌ No examples - callers have to guess
meta:
  description: "Analyzes code for quality issues"

# ✅ Clear capability + trigger + example
meta:
  description: |
    Systematic debugging with hypothesis-driven root cause analysis.
    Use when user reports errors, unexpected behavior, or test failures.

    <example>
    user: 'The build is failing'
    assistant: 'I'll use bug-hunter to investigate.'
    </example>
```

---

## Description Requirements

The `meta.description` field is the **ONLY** discovery mechanism for agents. When the
task tool presents available agents to the LLM, this description is all it sees to
decide which agent to use.

**How to write it is governed by the canonical
[description-authoring-principles.md](../context/shared/description-authoring-principles.md)**
-- trigger phrasing (decision rules over bare absolutes), the example policy
(≤2 examples, no `<commentary>`), staleness/deletion, and provider
disposition all live there and are not restated here.

### Required Elements

Every agent description should cover:

#### 1. WHAT - The Capability
What does this agent do? What value does it provide?

#### 2. WHEN - The Deciding Factor
The condition that should cause delegation, phrased as a decision rule
(see description-authoring-principles.md V6) rather than a bare imperative.

#### 3. Authoritative On (optional)
Domain terms this agent owns, so questions in that domain route here.
Pattern: `**Authoritative on:** term1, term2, "multi-word concept"`

#### 4. Examples
Up to 2 `<example>` blocks (no `<commentary>`) showing a real request and
the resulting delegation. Each example must reflect what the agent
actually does today.

### Template

```yaml
meta:
  name: my-agent
  description: |
    [ONE SENTENCE: What this agent does]

    Use when [the deciding factor -- context shape, not a bare imperative].

    **Authoritative on:** [comma-separated domain terms/keywords]

    <example>
    user: '[Example user request]'
    assistant: 'I'll delegate to [agent] because [reason].'
    </example>
```

### Anti-Patterns

❌ One-liner descriptions: `"Helps with debugging"`
❌ No indication of WHEN to delegate
❌ No taxonomy terms: LLM can't match domain questions
❌ No examples: LLM doesn't learn delegation patterns
❌ More than 2 examples, or any `<commentary>` tag
❌ An example describing a capability the agent no longer has (stale --
see description-authoring-principles.md V2)

### Description Token Budget

**Provisional** (pending A/B calibration -- see
description-authoring-principles.md V5): WARN above 300 tokens, ERROR
above 600 tokens. Enforced by `foundation:recipes/validate-agents.yaml`
and `foundation:recipes/validate-bundle-repo.yaml`.

---

## Instruction Structure

The markdown body after frontmatter becomes the agent's system prompt. It is instruction addressed to the agent — never a description of the agent for a human reader. That belongs in the frontmatter `meta.description`, which is metadata and is never sent to the model. See [What Goes Below the Frontmatter](BUNDLE_GUIDE.md#what-goes-below-the-frontmatter) for the general rule and the enforcing validators.

Recommended structure:

```markdown
# Agent Name

You are [role]. You [what you do, in one line].

**Execution model:** You run as a one-shot sub-session. Work with what 
you're given and return complete results.

## Operating Principles
1. [Principle 1]
2. [Principle 2]

## Workflow
1. [Step 1]
2. [Step 2]

## Output Contract

Your response MUST include:
- [Required element 1]
- [Required element 2]

---

@foundation:context/shared/common-agent-base.md
```

**Always end with the @mention** to include shared base instructions (git guidelines, tone, security, tool policies).

**Output contracts need an honest-stop valve.** Whenever your `## Output Contract` *compels* production of an item ("MUST include", "always report", "fill in"), make sure the agent is never cornered into fabricating it. The shared base instructions define the three-case **Honest Stopping** pattern — provide the item when there's real evidence, mark it `N/A — <reason>` when it genuinely doesn't apply, and **stop and report back** when it's required but can't be honestly satisfied. An agent told to "always produce X" with no path for "I can't honestly produce X" will invent X to complete the task. Lean on the base principle rather than restating it — and do not write local wording that re-compels output without the valve (e.g. "fill in the evidence" / "do not skip" with no third case).

---

## Model Selection with `model_role`

Agents can declare what *kind* of model they need rather than pinning a specific provider or model. The routing matrix resolves the role to a concrete provider/model at session start, based on the active matrix and installed providers.

### The `model_role` Frontmatter Field

**String shorthand** — request a single role:

```yaml
meta:
  name: my-agent
  description: "..."
  model_role: coding
```

**List form with fallback chain** — try roles in order:

```yaml
meta:
  name: my-agent
  description: "..."
  model_role: [vision, coding, general]
```

With the list form, the system tries `vision` first. If no installed provider matches any candidate for that role, it falls back to `coding`, then `general`.

### Available Roles

| Role | Use for |
|------|---------|
| `coding` | Code generation, implementation, debugging |
| `ui-coding` | Frontend/UI code — components, layouts, styling, spatial reasoning |
| `security-audit` | Vulnerability assessment, attack surface analysis, code auditing |
| `reasoning` | Deep architectural reasoning, system design, complex multi-step analysis |
| `critique` | Analytical evaluation — finding flaws in existing work, not generating solutions |
| `creative` | Design direction, aesthetic judgment, high-quality creative output |
| `writing` | Long-form content — documentation, marketing, case studies, storytelling |
| `research` | Deep investigation, information synthesis across multiple sources |
| `vision` | Understanding visual input — screenshots, diagrams, UI mockups |
| `image-gen` | Image generation, visual mockup creation, visual ideation |
| `critical-ops` | High-reliability operational tasks — infrastructure, orchestration, coordination |
| `fast` | Quick utility tasks — parsing, classification, file ops, bulk work |
| `general` | Versatile catch-all, no specialization needed |

> **Choosing the right role?** Use `load_skill(skill_name='role-definitions')` for detailed guidance on each role, including decision flowchart, "when to use / when NOT to use" recommendations, model tier grid, and fallback chain best practices.

Every routing matrix must define `general` and `fast`. Other roles are optional — if a role isn't defined in the active matrix, the fallback chain skips it.

### Example Agent Frontmatter

```yaml
---
meta:
  name: code-reviewer
  description: |
    Use PROACTIVELY when user asks for code review or quality analysis.
    Systematic review with actionable feedback.
  model_role: [coding, general]
---

# Code Reviewer

[Agent instructions...]
```

### Escape Hatch: `provider_preferences`

If you need to pin a specific provider and model (bypassing routing), `provider_preferences` in agent frontmatter still works:

```yaml
meta:
  name: my-agent
  description: "..."
  provider_preferences:
    - provider: anthropic
      model: claude-opus-4-6
```

When both `model_role` and `provider_preferences` are present, `provider_preferences` takes priority.

---

## Sub-Agent Access Control with `agents`

An agent can declare which sub-agents its own spawned session may delegate to. This is the `agents` field in agent frontmatter - a **Smart Single Value** taking one of three forms.

> **Not the same as a bundle's `agents:` section.** A bundle or behavior uses `agents:` with a **mapping** value (`include:` lists, inline definitions) to declare which agents it *provides*. An agent uses `agents:` with a **string or list** value to declare which agents it may *delegate to*. Same key, two meanings, told apart by value type. See [BUNDLE_GUIDE.md](BUNDLE_GUIDE.md) for the roster form.

### The `agents` Frontmatter Field

`agents` is a top-level key - a sibling of `meta:`, alongside `tools:` and `providers:` - not a field nested inside `meta:`.

**Disable delegation entirely** - the agent does the work itself:

```yaml
meta:
  name: leaf-worker
  description: "..."

agents: none
```

**Allowlist** - the agent may delegate only to the named agents:

```yaml
meta:
  name: coordinator
  description: "..."

agents: [explorer, bug-hunter]
```

**Inherit everything** - the default, and identical to omitting the field:

```yaml
meta:
  name: orchestrator
  description: "..."

agents: all
```

An allowlist is satisfied from every agent available to the parent session - both those declared statically by the bundle and those contributed at runtime by an active mode. Naming an agent that does not exist yields no error; the agent simply is not available to delegate to.

A value that is neither `all`, `none`, a list, nor a mapping raises at load time rather than being ignored, so a typo fails loudly instead of silently granting full access.

### Example Agent Frontmatter

```yaml
---
meta:
  name: security-auditor
  description: |
    Use PROACTIVELY for vulnerability assessment and code auditing.
    Reviews directly without delegating.
  model_role: security-audit

agents: none

tools:
  - module: tool-filesystem
  - module: tool-bash
---

# Security Auditor

[Agent instructions...]
```

> **Where this is enforced.** The reference spawn capability (`amplifier-app-cli`'s `session_spawner.py`) applies the declaration when it builds the child session's config, filtering both the parent's static agents and any runtime-contributed ones. An agent declaring `agents: none` receives an empty agent set, so its `delegate` tool has nothing to call.

Restricting delegation is not a security boundary - it shapes what an agent is *meant* to reach for, keeping a focused worker focused. To remove the capability itself, drop the delegation tool from the agent's `tools:` list.

---

## Agents as Context Sinks

Expert agents serve as **context sinks** - they carry heavy documentation that would bloat every session if always loaded.

### Why This Matters

- **Token efficiency**: Heavy docs load ONLY when agent spawns, not in every session
- **Delegation pattern**: Parent sessions stay lean; sub-sessions burn context doing work
- **Longer session success**: Critical strategy for sessions that run many turns

### Structure

```yaml
---
meta:
  name: my-expert
  description: "Expert for X domain. Delegate when user needs..."
---

# My Expert

[Role description]

## Knowledge Base

@my-bundle:docs/FULL_GUIDE.md        # Heavy docs - loaded only when spawned
@my-bundle:docs/REFERENCE.md         # More heavy docs
@my-bundle:docs/PATTERNS.md          # Even more

---

@foundation:context/shared/common-agent-base.md
```

### The Behavior + Agent Pattern

Pair your expert agent with a behavior that injects a thin awareness pointer:

```yaml
# behaviors/my-expert.yaml
bundle:
  name: behavior-my-expert
  version: 1.0.0

agents:
  include:
    - my-bundle:my-expert    # Heavy agent file

context:
  include:
    - my-bundle:context/my-awareness.md  # Thin pointer (~30 lines)
```

The thin awareness file tells root sessions: "This domain exists. Delegate to `my-bundle:my-expert`."

The agent file carries all the heavy @mentions that only load when the agent is actually spawned.

### Anti-Pattern: Heavy Context in Behaviors

```yaml
# ❌ BAD: Heavy docs in behavior context (loads for everyone)
context:
  include:
    - my-bundle:docs/FULL_GUIDE.md      # 500 lines in every session!
    - my-bundle:docs/REFERENCE.md       # More bloat

# ✅ GOOD: Thin pointer in behavior, heavy docs in agent
context:
  include:
    - my-bundle:context/awareness.md    # 30 lines: "domain exists, delegate"

# ✅ EVEN BETTER: No always-on context at all when an expert agent owns the domain
agents:
  include:
    - my-bundle:my-expert    # Agent meta.description IS the discovery surface
# (no context.include block needed — the agent catalog tells the LLM "this exists")
```

### Hard policy: behavior `context.include` token budget

**Rules (enforced by `foundation:recipes/validate-bundle-repo.yaml`):**

| Per-file size | Verdict |
|---|---|
| < 500 tokens | OK — qualifies as lightweight awareness, may stay |
| 500–1,000 tokens | WARNING — must justify; consider moving to agent body |
| > 1,000 tokens | ERROR — must move to agent body (context-sink), mode contribution, or skill |

**The default for any behavior `context.include` entry >1,000 tokens is: this is NOT a behavior context.include candidate. Find another mechanism.** Mechanisms ranked by load semantics:

1. **Expert agent body** (`@-mention` in agent `.md`) — loads only when delegated to. Best for reference docs that fit a domain specialist.
2. **Mode contribution** (`@-mention` in mode body OR `contributes.context` in mode frontmatter) — loads only when mode is active. Best for workflow-specific reference.
3. **Skill** (`load_skill` on demand) — loads only when explicitly invoked. Best for procedural guidance, not reference catalogs.
4. **Soft reference** (plain path in prose, no `@`) — agent must `read_file` it. Useful for files the root session may need inline but rarely.

Only put a file in behavior `context.include` if you can clearly defend "this must be in every session that composes this behavior, because it announces a capability or runtime convention the LLM will need universally." If the answer is "well, sometimes the LLM benefits from knowing this" — that's not enough. Move it.

---

## Common Mistakes

### 1. Vague Description
Callers don't know when to use the agent. Add activation triggers and examples.

### 2. Missing @mention Base
Forgetting `@foundation:context/shared/common-agent-base.md` causes inconsistent behavior.

### 3. No Output Contract
Callers don't know what to expect back. Define what the agent returns.

### 4. Treating Agents as Different from Bundles
Agents ARE bundles. Don't reinvent - use the same patterns from BUNDLE_GUIDE.md.

### 5. Heavy Docs in Always-Loaded Context
Put heavy @mentions in agent files (context sink), not in behavior context.include.

---

## Reference

| Topic | Documentation |
|-------|---------------|
| File format, YAML structure | [BUNDLE_GUIDE.md](BUNDLE_GUIDE.md) |
| Tool/provider configuration | [BUNDLE_GUIDE.md](BUNDLE_GUIDE.md) |
| @mention resolution | [BUNDLE_GUIDE.md](BUNDLE_GUIDE.md) |
| Agent spawning patterns | [PATTERNS.md](PATTERNS.md) |
| Agent resolution | [PATTERNS.md](PATTERNS.md) |
| Bundle composition | [CONCEPTS.md](CONCEPTS.md) |
| Per-repo conventions an agent reads at runtime | [PER_REPO_CONVENTIONS.md](PER_REPO_CONVENTIONS.md) |
