# Agents: Specialized Sub-Agent Delegation

## What It Is

An agent is a specialist sub-agent that gets spawned as a child session via the
`delegate` tool. Agents are markdown files with `meta:` YAML frontmatter (not
`bundle:` frontmatter) that define a partial configuration overlay merged with
the parent session's config when spawned. They exist primarily as "context sinks"
-- absorbing heavy token costs in their own isolated context and returning only
concise summaries.

## How It Works

### Agent Definition File

```yaml
---
meta:
  name: zen-architect
  description: "Architecture design, code planning, and review. Use PROACTIVELY
    for code planning, architecture design, and review tasks."

model_role: [reasoning, general]

provider_preferences:
  - provider: anthropic
    model: claude-sonnet-*
---

# Architecture Agent

System instruction body -- the agent's persona and operational instructions.

---

@foundation:context/IMPLEMENTATION_PHILOSOPHY.md
@foundation:context/shared/common-agent-base.md
```

### Agent vs Bundle

Agents ARE bundles structurally. The difference is conventional:
- Bundles use `bundle:` frontmatter -- complete, ready-to-run configuration
- Agents use `meta:` frontmatter -- partial overlay merged with parent session

When spawned, agents inherit tools, providers, and hooks from the parent, with
configurable exclusions.

### The Delegate Tool

The `DelegateTool` handles the full agent lifecycle:
1. Parse parameters (agent name, instruction, context settings)
2. Validate agent (check registry, handle "self" and "namespace:path")
3. Apply provider preferences and model_role resolution
4. Build inherited context (two-parameter system)
5. Merge tools (exclusions apply to inheritance, explicit declarations win)
6. Call `session.spawn` capability (app-layer creates child session)
7. Return result with `session_id` for resumption

### Context Inheritance (Two Parameters)

**context_depth** -- HOW MUCH:
- `"none"` -- clean slate
- `"recent"` -- last N turns (default 5)
- `"all"` -- full conversation

**context_scope** -- WHICH content:
- `"conversation"` -- user/assistant text only
- `"agents"` -- includes delegate tool results
- `"full"` -- ALL tool results (truncated to 4000 chars)

### Model Role System

Agents declare what kind of model they need:
- `general` -- versatile (explorer, foundation-expert)
- `fast` -- utility tasks (file-ops, git-ops, shell-exec)
- `[coding, general]` -- code generation (bug-hunter, modular-builder)
- `[reasoning, general]` -- deep analysis (zen-architect)
- `[security-audit, critique, general]` -- security (security-guardian)

Fallback chains are tried left-to-right. The delegate tool can also override
model_role at call time.

### Foundation Agent Catalog (16 agents)

| Agent | Role | Specialization |
|-------|------|---------------|
| zen-architect | reasoning | Architecture design, planning, review |
| explorer | general | Multi-file codebase exploration |
| bug-hunter | coding | Systematic hypothesis-driven debugging |
| modular-builder | coding | Implementation from specifications |
| file-ops | fast | File read/write/edit/search |
| git-ops | fast | Git/GitHub with safety protocols |
| web-research | fast | Web search and information synthesis |
| security-guardian | security-audit | Security reviews, vulnerability assessment |
| session-analyst | fast | Session debugging, transcript repair |
| test-coverage | coding | Test coverage analysis |
| integration-specialist | general | External API/MCP integration |
| post-task-cleanup | fast | Codebase hygiene after tasks |

### Key Implementation Files

- `amplifier-foundation/agents/` -- All 16 agent definition files
- `amplifier-foundation/modules/tool-delegate/__init__.py` -- Delegate tool (1,252 lines)
- `amplifier-core/docs/SESSION_FORK_SPECIFICATION.md` -- Kernel fork mechanism

## Relevance to System Design Bundle

Agents are the mechanism for specialized design expertise that requires dedicated
context and potentially different model capabilities.

### Immediate Opportunities

- **Systems architect agent**: A reasoning-class agent that specializes in system
  modeling -- identifying goals, constraints, actors, resources, interfaces,
  feedback loops, failure modes. Uses the structured response template from the
  agentic-system-design doc. Could be the primary design agent.

- **Design critic agent**: A critique-class agent that reviews proposed designs
  from multiple perspectives. Loaded with adversarial review prompts (SRE, security,
  staff engineer, finance, operator). Uses `model_role: [critique, reasoning, general]`.

- **Constraint analyst agent**: Specialized in identifying, tracking, and evaluating
  design constraints and tradeoffs. Understands that architecture is about choosing
  what to sacrifice, not finding optimal solutions.

- **Codebase surveyor agent**: Extends the explorer pattern but focused on
  architectural understanding -- identifying module boundaries, dependency patterns,
  coupling hotspots, data flows, and architectural debt.

### The Context Sink Pattern for Design

The context sink pattern is especially valuable for design work because:
- Design exploration requires reading many files (codebase survey)
- Different perspectives require different context (SRE vs security vs DX)
- The parent session needs to stay lean for the long design conversation
- Each specialist agent can carry heavy design reference documentation

### Agents vs Skills for Design Capabilities

| Use an Agent When | Use a Skill When |
|-------------------|-----------------|
| Need isolated context window | Knowledge injection is sufficient |
| Need specific model capabilities | Current model works fine |
| Multi-turn investigation needed | Single-shot guidance works |
| Heavy doc consumption required | Lightweight patterns needed |
| Tool restrictions differ from parent | Same tools work |

## Context Window Impact

Agents are the primary **context management** mechanism in Amplifier. The context
sink pattern isn't just an architectural nicety -- it's the core strategy for
keeping the parent session's context window viable across long interactions.

### What the Parent Pays

When the parent delegates to an agent, the context cost in the parent session is:

| Item | Approximate tokens |
|------|--------------------|
| Assistant message with tool_use block (delegate call) | ~100-200 tokens |
| Tool result message (agent's text response) | ~200-500 tokens |
| **Total parent cost per delegation** | **~300-700 tokens** |

This is a tool call/result pair in the message history, subject to normal
compaction. Over time, old delegation results get truncated (to ~250 chars) and
eventually removed by the compaction cascade.

### What the Child Absorbs

The child session absorbs all exploration costs in its own context window:

| Item | Approximate tokens |
|------|--------------------|
| Child's system prompt (agent @mentions + instruction) | 5,000-20,000 tokens |
| File reads, grep results, LSP operations | 10,000-50,000+ tokens |
| Multi-turn reasoning within the child | 5,000-20,000 tokens |

When the child session completes, all of this is discarded. The parent receives
only the final text summary.

**Example:** An explorer agent reading 20 files might consume 40K tokens in its
own context. The parent pays ~400 tokens for the delegation round-trip. That's a
100x token efficiency gain.

### Context Inheritance Costs

The `context_depth` and `context_scope` parameters control how much parent context
the child inherits. This inherited context is formatted as plain text and injected
into the child's first user message (not via set_messages).

| Setting | Token cost in child |
|---------|-------------------|
| `context_depth="none"` | 0 -- clean slate |
| `context_depth="recent"` (default, 5 turns) | ~1,000-2,500 tokens (each message truncated to 2000 chars) |
| `context_depth="all"`, `context_scope="full"` | Potentially very large -- entire parent history |

The inherited context sits in the child's first user message, which is protected
from removal during compaction (user messages are only stubbed at Level 8). For
long parent histories with `context_depth="all"`, this can consume substantial
child context budget.

**Recommendation:** Default `context_depth="recent"` with `context_scope="conversation"`
is designed for the common case. Use `"all"` + `"full"` only when the child
genuinely needs the complete parent history (debugging, self-delegation).

### The Token Economics of Parallel Delegation

Dispatching multiple agents in parallel multiplies the token efficiency:

```
Direct approach (no agents):
  20 file reads = ~20K tokens permanently in parent context

Sequential delegation (3 agents):
  Agent A: reads 7 files, returns summary (~400 tokens in parent)
  Agent B: reads 7 files, returns summary (~400 tokens in parent)
  Agent C: reads 6 files, returns summary (~400 tokens in parent)
  Total parent cost: ~1,200 tokens (compactable)

Parallel delegation (same 3 agents):
  Same total cost, but all three run concurrently.
```

### Agent @mentions vs Root @mentions

Heavy documentation should live in agent definition files, not root bundle context:

| Approach | Cost model |
|----------|-----------|
| 10K tokens of docs as root @mention | 10K tokens x every turn of the session (permanent) |
| 10K tokens of docs as agent @mention | 10K tokens only when that agent is spawned (isolated) |

This is the "context sink" pattern: root sessions carry thin awareness pointers
("delegate to X for this topic"), while agents carry the heavy documentation in
their own @mentions.
