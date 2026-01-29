# Foundation Ecosystem Awareness Index

This index provides awareness of the Amplifier ecosystem: module types, documentation locations, and domain prerequisites. This knowledge enables effective navigation and understanding of the system.

---

## Agent Delegation

When the `foundation:behaviors/agents` behavior is composed, detailed delegation
triggers and patterns are available. See that behavior's context for:
- Domain-claiming agents (MUST delegate)
- Implementation agents
- Expert consultants

---

## Kernel Vocabulary (Module Types)

Understanding what module types exist enables effective discussion and delegation.

| Type | Purpose | Contract |
|------|---------|----------|
| **Provider** | LLM backends | `complete(request) → response` |
| **Tool** | Agent capabilities | `execute(input) → result` |
| **Orchestrator** | The main engine driving sessions | `execute(prompt, context, providers, tools, hooks) → str` |
| **Hook** | Lifecycle observer | `__call__(event, data) → HookResult` |
| **Context** | Memory management | `add/get/set_messages, clear` |

**Note**: There are exactly 5 kernel module types. "Agent" is NOT a module type - see below.

### Orchestrator: The Main Engine

The orchestrator is **THE control surface** for agent behavior, not just "execution strategy":
- Controls the entire LLM → tool → response loop
- Decides when to call providers, how to process tool calls, when to emit events
- Swapping orchestrators can **radically change** how an agent behaves
- Examples: agentic-loop (default), streaming, event-driven, observer-pattern

### Agents, Bundles, and Behaviors

Agents are bundle-level abstractions (not kernel modules). For bundle composition, behavior patterns, agent spawning mechanics, and context architecture, delegate to `foundation:foundation-expert`.

**Key distinction**: There are exactly 5 kernel module types (Provider, Tool, Orchestrator, Hook, Context). "Agent" and "behavior" are bundle-layer conventions built on top of these primitives.

### Tool vs Hook: The Triggering Difference

| | **Tools** | **Hooks** |
|--|-----------|-----------|
| **Triggered by** | LLM decides to call | Code (lifecycle events) |
| **Control** | LLM-driven | Full programmatic control |

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

## Domain Prerequisites

Some domains have anti-patterns that cause significant rework. **Load required reading BEFORE starting work.**

| Domain | Required Reading |
|--------|------------------|
| Bundle/module packaging | `foundation:docs/BUNDLE_GUIDE.md` |
| Hook implementation | `core:docs/HOOKS_API.md` |

**Pattern**: When you detect work in a listed domain, load the doc FIRST—don't wait until you hit problems.

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

## Delegation Guidance

Delegation rules, urgency tiers, and multi-agent patterns are provided
via the `foundation:behaviors/agents` behavior context.
