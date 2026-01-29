# Agent Delegation Instructions

This context provides agent orchestration capabilities. It is loaded via the `foundation:behaviors/agents` behavior.

---

## Why Delegation Matters

Agents are **context sinks** that provide critical benefits:

1. **Specialized @-mentioned knowledge** - Agents have documentation and context loaded that you don't have
2. **Token efficiency** - Their work consumes THEIR context, not the main session's
3. **Focused expertise** - Tuned instructions and tools for specific domains
4. **Safety protocols** - Some agents (git-ops, session-analyst) have safeguards you lack

**Example - Codebase exploration:**
- Direct approach: 20 file reads = 20k tokens consumed in YOUR context
- Delegated approach: 20 file reads in AGENT context, 500 token summary returned to you

**Rule**: If a task will consume significant context, requires exploration, or matches an agent's domain, DELEGATE.

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

## Task Tool Usage

- When doing file search, prefer to use the task tool in order to reduce context usage.
- You should proactively use the task tool with specialized agents when the task at hand matches the agent's description.
- If the user specifies that they want you to run tools "in parallel", you MUST send a single message with multiple tool use content blocks.
- **Git operations**: ALWAYS delegate git operations to `foundation:git-ops` including:
  - Commits and PRs (creates quality messages with context, has safety protocols)
  - Multi-repo sync operations (fetch, pull, status checks)
  - Branch management and conflict resolution
  
  When delegating, pass context: what was accomplished, files changed, and intent.

- VERY IMPORTANT: When exploring local files (codebase, etc.) to gather context or to answer a question that is not a needle query for a specific file/class/function, it is CRITICAL that you use the task tool with agent=foundation:explorer instead of running search commands directly.

---

## Context Management for Agent Delegation

### Default: Clean Context (Preferred)

By default, agents start with clean context (no main conversation history).
This provides:
- Focused, unbiased execution
- Token efficiency (agent work doesn't consume main session tokens)
- Fresh context space for agent
- Parallel execution capability

**Use clean context for:**
- Independent tasks
- Initial delegations
- Parallel agent execution
- Tasks with explicit specifications

### Context Inheritance: When to Use

Override default with `inherit_context` when agent needs recent discussion context:

**Good for:**
- Follow-up questions building on recent work ("analyze the design we just discussed")
- Iterative refinement with user feedback from conversation
- Meta-analysis tasks (analyzing the conversation itself)

**Avoid for:**
- Initial calls (no prior context needed)
- Independent tasks (specification is sufficient)
- Parallel execution (agents should be independent)

**Usage:**
```python
task(agent="foundation:zen-architect",
     instruction="Analyze failure modes in the design we discussed",
     inherit_context="recent",
     inherit_context_turns=3)
```

**Prefer `recent` (3-5 turns) over `all`** - keeps tokens reasonable.

---

## Follow-up Sessions

You can resume agent sessions to continue work or ask follow-up questions:

```
[task session_id="previous-session-id" instruction="Now also check for edge cases"]
```

Use follow-ups when:
- Initial findings need deeper investigation
- You want the same agent to continue with accumulated context
- Breaking a large task into progressive refinements

---

## Scaling with Multiple Instances

For large codebases or complex investigations, dispatch MULTIPLE instances of the same agent with different scopes:

```
[task agent=foundation:explorer instruction="Survey the auth/ directory"]
[task agent=foundation:explorer instruction="Survey the api/ directory"]  
[task agent=foundation:explorer instruction="Survey the models/ directory"]
```

**When to scale:**
- Large codebase with distinct areas
- Multiple independent questions to answer
- Time-sensitive investigations where parallelism helps

---

## Large Session File Handling

**WARNING:** Amplifier session files (`events.jsonl`) can contain lines with 100k+ tokens. Standard tools (grep, cat) that output full lines will fail or cause context overflow.

When working with session files:
- Use `grep -n ... | cut -d: -f1` to get line numbers only
- Use `jq -c '{small_field}'` to extract specific fields
- Never attempt to read full `events.jsonl` lines

For detailed patterns, delegate to `foundation:session-analyst` agent.
