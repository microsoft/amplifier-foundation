# Amplifier Foundation

Ultra-thin mechanism layer for bundle composition in the Amplifier ecosystem.

Foundation provides the mechanisms for loading, composing, and resolving bundles from local and remote sources. It sits between `amplifier-core` (kernel) and applications, enabling teams to share and compose AI agent configurations.

## Quick Start (5 minutes)

### Install

```bash
pip install amplifier-foundation
```

### Load a Local Bundle

```python
import asyncio
from amplifier_foundation import load_bundle

async def main():
    # Load from local path
    bundle = await load_bundle("./my-bundle")

    # Get mount plan for AmplifierSession
    mount_plan = bundle.to_mount_plan()
    print(f"Loaded: {bundle.name} v{bundle.version}")
    print(f"Providers: {len(mount_plan.get('providers', []))}")
    print(f"Tools: {len(mount_plan.get('tools', []))}")

asyncio.run(main())
```

### Load from Git

```python
from amplifier_foundation import load_bundle

# Load bundle from GitHub
bundle = await load_bundle("git+https://github.com/microsoft/amplifier-foundation@main")

# Or with subdirectory
bundle = await load_bundle(
    "git+https://github.com/org/repo@main#subdirectory=bundles/my-bundle"
)
```

### Compose Bundles

```python
from amplifier_foundation import load_bundle

# Load base and overlay
base = await load_bundle("foundation")
overlay = await load_bundle("./my-customizations")

# Compose: overlay overrides base
composed = base.compose(overlay)
mount_plan = composed.to_mount_plan()
```

## What is a Bundle?

A **bundle** is a composable unit containing:

- **Mount plan config**: session, providers, tools, hooks
- **Resources**: agents, context files
- **Composition rules**: which bundles to include
- **Instructions**: system prompt for LLM guidance

Bundles produce mount plans for `AmplifierSession.create()`.

### Bundle Format

Bundles can be YAML (metadata only) or Markdown (metadata + instructions):

**bundle.yaml** - Configuration only:

```yaml
bundle:
  name: my-app
  version: 1.0.0
  description: My application bundle

includes:
  - foundation
  - foundation:behaviors/logging

session:
  orchestrator:
    module: loop-streaming
    source: git+https://github.com/microsoft/amplifier-module-loop-streaming@main

providers:
  - module: provider-anthropic
    source: git+https://github.com/microsoft/amplifier-module-provider-anthropic@main
    config:
      default_model: claude-sonnet-4-5

tools:
  - module: tool-filesystem
    source: git+https://github.com/microsoft/amplifier-module-tool-filesystem@main
```

**bundle.md** - Configuration + instructions:

```markdown
---
bundle:
  name: dev-assistant
  version: 1.0.0

includes:
  - foundation
---

# Development Assistant

You are a development assistant. Follow best practices for code quality.

@foundation:context/IMPLEMENTATION_PHILOSOPHY.md
```

The markdown body becomes the system instruction.

## Loading Bundles

### URI Patterns

Foundation supports multiple source types:

| Pattern | Example | Description |
|---------|---------|-------------|
| Local path | `./my-bundle` | Directory or file |
| File URI | `file:///path/to/bundle` | Explicit file path |
| Git | `git+https://github.com/org/repo@main` | Git repository |
| Git subdirectory | `git+https://github.com/org/repo@v1.0#subdirectory=bundles/app` | Path within repo |
| Zip over HTTPS | `zip+https://example.com/bundles.zip#subdirectory=my-bundle` | Zip archive |
| Zip local | `zip+file:///archive.zip#subdirectory=path` | Local zip file |

### Resolution Priority

When loading by name, resolution follows this order:

1. **URIs**: `git+`, `http://`, `https://`, `file://` - resolve directly
2. **Local paths**: `./`, `../`, `~/`, `/` - filesystem lookup
3. **Bundle references**: `namespace:path` - lookup namespace, resolve path
4. **Plain names**: `foundation` - discovery lookup

### Customizing Resolution

```python
from amplifier_foundation import BundleRegistry
from pathlib import Path

# Create registry with custom home directory
registry = BundleRegistry(home=Path("~/.my-app").expanduser())

# Register known bundles (accepts a dict)
registry.register({
    "foundation": "git+https://github.com/microsoft/amplifier-foundation@main",
    "my-bundle": "file:///path/to/my-bundle",
})

bundle = await registry.load("my-bundle")
```

## Composition

Bundles compose through the `includes:` directive (declarative) or `compose()` method (imperative).

### Declarative Composition

```yaml
# bundle.yaml
bundle:
  name: my-app

includes:
  - foundation                           # Base bundle
  - foundation:behaviors/logging         # Add logging behavior
  - foundation:providers/anthropic-sonnet  # Add provider
```

Includes are loaded in order. Later bundles override earlier ones.

### Imperative Composition

```python
from amplifier_foundation import load_bundle

base = await load_bundle("foundation")
logging = await load_bundle("foundation:behaviors/logging")
custom = await load_bundle("./my-customizations")

# Compose: later overrides earlier
result = base.compose(logging, custom)
```

### Composition Rules

| Section | Behavior |
|---------|----------|
| `session` | Deep merge (later overrides) |
| `providers`, `tools`, `hooks` | Merge by module ID |
| `agents`, `context` | Later overrides earlier |
| `instruction` | Later replaces earlier |

## The Foundation Bundle

This package includes a reference `foundation` bundle that provides:

- **Provider-agnostic base**: No providers (you add your choice)
- **Standard tools**: filesystem, bash, web, search, task
- **Behaviors**: logging, streaming-ui, redaction, status-context
- **Agents**: bug-hunter, explorer, zen-architect, modular-builder, etc.

### Using Foundation as Base

```python
from amplifier_foundation import load_bundle

# Load foundation as base
foundation = await load_bundle("foundation")

# Add your provider
my_bundle = await load_bundle("./my-config")
composed = foundation.compose(my_bundle)

# Your config can add providers without duplicating tools
```

Your `my-config/bundle.yaml`:

```yaml
bundle:
  name: my-config

providers:
  - module: provider-anthropic
    source: git+https://github.com/microsoft/amplifier-module-provider-anthropic@main
    config:
      default_model: claude-sonnet-4-5
```

## Bundle Directory Structure

A typical bundle directory:

```
my-bundle/
  bundle.md           # Main bundle file (required)
  agents/             # Agent definitions
    code-reviewer.md
    tester.md
  context/            # Context files for @mentions
    PHILOSOPHY.md
    GUIDELINES.md
  behaviors/          # Sub-bundles for composable behaviors
    strict-mode/
      bundle.yaml
  providers/          # Provider configurations
    anthropic-opus/
      bundle.yaml
```

## API Reference

### Core Classes

#### Bundle

The composable unit containing configuration and resources.

```python
from amplifier_foundation import Bundle

# Create from dict
bundle = Bundle.from_dict({
    "bundle": {"name": "my-bundle", "version": "1.0.0"},
    "providers": [...],
    "tools": [...],
}, base_path=Path("./my-bundle"))

# Compose bundles
result = bundle.compose(other_bundle)

# Get mount plan
mount_plan = bundle.to_mount_plan()

# Resolve resources
agent_path = bundle.resolve_agent_path("bug-hunter")
context_path = bundle.resolve_context_path("PHILOSOPHY.md")
instruction = bundle.get_system_instruction()
```

#### BundleRegistry

Unified class for bundle discovery, registration, and loading.

```python
from amplifier_foundation import BundleRegistry

# Create registry (optional: custom home directory)
registry = BundleRegistry()  # Uses ~/.amplifier by default
# registry = BundleRegistry(home=Path("/custom/home"))

# Register known bundles (accepts a dict)
registry.register({"my-bundle": "git+https://github.com/org/repo@main"})

# Load with automatic include resolution
bundle = await registry.load("my-bundle", auto_include=True)

# Load without resolving includes
bundle = await registry.load("my-bundle", auto_include=False)
```

#### load_bundle

Convenience function for simple loading.

```python
from amplifier_foundation import load_bundle

bundle = await load_bundle("git+https://github.com/org/repo@main")
```

### Protocols (Extension Points)

Foundation provides protocols for customization:

| Protocol | Purpose | Default Implementation |
|----------|---------|----------------------|
| `SourceResolverProtocol` | URI to local path | `SimpleSourceResolver` |
| `SourceHandlerProtocol` | Handle specific URI types | Git, HTTP, Zip, File handlers |
| `CacheProviderProtocol` | Bundle caching | `SimpleCache`, `DiskCache` |
| `MentionResolverProtocol` | @mention resolution | `BaseMentionResolver` |

### Utility Functions

```python
from amplifier_foundation import (
    # I/O
    read_yaml, write_yaml,
    parse_frontmatter,
    read_with_retry, write_with_retry,  # Cloud sync safe

    # Dicts
    deep_merge,
    merge_module_lists,
    get_nested, set_nested,

    # Paths
    parse_uri,
    normalize_path,
    find_files,
    find_bundle_root,

    # Mentions
    parse_mentions,
    load_mentions,

    # Validation
    validate_bundle,
    validate_bundle_or_raise,
)
```

### Exceptions

```python
from amplifier_foundation import (
    BundleError,           # Base exception
    BundleNotFoundError,   # Bundle not found
    BundleLoadError,       # Failed to load
    BundleValidationError, # Invalid bundle
    BundleDependencyError, # Circular dependency
)
```

## Philosophy

Foundation follows Amplifier's core principles:

- **Mechanism, not policy**: Provides loading/composition mechanisms. Apps decide which bundles to use.
- **Ruthless simplicity**: One concept (bundle), one mechanism (`includes:` + `compose()`).
- **Text-first**: YAML/Markdown formats are human-readable, diffable, versionable.
- **Composable**: Small bundles compose into larger configurations.

This library is pure mechanism. It doesn't know about specific bundles (even "foundation"). The co-located foundation bundle content is just content - discovered and loaded like any other bundle.

## Contributing

> [!NOTE]
> This project is not currently accepting external contributions, but we're actively working toward opening this up. We value community input and look forward to collaborating in the future. For now, feel free to fork and experiment!

Most contributions require you to agree to a
Contributor License Agreement (CLA) declaring that you have the right to, and actually do, grant us
the rights to use your contribution. For details, visit [Contributor License Agreements](https://cla.opensource.microsoft.com).

When you submit a pull request, a CLA bot will automatically determine whether you need to provide
a CLA and decorate the PR appropriately (e.g., status check, comment). Simply follow the instructions
provided by the bot. You will only need to do this once across all repos using our CLA.

This project has adopted the [Microsoft Open Source Code of Conduct](https://opensource.microsoft.com/codeofconduct/).
For more information see the [Code of Conduct FAQ](https://opensource.microsoft.com/codeofconduct/faq/) or
contact [opencode@microsoft.com](mailto:opencode@microsoft.com) with any additional questions or comments.

## Trademarks

This project may contain trademarks or logos for projects, products, or services. Authorized use of Microsoft
trademarks or logos is subject to and must follow
[Microsoft's Trademark & Brand Guidelines](https://www.microsoft.com/legal/intellectualproperty/trademarks/usage/general).
Use of Microsoft trademarks or logos in modified versions of this project must not cause confusion or imply Microsoft sponsorship.
Any use of third-party trademarks or logos are subject to those third-party's policies.
