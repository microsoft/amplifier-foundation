# Modes: Runtime Behavior Overlays

## What It Is

A mode is a runtime behavior overlay that modifies how an agent operates without
changing the underlying bundle configuration. Modes are markdown files with YAML
frontmatter that define tool policies (what tools are allowed/blocked) and inject
guidance into the agent's system prompt. The user activates modes via `/mode name`
or the agent can activate them programmatically.

## How It Works

### Mode File Structure

```yaml
---
mode:
  name: brainstorm
  description: Design refinement through structured conversation
  shortcut: brainstorm
  tools:
    safe: [read_file, glob, grep, delegate, recipes, load_skill]
    warn: [bash]
  default_action: block
  allowed_transitions: [write-plan, debug]
  allow_clear: false
---

# Brainstorm Mode

Guidance text injected as system-reminder when mode is active.
Includes behavioral rules, process phases, anti-rationalization tables.
```

### Tool Policy Types

| Policy | Behavior |
|--------|----------|
| `safe` | Tool works normally |
| `warn` | First call blocked with warning; retry proceeds |
| `confirm` | Requires user approval before execution |
| `block` | Tool disabled entirely |
| `default_action` | Applied to unlisted tools (default: block) |

Infrastructure tools (`mode`, `todo`) always bypass the cascade.

### Two-Hook Architecture

1. **Context injection** (`provider:request`, priority 10): Wraps mode body in
   `<system-reminder source="mode-name">` and injects as ephemeral system context
   before every LLM call. Resolves @mentions in mode content.

2. **Tool moderation** (`tool:pre`, priority -20): Evaluates each tool call
   against the policy cascade: infrastructure -> safe -> block -> confirm -> warn -> default.

### Mode Discovery

Modes are found in precedence order (first match wins):
1. `<project>/.amplifier/modes/` (project-specific)
2. `~/.amplifier/modes/` (user-global)
3. Bundle's own `modes/` directory
4. Config `search_paths` entries
5. Composed bundle `modes/` dirs (lazy discovery)

### Gate Policy (Transition Control)

The `tool-mode` has a configurable gate policy for agent-initiated transitions:
- `auto` -- agent changes modes freely
- `warn` (default) -- first transition attempt is denied, retry confirms
- `confirm` -- always denied, user must use /mode command

Modes can also constrain transitions via `allowed_transitions` and `allow_clear`.

### Built-in Modes (modes bundle)

- `explore` -- read-only, zero-footprint codebase exploration
- `plan` -- analyze and strategize, no writes
- `careful` -- full capability with user confirmation for writes

### Superpowers Modes (6-mode workflow)

```
/brainstorm -> /write-plan -> /execute-plan -> /verify -> /finish
                                   |
                              /debug (off-ramp)
```

Each mode blocks write tools and enforces a specific development phase:
- `/brainstorm` -- design conversation, delegates artifacts to brainstormer agent
- `/write-plan` -- plan discussion, delegates to plan-writer agent
- `/execute-plan` -- orchestration, delegates to implementer/reviewer pipeline
- `/debug` -- investigation, delegates fixes to bug-hunter/implementer
- `/verify` -- evidence collection with fresh test runs
- `/finish` -- branch completion (merge/PR/keep/discard)

### Key Implementation Files

- `amplifier-bundle-modes/modules/hooks-mode/__init__.py` -- ModeHooks (661 lines)
- `amplifier-bundle-modes/modules/tool-mode/__init__.py` -- ModeTool (377 lines)
- `amplifier-bundle-superpowers/modes/` -- 6 superpowers mode definitions
- `amplifier-bundle-modes/modes/` -- 3 built-in mode definitions

## Relevance to System Design Bundle

Modes are perhaps the **most directly applicable** mechanism from superpowers.
The brainstorm mode already demonstrates how to structure a design conversation,
and we can build system-design-specific modes that follow the same pattern.

### Immediate Opportunities

- **`/design` mode**: A mode for structured system design exploration. Like
  brainstorm, it would block write tools and enforce a phased process:
  1. Understand the system context (read code, docs)
  2. Identify constraints and requirements
  3. Explore design alternatives (at least 3)
  4. Evaluate tradeoffs systematically
  5. Present design section-by-section with validation
  6. Delegate design document creation to a specialist agent

- **`/systems-design-review` mode**: A mode for evaluating existing designs or proposed
  changes against the codebase. Block writes, allow reads and analysis tools.
  Enforce multi-perspective review (SRE, security, DX, cost, ops).

- **`/constraint-analysis` mode**: A mode focused specifically on discovering,
  documenting, and evaluating design constraints. Block writes, allow reads.
  Enforce systematic constraint identification before solution exploration.

## Context Window Impact

Modes affect the context window through **ephemeral injection** -- the mode's
full markdown body is injected on every LLM call while the mode is active, but
never stored in message history.

### How Mode Content Is Injected

The `hooks-mode` module fires on `provider:request` (priority 10). When a mode is
active, it:

1. Re-reads the mode file from disk (fresh every turn)
2. Resolves any `@namespace:path` mentions in the mode body (expanding them inline)
3. Wraps everything in `<system-reminder source="mode-{name}">` tags
4. Returns as `inject_context` with `ephemeral=True`

The orchestrator appends this to the message list **after** compaction runs. The
content is never passed to `context.add_message()` -- it evaporates after each
LLM call and is re-created fresh on the next.

### Token Cost Model

| Factor | Detail |
|--------|--------|
| Per-turn cost | Full size of mode markdown file + any expanded @mentions |
| Storage | None -- never enters message history |
| Compactable | No -- ephemeral injections bypass compaction |
| Accumulation | Never compounds -- re-created fresh each turn |
| Deactivation | Cost stops immediately when mode is cleared |

**Concrete example:** A mode with 1,000 words of guidance (~750 tokens) costs 750
tokens per LLM call. On a turn where the agent makes 5 tool calls (6 LLM calls
total), the mode costs ~4,500 tokens for that turn. Over a 30-turn session with
an average of 3 LLM calls per turn, the total mode cost is ~67,500 tokens.

### @Mentions in Mode Bodies

Mode files can contain `@namespace:path` references. These are resolved inline by
the mode hook -- the referenced file's full text replaces the mention. This means
a mode that @mentions a 2,000-token context file costs 2,000+ tokens per turn on
top of the mode's own content.

**Design implication:** Keep mode bodies concise. Move detailed methodology to a
companion skill that the mode's guidance tells the agent to load once on
activation, rather than @mentioning it in the mode body (which would inject it
every turn).

### Modes vs Content Files for Behavioral Guidance

| Aspect | Mode | Content file |
|--------|------|-------------|
| When active | Only while mode is toggled on | Always (every turn of session) |
| Token cost per turn | Same (both immune to compaction) | Same |
| Total session cost | Lower (only active portion of session) | Higher (entire session) |
| Can be deactivated | Yes -- cost stops immediately | No -- permanent |

Modes are more token-efficient than content files for guidance that's only needed
during specific phases of work. The superpowers workflow demonstrates this:
`/brainstorm` mode guidance is only paid for during the design conversation, not
during implementation.

### The Hybrid Pattern

Superpowers established the "hybrid pattern": the main agent handles the
interactive conversation (questions, exploration, tradeoff discussion), while
specialist agents handle artifact creation (writing the design doc). This works
because:
- The conversation needs full session context (mode does this)
- The artifact needs write tools (agents have them, mode blocks them from root)
- Neither can do the other's job well

This pattern maps perfectly to system design work: the design conversation is
interactive and context-heavy, but the final design document should be written
by a dedicated agent with a structured template.

### Anti-Rationalization Tables

Each superpowers mode includes an anti-rationalization table -- specific rebuttals
for common shortcuts. We should create similar tables for system design:
- "This is too simple for a design doc" -> Simple systems become complex. Document now.
- "I already know the right architecture" -> Assumptions kill designs. Explore alternatives.
- "We don't have time for a full design" -> Fixing a bad design costs 10x more.
- "The requirements aren't clear enough" -> Design reveals requirements. Start modeling.
