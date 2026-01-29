# Agent Authoring Guide

Agents are specialized AI configurations that run as sub-sessions for focused tasks.

**Key insight: Agents ARE bundles.** They use the same file format and are loaded via `load_bundle()`. The only difference is the frontmatter key (`meta:` vs `bundle:`).

→ For file format, tool/provider configuration, @mentions, and composition, see **[BUNDLE_GUIDE.md](BUNDLE_GUIDE.md)**
→ For agent spawning and resolution patterns, see **[PATTERNS.md](PATTERNS.md)**

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
1. **WHEN** should I use this agent? (Activation triggers)
2. **WHAT** does it do? (Core capability)
3. **HOW** do I invoke it? (Examples)

### Pattern

```yaml
meta:
  name: my-agent
  description: |
    [WHEN to use - activation triggers]. Use PROACTIVELY when [condition].
    
    [WHAT it does - core capability in 1-2 sentences].
    
    Examples:
    
    <example>
    user: '[Example user request]'
    assistant: 'I'll use my-agent to [action].'
    <commentary>[Why this agent is the right choice]</commentary>
    </example>
```

### Real Example

```yaml
meta:
  name: bug-hunter
  description: |
    Specialized debugging expert. Use PROACTIVELY when user reports errors,
    unexpected behavior, or test failures.
    
    Examples:
    
    <example>
    user: 'The pipeline is throwing a KeyError somewhere'
    assistant: 'I'll use bug-hunter to systematically track down this KeyError.'
    <commentary>Bug reports trigger bug-hunter delegation.</commentary>
    </example>
    
    <example>
    user: 'Tests are failing after the recent changes'
    assistant: 'Let me use bug-hunter to investigate the test failures.'
    <commentary>Test failures are a clear debugging task.</commentary>
    </example>
```

### Anti-Patterns

```yaml
# ❌ Too vague - when would you use this?
meta:
  description: "Helps with code stuff"

# ❌ No examples - callers have to guess
meta:
  description: "Analyzes code for quality issues"

# ✅ Clear triggers + capability + examples
meta:
  description: |
    Use PROACTIVELY when user reports errors or test failures.
    Systematic debugging with hypothesis-driven root cause analysis.
    
    <example>
    user: 'The build is failing'
    assistant: 'I'll use bug-hunter to investigate.'
    </example>
```

---

## Description Requirements (Critical)

The `meta.description` field is the **ONLY** discovery mechanism for agents. When the
task tool presents available agents to the LLM, this description is all it sees to
decide which agent to use.

**Poor descriptions cause delegation failures.** One-liner descriptions are unacceptable.

### Required Elements

Every agent description MUST include:

#### 1. WHY - The Purpose
What problem does this agent solve? What value does it provide?

#### 2. WHEN - Activation Triggers  
Explicit conditions that should cause delegation to this agent.
Use keywords: MUST, REQUIRED, ALWAYS, PROACTIVELY, "Use when..."

#### 3. WHAT - Domain/Taxonomy Terms
Keywords and concepts this agent is authoritative on.
Pattern: `**Authoritative on:** term1, term2, term3, "multi-word concept"`

This serves as the agent's "taxonomy" - terms that should trigger delegation.

#### 4. HOW - Usage Examples
Concrete examples showing user request → delegation rationale.
Use `<example>` blocks with `<commentary>` tags.

### Template

```yaml
meta:
  name: my-agent
  description: |
    [ONE SENTENCE: What this agent does and why it matters]
    
    Use PROACTIVELY when [primary trigger condition].
    
    **Authoritative on:** [comma-separated domain terms/keywords]
    
    **MUST be used for:**
    - [Condition 1]
    - [Condition 2]
    
    <example>
    user: '[Example user request]'
    assistant: 'I'll delegate to [agent] because [reason].'
    <commentary>
    [Why this triggers the agent - helps LLMs learn the pattern]
    </commentary>
    </example>
```

### Anti-Patterns

❌ One-liner descriptions: `"Helps with debugging"`
❌ No trigger conditions: Missing WHEN to use
❌ No taxonomy terms: LLM can't match domain questions
❌ No examples: LLM doesn't learn delegation patterns

### Audit Your Agents

Check each agent's description against these criteria:
- [ ] >100 words (not a one-liner)
- [ ] Has explicit trigger conditions
- [ ] Lists domain terms ("Authoritative on:")
- [ ] Includes at least one example
- [ ] Explains the value proposition

---

## Instruction Structure

The markdown body after frontmatter becomes the agent's system prompt. Recommended structure:

```markdown
# Agent Name

[One-line role description]

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
```

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
