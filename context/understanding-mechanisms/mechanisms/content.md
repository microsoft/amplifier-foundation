# Content (Context Files): Static Knowledge Injection

## What It Is

Context files are plain markdown documents that get injected verbatim into the
LLM's system prompt. They are the simplest and most direct way to shape agent
behavior -- essentially "instruction manuals" the LLM reads at the start of each
turn.

## How It Works

### Two Loading Mechanisms

| Mechanism | Syntax | Where Used | Composition |
|-----------|--------|-----------|-------------|
| `@mentions` | `@bundle:path/file.md` | Markdown body | Stays with this instruction |
| `context.include` | `bundle:path/file.md` | YAML behaviors | Accumulates across composition |

Critical distinction: `@mentions` are replaced when another bundle overrides the
instruction. `context.include` propagates through the composition chain.

### @Mention Loading Pipeline

1. **Parse**: Regex extracts `@namespace:path` from markdown (excluding code blocks)
2. **Resolve**: Maps namespace to bundle, resolves to filesystem path
3. **Recursive load**: Content is read and scanned for nested @mentions (depth 3)
4. **Deduplicate**: SHA-256 hash prevents same content appearing twice
5. **Wrap**: Content formatted as `<context_file paths="@bundle:path -> /abs/path">...</context_file>`
6. **Assemble**: Prepended before main instruction in system prompt

### Dynamic System Prompt Factory

The system prompt is NOT static. A factory function is called on every LLM turn,
re-reading @mentioned files fresh. Files changed on disk are picked up next turn.

### Static vs Dynamic Context

| Aspect | Static (context files) | Dynamic (message history) |
|--------|----------------------|--------------------------|
| What | Markdown files from bundles | User/assistant/tool messages |
| When loaded | Session start + re-read each turn | Accumulated during session |
| Managed by | System prompt factory | ContextManager module |
| Compaction | Not compacted (always full) | Auto-compacted at token budget |

### ContextManager Module (Message History)

Separate from static context files, the ContextManager handles conversation
message history. Key capability: `get_messages_for_request(budget)` returns
messages fitting the token budget, compacting internally if needed. Compaction is
non-destructive ephemeral windowing -- the full history is always preserved.

### Context Organization Patterns

Foundation organizes context by audience:
- `shared/` -- core instructions for all sessions
- `agents/` -- context for agent-related behaviors
- `amplifier-dev/` -- development-specific (only loaded when that behavior is composed)

Superpowers organizes by concern:
- `philosophy.md` -- 7 core principles
- `instructions.md` -- standing orders + mode recommendation
- `tdd-depth.md` -- 474-line TDD reference
- `debugging-techniques.md` -- root-cause tracing patterns

### The "Context Sink" Pattern

Root sessions stay thin (only awareness pointers). Agent sessions carry heavy
documentation via @mentions. When an agent spawns, its @mentioned docs load in
its context -- not the parent's. This preserves the parent's context window.

### Key Implementation Files

- `amplifier-foundation/mentions/parser.py` -- @mention extraction
- `amplifier-foundation/mentions/resolver.py` -- Namespace resolution
- `amplifier-foundation/mentions/loader.py` -- Recursive loading + dedup
- `amplifier-foundation/bundle/_prepared.py:243-297` -- System prompt factory
- `amplifier-core/python/amplifier_core/interfaces.py:166-212` -- ContextManager protocol

## Relevance to System Design Bundle

Context files are the **foundation layer** of our system design bundle. They
establish the persistent knowledge and behavioral framework the agent always has.

### Immediate Opportunities

- **System design principles**: A core context file establishing the principles,
  patterns, and practices for system design. Not Amplifier-specific, but generic
  systems design wisdom: model the system before solving, force multiscale reasoning,
  tradeoff analysis over best-practice mimicry, causal reasoning, architectural
  primitives.

- **Structured response template**: A context file that establishes the default
  template for design work: problem framing, assumptions, boundaries, components,
  flows, risks, tradeoffs, design, migration, metrics. This ensures the agent
  doesn't produce shallow output.

- **Simplicity principles**: From the agentic-system-design doc -- structural
  simplicity, simplicity under change, simplicity as constraint. Including the
  compact simplicity rubric.

- **System type guides**: Context files for specific system types (web service,
  distributed system, etc.) that provide domain-specific patterns and pitfalls.
  These could be loaded conditionally based on what type of system is being designed.

### Context vs Skills

For our bundle, the key decision is which knowledge goes in always-present context
files vs on-demand skills:

| Put in Context (Always Present) | Put in Skills (On Demand) |
|--------------------------------|--------------------------|
| Core design principles | System-type specific patterns |
| Structured response templates | Tradeoff analysis methodology |
| Simplicity rubrics | Adversarial review perspectives |
| Standing behavioral orders | Architecture primitive catalog |

The principle: context files for things the agent should ALWAYS consider, skills
for specialized knowledge loaded when relevant. Context files cost tokens every
turn, so they should be concise and high-value.

### Token Budget Considerations

Context files consume tokens on every turn. The foundation bundle already loads
substantial context. Our system design content needs to fit within reasonable
budgets. Options:
- Keep core principles concise (~2K tokens)
- Use skills for detailed methodology (loaded on demand)
- Use the context sink pattern (heavy docs in agent sessions only)
- Use `context.include` in behaviors so only composed content loads

## Context Window Impact

Content files sit in the **system prompt layer** of the context window -- the one
layer that is completely immune to compaction. This makes them the most expensive
mechanism per token over the life of a session.

### The Cost Model

The system prompt factory re-assembles the full system prompt on **every LLM call**.
It re-reads all @mentioned files from disk, deduplicates by SHA-256 hash, and
wraps each in `<context_file>` XML blocks prepended to the instruction body. This
entire payload becomes the system message.

During compaction, system messages are **extracted before compaction runs and
unconditionally re-prepended after**. They are never truncated, never removed,
never windowed. The compaction budget operates only on the non-system portion of
the context.

```
Budget available for conversation = context_window - system_prompt_size - reserved_space
```

Every token in your @mentioned files directly reduces the budget available for
conversation history and tool results.

### @mentions vs context.include

Both mechanisms have identical token cost -- they both resolve to content in the
system prompt. The difference is compositional:

| Mechanism | Composition behavior | Token implication |
|-----------|---------------------|-------------------|
| `@mentions` in instruction body | Replaced if another bundle overrides the instruction | Tokens go away if instruction is overridden |
| `context.include` in behavior YAML | Accumulates across all composed behaviors | Tokens add up across every behavior in the chain |

`context.include` is particularly dangerous for token budgets because it
**accumulates silently**. A bundle that composes three behaviors each adding 2K
tokens of context has a 6K token permanent floor before any conversation starts,
and that cost is invisible unless you audit the composition chain.

### Quantifying the Cost

For reference, the foundation bundle's base context (common-system-base.md,
delegation instructions, multi-agent patterns, etc.) already consumes a
significant token floor. Every bundle that adds content files on top of foundation
increases this permanent overhead.

| Content size | Per-turn cost | Over 50-turn session |
|-------------|---------------|---------------------|
| 500 words (~375 tokens) | 375 tokens | 18,750 tokens |
| 2,000 words (~1,500 tokens) | 1,500 tokens | 75,000 tokens |
| 5,000 words (~3,750 tokens) | 3,750 tokens | 187,500 tokens |

These costs are **multiplied across every LLM call**, including calls made by
tool-using loops (which may make 5-10 LLM calls per user turn).

### The Context Sink Alternative

The "context sink" pattern exists specifically to address this cost. Instead of
loading heavy documentation in the root session's system prompt, the docs are
@mentioned in **agent definition files**. When an agent spawns, those @mentions
load in the **child's** context -- not the parent's.

Root session context file (thin pointer):
```markdown
For bundle composition details, delegate to `foundation:foundation-expert`.
```

Agent definition file (heavy docs):
```markdown
@foundation:docs/BUNDLE_GUIDE.md
@foundation:docs/URI_FORMATS.md
@core:docs/contracts/TOOL_CONTRACT.md
```

The parent pays ~100 tokens for the pointer. The child pays ~10K tokens for the
full docs -- but only when spawned, and only in its own context window.

### Design Guidelines

1. **Budget per byte.** Every token in a content file is paid on every turn. Treat
   content files like permanent memory allocation.
2. **Concise principles over detailed reference.** Content files should contain
   high-signal behavioral guidance (~1-2K tokens). Move detailed reference
   material to skills (on-demand) or agent @mentions (child context).
3. **Audit composition chains.** When composing behaviors, tally the total
   `context.include` footprint. Silent accumulation is the most common cause of
   unexpectedly large system prompts.
4. **Use the context sink pattern for heavy docs.** If a document exceeds ~1K
   tokens and is only needed for specific tasks, put it in an agent definition
   file rather than root session context.
