---
name: amplifier-recipes
description: "Use when authoring or running a recipe YAML, reading existing recipes, or deciding whether a workflow should be a recipe. Covers the recipe schema, staged recipes with approval gates, and the recipes tool surface."
---

> *Loaded on demand. The full guidance below is not in your default context --
> you reached for this skill because the situation called for it. Treat the
> sections that follow as authoritative for the duration of this task.*


You have access to the **recipes** tool for multi-step AI agent orchestration.

## When to Use Recipes

**Use recipes when:**
- Tasks have multiple sequential steps requiring different agents
- The workflow will be repeated (worth encoding as reusable YAML)
- Human approval checkpoints are needed between phases
- Work might be interrupted and needs resumption

**Use direct agent delegation when:**
- Tasks are single-step or simple
- Real-time interactive iteration is needed
- The workflow is exploratory or ad-hoc

## Recipe Authoring Lifecycle (REQUIRED)

All recipe work MUST follow this lifecycle. Do NOT write recipe YAML directly.

| Phase | Agent | Purpose |
|-------|-------|---------|
| 1. Author | `recipes:recipe-author` | Create, edit, validate, debug recipes |
| 2. Validate | `recipes:result-validator` | Verify recipe meets user's original intent |

**Anti-pattern**: Writing recipe YAML directly. Always delegate to `recipes:recipe-author`.

## Tool Operations

| Operation | Purpose |
|-----------|---------|
| `execute` | Run a recipe from YAML file |
| `resume` | Continue an interrupted session |
| `validate` | Check recipe YAML before execution |
| `list` | Show active sessions |
| `approvals` | Show pending approval gates |
| `approve/deny` | Respond to approval gates |

For detailed usage, schema knowledge, and best practices, delegate to `recipes:recipe-author`.

## While Loops and Enhanced Expressions

Recipes support convergence-based iteration:

- **`while_condition`** — loop until a condition becomes false
- **`break_when`** — exit loop early when a condition becomes true
- **`update_context`** — mutate context variables after each iteration
- **Expression operators** — `<`, `>`, `>=`, `<=`, `not`, parentheses, numeric comparison

For details, delegate to `recipes:recipe-author`.


You have access to the **recipes** tool for multi-step AI agent orchestration.

## Recipe Authoring Lifecycle (REQUIRED)

All recipe work MUST follow this lifecycle. Do NOT write recipe YAML directly.

| Phase | Agent | Purpose |
|-------|-------|---------|
| 1. Author   | `recipes:recipe-author`    | Create, edit, validate, debug recipes |
| 2. Validate | `recipes:result-validator` | Verify recipe meets user's original intent |
| 3. Document | `recipes:generate-recipe-docs` recipe | Generate co-located DOT+PNG flowcharts |

### Why This Matters

- `recipe-author` ensures **technical correctness** (valid YAML, proper schema, best practices)
- `result-validator` ensures **intent alignment** (solves what the user actually asked for)
- Skipping either step results in recipes that are syntactically valid but may not solve the right problem

### The Workflow

1. **User describes intent** → Delegate to `recipes:recipe-author`
2. **recipe-author creates/edits** → Provides recipe + intent summary for validation
3. **Delegate to `recipes:result-validator`** → Pass the recipe AND conversation context
4. **On PASS** → Present to user
5. **On FAIL** → Return to recipe-author with feedback, iterate

**Anti-pattern**: Writing recipe YAML directly, even if you know the schema. You lose clarifying questions, context accumulation, and validation against intent.

---

## How to Run Recipes

### Conversational (Recommended)

Within an Amplifier session, just ask naturally:

```
"Run the feature-announcement recipe"
"Execute the code-review recipe on src/auth.py"
"Run repo-activity-analysis for the last week"
```

Amplifier will invoke the recipes tool with the appropriate parameters.

### CLI (Direct Tool Invocation)

From the command line, use `amplifier tool invoke`:

```bash
# Basic execution
amplifier tool invoke recipes operation=execute recipe_path=recipes:examples/code-review-recipe.yaml

# With context variables
amplifier tool invoke recipes \
  operation=execute \
  recipe_path=recipes:examples/feature-announcement.yaml \
  context='{"user_description": "Added new caching layer"}'

# List active recipe sessions
amplifier tool invoke recipes operation=list

# Resume an interrupted recipe
amplifier tool invoke recipes operation=resume session_id=<session_id>

# Validate a recipe without running it
amplifier tool invoke recipes operation=validate recipe_path=my-recipe.yaml
```

**Note**: There is no `amplifier recipes` CLI command. Recipes are invoked via the `recipes` tool, either conversationally or through `amplifier tool invoke`.

## What Recipes Solve

Recipes are declarative YAML workflows that provide:
- **Orchestration** - Coordinate multiple agents in sequence or parallel
- **Resumability** - Automatic checkpointing; resume if interrupted
- **Approval Gates** - Pause for human review at critical checkpoints
- **Context Flow** - Results from each step available to subsequent steps

## When to Use Recipes

**Use recipes when:**
- Tasks have multiple sequential steps requiring different agents
- The workflow will be repeated (worth encoding as reusable YAML)
- Human approval checkpoints are needed between phases
- Work might be interrupted and needs resumption
- Context must accumulate across agent handoffs

**Use direct agent delegation when:**
- Tasks are single-step or simple
- Real-time interactive iteration is needed
- The workflow is exploratory or ad-hoc

## Tool Operations

| Operation | Use For |
|-----------|---------|
| `execute` | Run a recipe from YAML file |
| `resume` | Continue an interrupted session |
| `validate` | Check recipe YAML before execution |
| `list` | Show active sessions |
| `approvals` | Show pending approval gates |
| `approve/deny` | Respond to approval gates |

## Recipe Paths

The `recipe_path` parameter supports `@bundle:path` format for referencing recipes within bundles. Prefer this format over absolute paths for portability. See the `recipes` tool description for examples.

## Provider and Model Selection

Recipe steps can specify which provider and model to use, enabling cost/capability optimization per step.

### Basic Usage

```yaml
steps:
  - id: "quick-classification"
    agent: "foundation:explorer"
    provider: "anthropic"
    model: "claude-haiku"           # Fast, cheap for simple tasks
    prompt: "Classify this as bug/feature/question"

  - id: "deep-analysis"
    agent: "foundation:zen-architect"
    provider: "anthropic"
    model: "claude-opus-4-*"        # Best reasoning for complex work
    prompt: "Design the architecture for..."
```

### Glob Pattern Matching

Model names support glob patterns (fnmatch-style) for flexible version matching:

| Pattern | Matches |
|---------|---------|
| `claude-sonnet-*` | Latest claude-sonnet version |
| `claude-opus-4-*` | Any claude-opus-4 variant |
| `gpt-*` | Any GPT model |
| `claude-sonnet-4-5-20250514` | Exact model version |

### Model Selection Strategy

| Task Type | Recommended Model | Why |
|-----------|-------------------|-----|
| Simple classification, yes/no | `claude-haiku` | Fast, cheap, sufficient |
| Code implementation, analysis | `claude-sonnet-*` | Good balance of speed/capability |
| Architecture, strategy, security | `claude-opus-*` | Best reasoning, worth the cost |
| Quick summaries, formatting | `claude-haiku` | No deep reasoning needed |

### Fallback Behavior

- If specified provider not configured → uses default provider (warning logged)
- If model pattern has no matches → uses provider's default model
- If no provider/model specified → uses session's configured provider

## Quick Gotchas

- **Field access requires parsing**: Use `parse_json: true` on bash/agent steps if you need `{{result.field}}` access
- **Bash JSON construction**: Use `jq` to build JSON, never shell variable interpolation (breaks on quotes/newlines)
- **Nested context**: Template variables in nested objects are resolved recursively
- **Model patterns**: Use glob patterns like `claude-sonnet-*` to auto-select latest versions

## Presenting Recipe Results

When a recipe produces output designed for external use (announcements, reports, summaries meant for Teams/Slack/email):

**ALWAYS wrap the result in code fences** when presenting to the user:

```
Here's the announcement ready for posting:

\`\`\`
[formatted content here]
\`\`\`
```

**Why**: Terminal reflow destroys intentional line breaks. Without code fences, carefully formatted multi-line output becomes an unreadable wall of text.

**Detection heuristic**: If the output has multiple lines with consistent structure (bullets, numbered items, labeled sections), it likely has intentional formatting. Wrap it.

This applies to:
- Announcement text (Teams, Slack, Discord posts)
- Report summaries with structured sections
- Any output the user will copy-paste elsewhere

## Getting Help

**Delegate to `recipes:recipe-author`** when users need to:
- Create new recipes (conversational workflow design)
- Validate, debug, or improve existing recipes
- Learn about recipe capabilities or patterns
- Add advanced features (loops, conditions, parallel execution, approval gates)
- Decide whether to extract sub-recipes vs keep steps inline (modularization)

The recipe-author agent has complete schema knowledge and will ask clarifying questions to design the right workflow. Don't attempt to write recipe YAML directly—delegate to this expert.

**Use `recipes:result-validator`** for:
- Objective pass/fail assessment of step outcomes or workflow results
- Systematic evaluation against criteria or rubrics
- Formal verdicts needed for automation decisions or approval gates

**Diagrams are auto-maintained:** `validate-recipes` Phase 7 auto-regenerates stale or missing DOT+PNG diagrams with LLM-enhanced accessible labels by default. Set `enhance_diagrams: "false"` to get structural-only regeneration (faster, no LLM cost). Run `recipes:recipes/generate-recipe-docs.yaml` directly for bulk generation or to force a full refresh.

