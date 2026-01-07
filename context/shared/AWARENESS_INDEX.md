# Foundation Ecosystem Awareness Index

This index provides the root session with awareness of what exists in the Amplifier ecosystem, enabling effective delegation to specialized agents.

---

## Agent Delegation Triggers

Agents carry @-mentioned context the root session lacks. Delegate when triggers match.

### Domain-Claiming Agents (MUST delegate)

| Agent | Trigger | Domain |
|-------|---------|--------|
| `foundation:bug-hunter` | **REQUIRED** when errors, test failures, debugging | Hypothesis-driven debugging |
| `foundation:session-analyst` | **REQUIRED** for events.jsonl, session files | Handles 100k+ token lines safely |
| `foundation:git-ops` | **ALWAYS** for commits, PRs, branch operations | Safety protocols, quality messages |

### Implementation Agents

| Agent | When to Use | Expertise |
|-------|-------------|-----------|
| `foundation:zen-architect` | Architecture design, planning, code review | Philosophy compliance, design patterns |
| `foundation:modular-builder` | Implementing code from specifications | Bricks-and-studs code generation |
| `foundation:explorer` | Codebase reconnaissance, understanding structure | Structured breadth-first survey |
| `foundation:security-guardian` | Security review, vulnerability assessment | OWASP patterns, threat models |
| `foundation:test-coverage` | Test coverage analysis, test strategy | Strategic testing patterns |
| `foundation:integration-specialist` | External integrations, API connections, dependencies | Clean boundary patterns |
| `foundation:post-task-cleanup` | Post-task workspace hygiene | Philosophy compliance check |
| `foundation:file-ops` | File read/write/edit operations | Context sink for file operations |
| `foundation:web-research` | Web searches, fetching URLs | Context sink for web operations |

### Expert Consultants

| Domain | Agent | When to Consult |
|--------|-------|-----------------|
| Ecosystem knowledge | `amplifier:amplifier-expert` | "What repo?", "Is this possible?", module catalog, governance |
| Kernel internals | `core:core-expert` | Events, hooks, protocols, "kernel vs module" decisions |
| Bundle composition | `foundation:foundation-expert` | Patterns, examples, building applications |
| Recipe authoring | `recipes:recipe-author` | YAML workflow creation, validation |

---

## Kernel Vocabulary (Module Types)

Understanding what module types exist enables effective discussion and delegation.

| Type | Purpose | Contract |
|------|---------|----------|
| **Provider** | LLM backends | `complete(request) → response` |
| **Tool** | Agent capabilities | `execute(input) → result` |
| **Orchestrator** | Execution strategy | `execute(prompt, context, ...) → str` |
| **Hook** | Lifecycle observer | `__call__(event, data) → HookResult` |
| **Context** | Memory management | `add/get/set_messages, clear` |
| **Agent** | Config overlay | Partial mount plan for sub-sessions |

**Session Model**: Sessions carry a Coordinator with mounted modules. Sub-sessions (agents) fork with configuration overlays.

**Event System**: Canonical events (`session:*`, `turn:*`, `tool:*`, `provider:*`) flow through hooks. Hooks can observe, enrich, modify, or cancel.

---

## Documentation Catalog

Load on demand via `read_file` or delegate to expert agents.

### Getting Started
| Need | Location |
|------|----------|
| User quick start | `amplifier:docs/USER_ONBOARDING.md` |
| Developer guide | `amplifier:docs/DEVELOPER.md` |
| Module ecosystem | `amplifier:docs/MODULES.md` |
| Repository rules | `amplifier:docs/REPOSITORY_RULES.md` |

### Bundle Building
| Need | Location |
|------|----------|
| Bundle authoring | `foundation:docs/BUNDLE_GUIDE.md` |
| Common patterns | `foundation:docs/PATTERNS.md` |
| Core concepts | `foundation:docs/CONCEPTS.md` |
| API reference | `foundation:docs/API_REFERENCE.md` |
| URI formats | `foundation:docs/URI_FORMATS.md` |

### Kernel & Modules
| Need | Location |
|------|----------|
| Kernel design | `core:docs/DESIGN_PHILOSOPHY.md` |
| Hook system | `core:docs/HOOKS_API.md` |
| Provider contract | `core:docs/contracts/PROVIDER_CONTRACT.md` |
| Tool contract | `core:docs/contracts/TOOL_CONTRACT.md` |
| Hook contract | `core:docs/contracts/HOOK_CONTRACT.md` |

### Recipes
| Need | Location |
|------|----------|
| Recipe schema | `recipes:docs/RECIPE_SCHEMA.md` |
| Best practices | `recipes:docs/BEST_PRACTICES.md` |
| Example recipes | `recipes:examples/` |

### Philosophy (Already Loaded)
- `foundation:context/IMPLEMENTATION_PHILOSOPHY.md` - Ruthless simplicity
- `foundation:context/MODULAR_DESIGN_PHILOSOPHY.md` - Bricks and studs
- `foundation:context/KERNEL_PHILOSOPHY.md` - Mechanism not policy

---

## Examples Catalog

Working examples in `foundation:examples/`:

| Example | Description |
|---------|-------------|
| 01-07 | Basic patterns (hello world through hooks) |
| 08-10 | Multi-provider and multi-agent patterns |
| 11-15 | Advanced features (MCP, tools, context) |
| 16-22 | Bundle composition patterns |

Delegate to `foundation:foundation-expert` for guidance on which example applies to your use case.

---

## Delegation Decision Framework

### DELEGATE when:
- Task matches a domain-claiming agent (ALWAYS/REQUIRED/MUST)
- Task requires **>2 exploratory tool calls** (file reads, searches, etc.)
- Task is open-ended: "find", "explore", "understand", "review", "investigate"
- You lack specialized context the agent has (@-mentioned docs, tools)
- Task will consume significant context (many files, large outputs)

### Execute DIRECTLY when:
- Single file read/write to a **known** location
- Single command with **known** outcome (git status, ls, pwd)
- Needle query for a specific item you can pinpoint

### Delegation Urgency Tiers

| Tier | Keyword | Meaning | Agents |
|------|---------|---------|--------|
| **1** | ALWAYS | 100% delegation, no exceptions | git-ops |
| **2** | REQUIRED | Delegate when trigger condition matches | bug-hunter, session-analyst |
| **3** | MUST | Delegate for domain tasks | zen-architect, modular-builder |
| **4** | PREFERRED | Delegate unless truly trivial (<2 tool calls) | explorer, security-guardian |

### Multi-Agent Patterns

**Use multiple agents in parallel** for richer investigation:

| Task | Agent Combination | Why |
|------|-------------------|-----|
| Code investigation | `python-code-intel` + `explorer` + `zen-architect` | LSP traces actual code; explorer finds related files; architect assesses design |
| Bug debugging | `bug-hunter` + `python-code-intel` | Hypothesis methodology + precise call tracing |
| Implementation | `zen-architect` → `modular-builder` → `zen-architect` | Design → implement → review |

**Key insight**: Different agents have different tools (LSP vs grep), perspectives (deterministic vs exploratory), and context. TOGETHER they reveal more than any single agent.

---

## Quick Decision Guide

```
Need to understand what exists?     → amplifier:amplifier-expert
Need kernel/protocol details?       → core:core-expert  
Need bundle/pattern guidance?       → foundation:foundation-expert
Need to create a recipe?            → recipes:recipe-author
Encountering errors?                → foundation:bug-hunter (REQUIRED)
Working with session files?         → foundation:session-analyst (REQUIRED)
Making git commits/PRs?             → foundation:git-ops (ALWAYS)
```

**When in doubt**: If an agent claims a domain with MUST/REQUIRED/ALWAYS, it has context you don't. Delegate immediately.
