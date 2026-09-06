# Agent Delegation — Depth Reference

The awareness half of this material lives in `foundation:context/agents/delegation-core.md`,
which the `foundation:behaviors/agents` behavior loads into every root session. This file is
the DEPTH half: it is NOT loaded by the behavior. It is @-mentioned from the body of the
agents whose work actually requires it — the ones that delegate onward or resume sessions.

Read it if you are about to: resume an agent session, read a structured agent return,
plan a wave of concurrent delegations, or touch a session file.

---

## The Context Sink Pattern

**Agents are context sinks** - they absorb the token cost of exploration and return only distilled insights.

### How It Works

```
┌─────────────────────────────────────────────────────────────┐
│  Root Session (YOUR context)                                │
│  - Orchestration decisions                                  │
│  - User interaction                                         │
│  - ~500 token summaries from agents                         │
└────────────────────────┬────────────────────────────────────┘
                         │ delegate()
                         ▼
┌─────────────────────────────────────────────────────────────┐
│  Agent Session (AGENT's context)                            │
│  - Heavy @-mentioned documentation                          │
│  - 20+ file reads (~20k tokens)                             │
│  - Specialized tools and analysis                           │
│  - Returns: concise summary to parent                       │
└─────────────────────────────────────────────────────────────┘
```

### Why This Matters

| Without Context Sink | With Context Sink |
|---------------------|-------------------|
| 20 file reads = 20k tokens in YOUR context | 20 file reads in AGENT context |
| Session fills quickly | Session stays lean |
| Can't run long tasks | Can orchestrate for hours |
| Loses history to compaction | Preserves important history |

### Applying the Pattern

1. **Expert agents carry heavy docs** - @-mentioned documentation loads in THEIR context
2. **Root sessions get thin pointers** - "This capability exists, delegate to X"
3. **Zero partial knowledge** - If capability isn't composed, zero context about it
4. **Summaries travel, raw data stays** - Agents return insights, not raw data

### Relaying Results to the User

The user does not see full tool results — they see only a brief, truncated preview. Intermediate text you write before tool calls may also not be prominently visible. Therefore:

- **Always relay key findings** in your final response text
- **Write as though your response text is the user's only reliable view** — tool output and intermediate narration reach them only as truncated previews, if at all
- **When agents return results**, summarize the important parts in your own words as part of your response to the user
- **Err on the side of over-communicating** — repeating a finding is better than the user missing it entirely

This is not about verbosity. It's about ensuring the user receives the information they need without having to ask you to repeat yourself.

### Scrutinize an Agent's "N/A" or "Couldn't Do It"

Agents follow the **Honest Stopping** rule: when they can't satisfy a required item, they surface it as `N/A — <reason>` or stop and report back rather than fabricate. That honesty only helps if **you** treat those determinations as checkpoints, not conclusions:

- **When a returned `N/A` or "blocked" doesn't smell right** — the item looks like it *should* apply, or the reason is thin — treat it as unresolved until verified. The agent may be missing context you have, or the requirement may be real and non-negotiable.
- **Re-engage rather than proceed.** Resume the same agent session with the missing context ("X actually does apply because...; please satisfy it, or tell me precisely what's blocking"), or escalate the unmet requirement to the user for a decision.
- **A self-granted N/A is not a waiver.** Only you — or the user — can waive a requirement, and a waiver should be explicit (and recorded where the repo expects it), not inferred from an agent's convenience. If the requirement stands and can't be met, that's a blocker to surface, not a box to quietly accept as checked.

The agent's job is honest reporting; your job is to examine every "N/A" before it can pass for a satisfied requirement.

### Reading a Structured Agent Return

When the `return_contract` feature is enabled (see `tool-delegate`'s config), a delegate
result may carry a `contract` field alongside `response`: `contract.findings` (each a
`claim` + `evidence` + `confidence`), `contract.not_covered`, and `contract.artifacts`.
This is the same doctrine above, given a machine-readable field instead of leaving it to
be excavated from prose:

- **Walk every entry in `contract.findings`** before you write your answer. A finding you
  do not carry forward into your response text is one you have decided to discard —
  decide that on purpose, not by running out of attention.
- **`contract.not_covered` is the machine-readable form of the "N/A" doctrine above** —
  it is the agent telling you, in a field instead of a sentence you might skim, exactly
  what it did not examine. Read it the moment the result arrives: resuming that session
  *now* is cheap; discovering the gap three turns later is not.
- **`contract.conformant: false` means the agent returned unstructured prose.** Its
  coverage is unknown — do not treat its silence on any topic as evidence it looked and
  found nothing. Fall back to reading `response` the way you always have.

---

## Agent Domain Honoring

**CRITICAL**: When an agent description states it MUST, REQUIRED, or ALWAYS be used for a specific domain, you MUST delegate to that agent rather than attempting the task directly.

Agent domain claims are authoritative. The agent descriptions contain expertise you do not have access to otherwise. Examples:

| Agent Claim | Your Response |
|-------------|---------------|
| "REQUIRED for events.jsonl" | ALWAYS use session-analyst for session files |
| "MUST BE USED when errors" | ALWAYS use bug-hunter for debugging |
| "Implementation-only with complete specs" | Use modular-builder ONLY when specifications complete; use zen-architect first for design |
| "ALWAYS delegate git operations" | ALWAYS use git-ops for commits/PRs |

**Why this matters**: Agents that claim domains often have @-mentioned context, specialized tools, or safety protocols that the root session lacks. When you skip delegation, you lose that expertise.

**Anti-pattern**: Attempting a task yourself when an agent explicitly claims that domain.
**Correct pattern**: Immediately delegate to the claiming agent with full context.

---

## Delegate Tool Usage

The `delegate` tool spawns specialized agents for autonomous task handling.

### Special Agent Values

- `agent="self"` - Spawn yourself as a sub-agent (maximum token conservation)
- `agent="namespace:path/to/bundle"` - Delegate to any bundle directly

### Context Control (Two Independent Parameters)

The delegate tool provides fine-grained control over context inheritance:

**Parameter 1: `context_depth`** - HOW MUCH context to inherit

| Value | Behavior |
|-------|----------|
| `"none"` | Clean slate - agent starts fresh (use for independent tasks) |
| `"recent"` | Last N turns (default, controlled by `context_turns`) |
| `"all"` | Full conversation history |

**Parameter 2: `context_scope`** - WHICH content to include

| Value | What's Included |
|-------|-----------------|
| `"conversation"` | User/assistant text only (default, safest) |
| `"agents"` | + results from delegate tool calls (for multi-agent collaboration) |
| `"full"` | + ALL tool results (complete context mirror) |

### Context Usage Examples

```python
# Default: Recent conversation text (most common)
delegate(agent="foundation:explorer", instruction="...")

# Independent task - fresh perspective
delegate(agent="foundation:zen-architect", instruction="Review design",
         context_depth="none")

# Multi-agent collaboration - agent B sees agent A's output
delegate(agent="foundation:architect", instruction="Design based on findings",
         context_scope="agents")

# Self-delegation with full context (recommended for "self")
delegate(agent="self", instruction="Continue this analysis",
         context_depth="all", context_scope="full")

# Debugging - bug-hunter needs to see everything
delegate(agent="foundation:bug-hunter", instruction="Why did this fail?",
         context_depth="all", context_scope="full")
```

### Delegation Quality: Semantic Context

Agents that produce artifacts — commit messages, PR descriptions, design docs, bug reports — need to know **WHY**, not just WHAT. A fast model given only `"commit the changes"` produces generic output; one given a semantic summary of what was accomplished produces something meaningful.

**Always include in your instruction:**
- What was accomplished (semantic summary, not just file names or vague directives)
- Why it was done (fix, feature, refactor — the motivation)
- What specific output is needed (commit + push, create PR, write design doc, etc.)

Complement with `context_depth`/`context_scope` to reinforce the instruction via conversation history:

| Situation | Recommendation |
|-----------|----------------|
| Work just completed | `context_depth="recent"` — recent turns hold the story |
| Complex multi-step work | `context_depth="all"`, `context_scope="agents"` — full arc needed |
| Independent task | `context_depth="none"` — clean slate, no prior context |

**Explicit summary in instruction + matching context parameters** is better than either alone. Check each agent's description for its preferred parameters when it has specific requirements.

---

## Session Resumption

Delegate returns `session_id` for multi-turn engagement:

```python
# Initial delegation
result = delegate(agent="foundation:explorer", instruction="Survey codebase")
# result.session_id = "abc123-def456-..._foundation:explorer"

# Resume with full session_id
delegate(session_id=result.session_id, instruction="Now also check the tests")
```

Use session resumption when:
- Initial findings need deeper investigation
- You want the agent to continue with accumulated context
- Breaking a large task into progressive refinements

---

## Wave Discipline: Batch Everything Independent

**Every delegate call in one turn runs concurrently. Every delegate call in a separate turn
runs sequentially, and you block on the previous one first.**

Plan your full delegation set before dispatching any of it. Emit every independently
resolvable delegation in a single turn — different agents, same agent with different
scopes, or both.

```python
# One turn, three concurrent agents - different specialists
delegate(agent="foundation:explorer", instruction="Survey auth/", context_depth="none")
delegate(agent="python-dev:code-intel", instruction="Trace authenticate() callers")
delegate(agent="foundation:git-ops", instruction="Summarize recent auth/ commits")

# One turn, three concurrent instances - same agent, split scope
delegate(agent="foundation:explorer", instruction="Survey auth/", context_depth="none")
delegate(agent="foundation:explorer", instruction="Survey api/", context_depth="none")
delegate(agent="foundation:explorer", instruction="Survey models/", context_depth="none")
```

**Only a delegation that consumes another delegation's output belongs in a later turn.**
Everything else goes now. Before you dispatch a second wave, ask whether it could have
gone out with the first — if it could have, batch what remains rather than repeating the
mistake.

**When to scale out:** large codebase with distinct areas; multiple independent questions;
any investigation where you would otherwise wait on one agent before starting the next.

---

## Large Session File Handling

**WARNING:** Amplifier session files (`events.jsonl`) can contain lines with 100k+ tokens. Standard tools (grep, cat) that output full lines will fail or cause context overflow.

When working with session files:
- Use `grep -n ... | cut -d: -f1` to get line numbers only
- Use `jq -c '{small_field}'` to extract specific fields
- Never attempt to read full `events.jsonl` lines

For detailed patterns, delegate to `foundation:session-analyst` agent.

---

## Final Reminder: Default to Delegation

If you've read this far and are about to use a tool directly, ask yourself:

1. **Is there an agent for this?** → If yes, DELEGATE.
2. **Will this consume significant context?** → If yes, DELEGATE.
3. **Is this truly trivial (1 file, 1 command)?** → Only then, proceed directly.

**Your context window is precious. Protect it by delegating.**
