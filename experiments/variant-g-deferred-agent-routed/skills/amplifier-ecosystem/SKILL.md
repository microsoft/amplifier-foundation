---
skill:
  name: amplifier-ecosystem
  description: Amplifier ecosystem overview — kernel philosophy, module types, dependency hierarchy, bundle composition. Load when working on Amplifier internals.
  version: 1.0.0
---

# Amplifier Ecosystem

## Architecture

Tiny stable kernel (amplifier-core, ~2,600 lines) provides mechanisms. All policies live at the edges as replaceable modules.

## Module Types (exactly 5)

| Type | Purpose | Triggered by |
|------|---------|-------------|
| Provider | LLM backends | Orchestrator |
| Tool | Agent capabilities | LLM decides |
| Orchestrator | The main engine (LLM → tool → response loop) | Session |
| Hook | Lifecycle observers | Code (events) |
| Context | Memory management | Session |

"Agent" is NOT a module type — agents are bundle-level abstractions.

## Dependency Hierarchy

```
amplifier (docs) → amplifier-app-cli
                      ├── amplifier-foundation
                      └── amplifier-core ← ALL modules
```

Modules depend ONLY on amplifier-core, never on foundation or apps.

## Key Principle

**Mechanism, not policy.** Could two teams want different behavior? → Module, not kernel.
