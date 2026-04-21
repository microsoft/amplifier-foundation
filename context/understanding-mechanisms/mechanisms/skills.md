# Skills: On-Demand Domain Knowledge Packages

## What It Is

A skill is a reusable, on-demand knowledge package that agents can discover and
load via the `load_skill` tool. Skills follow the open Agent Skills specification
(agentskills.io) and provide progressive disclosure: metadata is always visible,
full content loads on demand, and companion files are accessible as needed.

Skills are the lightest-weight mechanism for extending agent capabilities. They're
just directories containing a `SKILL.md` file with YAML frontmatter + markdown.

## How It Works

### Skill Structure

```
my-skill/
  SKILL.md           # Required -- YAML frontmatter + markdown body
  patterns.md        # Optional companion file (Level 3)
  examples/
    example.py       # Optional companion
```

### SKILL.md Format

```yaml
---
name: my-skill
description: "What this skill provides and when to use it"
version: 1.0.0
---

# My Skill

Instructions the agent follows when this skill is loaded.
Can reference companion files via ${SKILL_DIR}/patterns.md.
```

### Three Levels of Progressive Disclosure

| Level | What | Token Cost | When |
|-------|------|-----------|------|
| L1: Metadata | name + description | ~100 tokens | Always -- visibility hook |
| L2: Content | Full SKILL.md body | ~1-5K tokens | On demand -- load_skill() |
| L3: References | Companion files | 0 until accessed | Explicit read_file() |

### Visibility Hook

A `SkillsVisibilityHook` fires before every LLM call, injecting available skill
names and descriptions into context. The agent doesn't need to call `load_skill(list=true)`
-- skills are automatically surfaced.

### Fork Skills (context: fork)

When a skill has `context: fork` in frontmatter, loading it spawns an isolated
subagent. The skill's body becomes the subagent's instruction. The subagent gets
its own context window, tools, and conversation -- then returns a result.

This bridges the gap between skills (lightweight knowledge) and agents (isolated
execution). Fork skills can specify:
- `model_role` for model selection (coding, reasoning, critique, etc.)
- `allowed-tools` to restrict available tools
- `provider_preferences` for model routing

### Real Skills Inventory

**Built-in power skills** (user-invoked via slash commands):
- `code-review` -- spawns 3 parallel review agents (fork skill)
- `mass-change` -- 3-phase orchestrator for large-scale changes (fork skill)
- `session-debug` -- delegates to session-analyst (fork skill)

**Superpowers skills**:
- `superpowers-reference` -- complete mode/agent/recipe reference tables
- `integration-testing-discipline` -- E2E testing discipline

**External skills** (from obra/superpowers):
- brainstorming, systematic-debugging, verification-before-completion,
  test-driven-development, writing-plans, subagent-driven-development

### Enhanced Features

- Shell preprocessing: `` !`command` `` in body replaced with stdout at load time
- Variable substitution: `${SKILL_DIR}`, `$ARGUMENTS`, `$0`, `$1`...
- Security: untrusted (remote) skills have shell commands blocked
- Git URL sources: skills can be loaded from remote git repos
- `disable-model-invocation: true` hides from auto-visibility
- `user-invocable: true` registers as `/name` slash command
- `auto-load: true` loads at session startup

### Key Implementation Files

- `amplifier-bundle-skills/modules/tool-skills/__init__.py` -- SkillsTool (793 lines)
- `amplifier-bundle-skills/modules/tool-skills/discovery.py` -- Discovery + metadata
- `amplifier-bundle-skills/modules/tool-skills/hooks.py` -- Visibility hook
- `amplifier-bundle-skills/modules/tool-skills/preprocessing.py` -- Shell/var processing
- `amplifier-bundle-skills/modules/tool-skills/model_resolver.py` -- 5-level model selection

## Relevance to System Design Bundle

Skills are arguably the **highest-leverage mechanism** for our system design bundle,
especially for initial experiments. They're lightweight, fast to create, and can
deliver immediate value.

### Immediate Opportunities

- **System design methodology skill**: A comprehensive skill containing the
  structured response template from the agentic-system-design doc (problem framing,
  assumptions, boundaries, components, flows, risks, tradeoffs, design, migration,
  metrics). Loaded when the agent encounters a design problem.

- **System type skills**: Separate skills for different system types (web service,
  web app, distributed system, peer-to-peer, event-driven). Each contains patterns,
  common pitfalls, and evaluation criteria specific to that system type. The agent
  loads the relevant one based on what kind of system is being designed.

- **Tradeoff analysis skill**: A skill that guides the agent through structured
  tradeoff analysis using the fixed frame (latency, complexity, reliability, cost,
  security, scalability, reversibility, organizational fit).

- **Adversarial review skill**: A fork skill that spawns multiple review agents
  with different perspectives (SRE, security reviewer, staff engineer, finance
  owner, operator) -- similar to how code-review spawns parallel review agents.

- **Architecture primitives skill**: A skill containing reusable abstractions
  (boundaries, contracts, state machines, queues, caches, idempotency, etc.) with
  guidance on when each pattern is appropriate AND when it's wrong.

### Skills vs Other Mechanisms

For our bundle, skills should be the **default starting point** because:
- Zero Python code needed (just markdown files)
- Progressive disclosure saves tokens (only loaded when relevant)
- Fork skills provide agent-like isolation when needed
- Easy to iterate and experiment (just edit SKILL.md)
- Cross-harness portable (Agent Skills spec)

Graduate to other mechanisms when:
- Need structured data manipulation -> tool
- Need automated real-time feedback -> hook
- Need multi-step workflows -> recipe
- Need persistent agent specialization -> agent
- Need always-present guidance -> context file

## Context Window Impact

Skills have a three-tiered token cost model that makes them significantly more
context-efficient than content files for reference material.

### L1: Visibility Hook (~1,000-1,500 tokens per turn)

The `SkillsVisibilityHook` fires on every `provider:request` event and injects
all skill names and descriptions as an ephemeral `<system-reminder>`. This is the
"menu" the agent sees to know what skills are available.

| Factor | Detail |
|--------|--------|
| Injection type | Ephemeral (not stored in message history) |
| Frequency | Every LLM call (including tool-loop iterations) |
| Size | ~80 chars per skill x number of composed skills |
| Configurable | `max_visible: 50` limits regular skills shown |
| Compactable | No -- ephemeral injections bypass compaction |

With 25 skills, this costs roughly 1,000-1,500 tokens per turn. It scales with
the number of skills composed into the bundle. Skills with
`disable-model-invocation: true` are excluded from the visibility list, reducing
this overhead.

**Design implication:** Every skill added to a bundle increases the per-turn
visibility cost. Consider using `disable-model-invocation: true` for skills that
are only user-invoked (via slash commands) to keep the visibility injection lean.

### L2: Loaded Content (compactable tool result)

When the agent calls `load_skill(skill_name="...")`, the full skill body is
returned as a **tool result** -- stored in the message history and subject to
normal compaction.

| Factor | Detail |
|--------|--------|
| Storage | Message history (as tool result) |
| Compactable | Yes -- truncated at Levels 1-2, removed at Level 3+ |
| Typical size | 1,000-5,000 tokens per skill |
| Protected | Only if among the last 5 tool results |

This is the key advantage over content files: a skill's full body is paid **once
when loaded** and then gradually compacted as the session progresses. A content
file of the same size would be paid on **every turn forever**.

### L3: Companion Files (on-demand, compactable)

Companion files accessed via `read_file(skill_directory + "/file.md")` are also
tool results -- stored in message history, subject to compaction. Zero cost until
explicitly accessed.

### Fork Skills: Agent-Level Isolation

Fork skills (`context: fork`) behave like agents from a token perspective:

| Factor | Detail |
|--------|--------|
| Parent cost | ~300-700 tokens (tool call + result summary) |
| Child cost | Full skill body + all tool use within the fork session |
| Isolation | Complete -- child context discarded after execution |

Fork skills are the most token-efficient option for heavy skill work. The parent
pays only for the summary result.

### Skills vs Content Files: Token Comparison

For a 3,000-token reference document over a 50-turn session:

| Mechanism | Total tokens consumed |
|-----------|---------------------|
| Content file (@mention) | 3,000 x 50 turns = **150,000 tokens** |
| Inline skill (loaded once) | 100/turn visibility + 3,000 once = **8,000 tokens** (compactable after use) |
| Fork skill (loaded once) | 100/turn visibility + ~400 result = **5,400 tokens** |

The difference is dramatic. Skills are the right mechanism for any reference
material that doesn't need to be present on every single turn.
