# Recipes: Multi-Step Agent Workflows

## What It Is

A recipe is a declarative YAML specification that orchestrates multi-step AI agent
workflows. Recipes define sequences of agent invocations, bash commands, and
sub-recipe calls with automatic checkpointing, context accumulation, conditional
logic, looping, parallel execution, and approval gates for human-in-loop workflows.

## How It Works

### Recipe Structure

```yaml
name: design-review
description: Multi-perspective architecture review
version: 1.0.0
context:
  file_path: ""     # Initial variables
steps:
  - id: "analyze"
    agent: "foundation:zen-architect"
    prompt: "Analyze the architecture of {{file_path}}"
    output: "analysis"
  - id: "review"
    agent: "foundation:security-guardian"
    prompt: "Review for security: {{analysis}}"
    output: "security_review"
```

### Step Types

- **`agent`** (default): Spawns an LLM agent with a prompt
- **`recipe`**: Executes another recipe as a sub-workflow (isolated context)
- **`bash`**: Executes shell commands directly

### Context Accumulation

Each step's output is stored in a shared context dict. Subsequent steps reference
previous outputs via `{{variable}}` template syntax. Sub-recipes receive ONLY
explicitly-passed variables (context isolation).

### Staged Recipes with Approval Gates

```yaml
stages:
  - name: "design"
    steps: [...]
    approval:
      required: true
      prompt: "Review the design before proceeding to implementation?"
  - name: "implementation"
    steps: [...]
```

Execution pauses between stages. Users approve/deny via the recipes tool. Approval
messages become `{{_approval_message}}` in subsequent stages.

### Loops

**Foreach**: Iterate over lists with optional parallel execution.
```yaml
- id: "multi-review"
  foreach: "{{perspectives}}"
  as: "perspective"
  parallel: 3
  collect: "reviews"
  prompt: "Review from {{perspective}} perspective"
```

**While/convergence**: Loop until a condition is met.
```yaml
- id: "refine"
  while_condition: "{{quality_score}} < 8"
  max_while_iterations: 5
  update_context:
    quality_score: "{{result.score}}"
```

### Conditional Execution

```yaml
- id: "deep-review"
  condition: "{{severity}} == 'critical'"
  prompt: "Perform deep security analysis"
```

Expression language supports: ==, !=, <, >, <=, >=, and, or, not, parentheses.

### Recipe Composition

Recipes can call other recipes as sub-workflows with isolated context:
```yaml
- id: "security-audit"
  type: "recipe"
  recipe: "security-audit.yaml"
  context:
    target: "{{file_path}}"
```

Recursion protection: max_depth (default 5), max_total_steps (default 100).

### Resumability

Checkpoints are saved after every step. Sessions persist in
`~/.amplifier/projects/<project>/recipe-sessions/<id>/`. Resume with
`recipes operation=resume session_id=<id>`.

### Key Implementation Files

- `amplifier-bundle-recipes/modules/tool-recipes/models.py` -- Dataclasses (926 lines)
- `amplifier-bundle-recipes/modules/tool-recipes/executor.py` -- Execution engine (2,753 lines)
- `amplifier-bundle-recipes/modules/tool-recipes/expression_evaluator.py` -- Condition parser
- `amplifier-bundle-recipes/modules/tool-recipes/session.py` -- Persistence
- `amplifier-bundle-recipes/docs/RECIPE_SCHEMA.md` -- Complete schema documentation

## Relevance to System Design Bundle

Recipes are ideal for multi-step design processes that follow a predictable
structure with human checkpoints.

### Immediate Opportunities

- **Architecture review recipe**: A staged recipe that runs multiple analysis
  agents in sequence:
  1. Explorer agent surveys the codebase structure
  2. Zen-architect analyzes the architecture
  3. Security guardian reviews attack surface
  4. Synthesis agent combines perspectives
  5. APPROVAL GATE: Human reviews before generating report
  6. Writer agent produces design document

- **Design exploration recipe**: A foreach recipe that evaluates multiple design
  alternatives in parallel, each from a different perspective (SRE, security,
  developer experience, cost), then synthesizes into a tradeoff matrix.

- **Constraint discovery recipe**: A while-loop recipe that iteratively refines
  understanding of system constraints through conversation with agents examining
  code, documentation, and dependencies until convergence.

- **System-type classification recipe**: A flat recipe that analyzes a codebase
  to determine what kind of system it is, then loads appropriate system-type
  skills and generates an architectural overview.

### Recipes vs Other Mechanisms

Recipes shine when:
- The process has distinct phases with human checkpoints
- Multiple agents need to contribute in sequence
- The workflow is repeatable (worth encoding as YAML)
- Results need to be combined across agents
- Parallelism is valuable (multi-perspective analysis)

Recipes are overkill when:
- The interaction is exploratory and ad-hoc (use modes instead)
- It's a single-step analysis (use direct agent delegation)
- The process changes every time (use interactive modes)

The superpowers full-development-cycle recipe (440 lines, 4 stages, 3 approval
gates) is the most sophisticated example -- composing brainstorming, planning,
worktree setup, subagent-driven development, and finishing into end-to-end flow.
This serves as a strong template for a "system design lifecycle" recipe.

## Context Window Impact

Recipes have the strongest context isolation of any mechanism. Each step runs in
a completely independent child session, and only text output propagates between
steps.

### Per-Step Isolation

```
Step 1 -> spawn() -> child session A (fresh context) -> runs, returns output text
Step 2 -> spawn() -> child session B (fresh context) -> receives {{step1_output}} in prompt
Step 3 -> spawn() -> child session C (fresh context) -> receives {{step1_output}}, {{step2_output}}
```

Each child session has:
- Its own system prompt (from the agent's @mentions and instruction)
- Its own message history (starts empty)
- Its own compaction lifecycle (independent of parent and other steps)
- No shared conversation history with other steps

When a step completes, its entire context (file reads, tool results, reasoning
turns) is discarded. Only the final text output is captured as a string in the
recipe's context dict.

### What the Parent Session Pays

While a recipe executes, the parent session accumulates:
- One tool call message (the `recipes` tool invocation)
- One tool result message (the recipe's final output)

Individual step delegations happen inside the recipe executor, not in the parent's
message history. A 20-step recipe does NOT create 40 messages in the parent -- it
creates 2 (one call, one result).

### Context Accumulation via {{variables}}

The recipe context dict is a flat key-value map of strings. Each step's output is
stored as `{{step_id}}` (or the explicit `output:` variable name). Subsequent
steps receive previous outputs interpolated into their prompt text.

This means context accumulation is **textual, not conversational**. A step that
produces a 2,000-token analysis creates a 2,000-token string that gets embedded
verbatim in later step prompts. If multiple prior steps are referenced, the
interpolated prompt can grow large.

| Pattern | Token behavior |
|---------|---------------|
| Linear chain (each step references only the previous) | Constant-size prompts |
| Fan-in (later step references all previous) | Prompt grows with number of prior steps |
| Foreach with collect | Collected results all appear in the next step's prompt |

### Recipes vs Direct Multi-Agent Delegation

For the same multi-step workflow:

| Approach | Parent context cost | Isolation |
|----------|-------------------|-----------|
| Direct sequential delegation | ~400 tokens per agent x N agents, all in parent history | Agent results accumulate in parent |
| Recipe | ~400 tokens total (one recipe tool call/result) | Everything inside recipe is isolated |

Recipes are significantly more context-efficient for multi-step workflows because
the parent only sees the recipe as a single tool invocation. The individual step
delegations, their outputs, and all intermediate work happen outside the parent's
context window entirely.

### Sub-Recipe Context Isolation

Recipes calling sub-recipes (`type: "recipe"`) provide an additional isolation
boundary. Sub-recipes receive only explicitly-passed context variables -- not the
parent recipe's full context dict. This prevents context bleed between workflow
phases.
