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

### Behavior-first and supporting-root patterns

A reusable capability is a behavior partial. It contributes only the capability;
the host chooses its root, provider, orchestrator, context manager, and
instruction. Complete roots choose runtime defaults; applications may override
them:

```yaml
bundle:
  name: recipes-behavior
  version: 1.0.0
agents:
  include:
    - recipes:recipe-author
```

A complete host may offer a thin supporting root that composes the behavior. New
complete hosts can compose Anchors; adding a capability to an existing host does
not require replacing that host:

```yaml
includes:
  - bundle: git+https://github.com/microsoft/amplifier-foundation@main#subdirectory=bundles/anchors.md
  - bundle: recipes:behaviors/recipes
```

The root preserves `@anchors:context/system.md` when it has its own instruction
body. Existing selected legacy roots remain valid. A root can also anchor
repo-level namespaced resources, so behavior-first is not a root ban.

For example, `bundles/anchors-amp-dev.md` composes `bundles/anchors.md` plus
`behaviors/amp-dev.yaml`. All Amplifier-development additions belong to that
runtime-neutral behavior, including the `amp-dev:amplifier-dev-expert` agent and
Tester behavior. Another host can compose the same capability without Anchors.
The flat Anchors manifest uses `namespace_root: anchors`, relative to its
containing `bundles/` directory, to retain the existing `anchors:` assets.

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
