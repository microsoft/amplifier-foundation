# Multi-Agent Patterns

This context provides patterns for orchestrating multiple agents effectively.

---

## Parallel Agent Dispatch

**CRITICAL**: For non-trivial investigations or tasks, use MULTIPLE agents to get richer results. Different agents have different tools, perspectives, and context that complement each other.

When investigating or analyzing, dispatch multiple agents IN PARALLEL in a single message:

```python
delegate(agent="foundation:explorer", instruction="Survey the authentication module structure")
delegate(agent="lsp-python:python-code-intel", instruction="Trace the call hierarchy of authenticate()")
delegate(agent="foundation:zen-architect", instruction="Review auth module for design patterns")
```

**Why parallel matters:**
- Each agent brings different tools (LSP vs grep vs design analysis)
- Deterministic tools (LSP) find actual code paths; text search finds references and docs
- TOGETHER they reveal: actual behavior + dead code + documentation gaps + design issues

---

## Complementary Agent Combinations

| Task Type | Agent Combination | Why |
|-----------|-------------------|-----|
| **Code investigation** | `python-code-intel` + `explorer` + `zen-architect` | LSP traces actual code; explorer finds related files; architect assesses design |
| **Bug debugging** | `bug-hunter` + `python-code-intel` | Hypothesis-driven debugging + precise call tracing |
| **Implementation** | `zen-architect` → `modular-builder` → `zen-architect` | Design → implement → review cycle |
| **Security review** | `security-guardian` + `explorer` + `python-code-intel` | Security patterns + codebase survey + actual data flow |

---

## Multi-Agent Collaboration with Context Sharing

The `context_scope="agents"` parameter enables agents to see each other's work:

```python
# Agent A works independently
result_a = delegate(agent="foundation:explorer", instruction="Find all authentication issues",
                    context_depth="none")

# Agent B sees Agent A's output via context_scope="agents"
result_b = delegate(agent="foundation:zen-architect", 
                    instruction="Design fixes for the issues found",
                    context_scope="agents")  # Can see result_a!

# Agent C sees both A and B
result_c = delegate(agent="foundation:modular-builder",
                    instruction="Implement the designed fixes",
                    context_scope="agents")  # Sees both previous agent results
```

### Context Scope for Collaboration

| Scope | Agents See | Best For |
|-------|------------|----------|
| `"conversation"` | User/assistant text only | Independent work |
| `"agents"` | + delegate tool results | Multi-agent collaboration |
| `"full"` | + ALL tool results | Complete context sharing |

---

## Session Resumption for Iterative Work

Delegate returns `session_id` for multi-turn engagement:

```python
# Round 1 - Initial analysis
analyst = delegate(agent="foundation:analyst", instruction="Analyze the problem")
critic = delegate(agent="foundation:critic", 
                  instruction=f"Critique: {analyst.response}",
                  context_scope="agents")

# Round 2 - Resume sessions using full session_id
analyst2 = delegate(session_id=analyst.session_id, 
                    instruction=f"Address feedback: {critic.response}")
critic2 = delegate(session_id=critic.session_id,
                   instruction="Review the revision")

# Continue rounds until convergence
while not is_converged(analyst_result, critic_result):
    analyst_result = delegate(session_id=analyst.session_id, ...)
    critic_result = delegate(session_id=critic.session_id, ...)
```

---

## Task Decomposition for Implementation Work

**Before delegating to modular-builder, ensure specifications are complete.**

### Task Complexity Assessment

| Task Type | Decomposition Strategy |
|-----------|------------------------|
| "Implement X from spec in [file]" | Direct to modular-builder (spec exists) |
| "Add feature Y" (no spec) | Two-phase: zen-architect (design) → modular-builder (implement) |
| "Improve performance" | Three-phase: zen-architect (analyze) → zen-architect (design) → modular-builder (implement) |
| "Refactor Z" | Two-phase: zen-architect (plan refactor) → modular-builder (execute) |

### Required Specification Elements

modular-builder requires these inputs:
- **File paths**: Exact locations
- **Interfaces**: Complete signatures with types
- **Pattern**: Reference example or design freedom
- **Success criteria**: Measurable outcomes

**Missing any of these? Use zen-architect first to create specifications.**

### The Design-First Pattern

For under-specified tasks, use this workflow:

```
1. zen-architect (ANALYZE mode)
   ↓ Produces: Problem analysis and design options
   
2. zen-architect (ARCHITECT mode)  
   ↓ Produces: Complete specification with all required elements
   
3. modular-builder
   ↓ Produces: Implementation matching specification
   
4. zen-architect (REVIEW mode)
   ↓ Produces: Quality assessment and recommendations
```

### Anti-Patterns

❌ Delegating "add authentication" to modular-builder
   → Missing: where, how, what pattern, what interface?
   → Fix: zen-architect designs auth approach first

❌ Delegating "improve code quality" to modular-builder
   → This is analysis/review work, not implementation
   → Fix: zen-architect reviews and creates refactor spec

❌ Delegating complex features without specs to modular-builder
   → Will cause research loops and paralysis
   → Fix: zen-architect creates complete specification first

### Good Delegation Examples

✅ "Use zen-architect to design caching layer, then modular-builder to implement per spec"

✅ "For the refactoring, use zen-architect to plan changes, then modular-builder to execute"

✅ "Use modular-builder to add the `validate_email()` method to `validators.py` following the pattern of `validate_username()`"
   → Note: Last example has enough detail to skip zen-architect

---

## Creative Patterns

### Agent Chain with Accumulated Knowledge

Each agent sees all previous agents' work:

```python
delegate(agent="foundation:explorer", instruction="Survey", context_depth="none")
delegate(agent="foundation:analyst", instruction="Analyze", context_scope="agents")
delegate(agent="foundation:architect", instruction="Design", context_scope="agents")
delegate(agent="foundation:builder", instruction="Implement", context_scope="agents")
```

### Parallel Investigation with Synthesis

```python
# Parallel - independent work (context_depth="none")
r1 = delegate(agent="foundation:explorer", instruction="Check frontend", context_depth="none")
r2 = delegate(agent="foundation:explorer", instruction="Check backend", context_depth="none")
r3 = delegate(agent="foundation:explorer", instruction="Check database", context_depth="none")

# Synthesizer sees all their results
delegate(agent="foundation:architect", 
         instruction=f"Synthesize: {r1.response}, {r2.response}, {r3.response}",
         context_scope="agents")
```

### Self-Delegation for Token Management

When your session is getting full, spawn yourself to continue:

```python
delegate(agent="self", instruction="Continue the analysis in depth",
         context_depth="all", context_scope="full")
# Returns summary, main session stays lean
```

**Recommended for self-delegation:** `context_depth="all", context_scope="full"` - the sub-instance should see everything to avoid re-doing work.
