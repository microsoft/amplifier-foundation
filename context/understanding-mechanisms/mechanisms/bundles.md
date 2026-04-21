# Bundles: Composable Configuration Packages

## What It Is

A bundle is Amplifier's core composition unit -- a markdown file (or YAML file)
with YAML frontmatter that packages tools, providers, agents, hooks, context, and
system instructions into a single "mount plan" for an AmplifierSession.

The file format is intentionally simple: YAML frontmatter between `---` fences
defines the configuration, and the markdown body below becomes the system prompt.

## How It Works

### Bundle Structure

```yaml
---
bundle:
  name: my-bundle        # Establishes namespace for @mentions
  version: 1.0.0

includes:
  - bundle: foundation   # Inherit from other bundles

session:
  orchestrator: {module: loop-streaming, source: git+https://...}
  context: {module: context-simple, source: git+https://...}

providers:
  - module: provider-anthropic
    source: git+https://...
    config: {default_model: claude-sonnet-4-5}

tools:
  - module: tool-filesystem
    source: git+https://...

hooks:
  - module: hooks-logging
    source: git+https://...

agents:
  include:
    - my-bundle:agent-name
---

# System Instructions

Everything below the YAML becomes the system prompt.
Reference files with @my-bundle:context/guide.md
```

### Composition Model

Bundles compose via `includes:` with "later overrides earlier" semantics. Different
sections have different merge rules:

| Section | Merge Rule |
|---------|-----------|
| `session`, `spawn` | Deep merge (nested dicts merged recursively) |
| `providers`, `tools`, `hooks` | Merge by module ID (same ID = update config) |
| `agents` | Later overrides earlier (by agent name) |
| `context` | Accumulates with namespace prefix (no collision) |
| `instruction` | Replace (later wins entirely) |

Includes are loaded in parallel with circular dependency detection.

### The "Thin Bundle" Pattern

Most bundles inherit from foundation and only declare unique additions:

```yaml
bundle:
  name: recipes
  version: 1.0.0
includes:
  - bundle: git+https://github.com/microsoft/amplifier-foundation@main
  - bundle: recipes:behaviors/recipes
```

No `tools:`, `session:`, or `hooks:` needed -- all inherited from foundation.

### Behaviors

A behavior IS a bundle structurally (same YAML format), but by convention it's a
reusable capability add-on rather than a standalone configuration. Behaviors use
`context.include` (which accumulates during composition) rather than `@mentions`
(which stay with a specific instruction).

Real examples from foundation:
- `agents.yaml` -- adds delegate tool, skills tool, delegation context
- `sessions.yaml` -- adds session naming hook, logging, session-analyst agent
- `redaction.yaml` -- adds secret/PII redaction hook

### Loading Pipeline

1. `BundleRegistry` resolves source URI (git, file, http, zip)
2. Parses frontmatter + markdown body into `Bundle` dataclass
3. Resolves and loads all `includes` in parallel
4. Composes left-to-right, declaring bundle wins
5. `PreparedBundle` activates all modules, creates session
6. System prompt factory resolves `@mentions` on every turn

### Key Implementation Files

- `amplifier_foundation/bundle/_dataclass.py` -- Bundle dataclass (762 lines)
- `amplifier_foundation/bundle/_prepared.py` -- PreparedBundle (648 lines)
- `amplifier_foundation/registry.py` -- BundleRegistry (1,301 lines)
- `amplifier_foundation/mentions/` -- @mention subsystem

## Relevance to System Design Bundle

The bundle is our primary delivery mechanism. Our systems-design
bundle will be structured as a thin bundle that includes foundation and adds:

- **Context files** for system design principles, patterns, and practices
- **Agents** for architecture analysis, design review, constraint evaluation
- **Skills** for on-demand system design knowledge (system types, patterns)
- **Modes** for structured design workflows (analogous to superpowers' brainstorm)
- **Recipes** for multi-step design processes
- **Hooks** (potentially) for automated design feedback

The composition model means users can include our bundle alongside others.
Behaviors let us ship individual capabilities (e.g., "just the design review
agent" or "just the constraint analysis tool") that others can compose.

Key design decisions for our bundle:
- Should we include foundation directly, or let users compose us with their
  existing foundation setup?
- Which capabilities should be behaviors (composable) vs root bundle features?
- How do we handle context files for different system types (web service,
  distributed system, etc.) -- static includes vs on-demand skills?
