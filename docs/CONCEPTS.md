# Core Concepts

Mental model for Amplifier Foundation. For code examples, see [PATTERNS.md](PATTERNS.md).

## What is a Bundle?

A **Bundle** is a composable configuration unit that produces a **mount plan** for AmplifierSession.

```
Bundle → to_mount_plan() → Mount Plan → AmplifierSession
```

### Bundle Contents

| Section | Purpose |
|---------|---------|
| `bundle` | Metadata (name, version) |
| `session` | Orchestrator and context manager |
| `providers` | LLM backends |
| `tools` | Agent capabilities |
| `hooks` | Observability and control |
| `agents` | Named agent configurations |
| `context` | Context files to include |
| `instruction` | System instruction (markdown body) |
| `spawn` | Tool inheritance policy for spawned agents |

Bundles are markdown files with YAML frontmatter. See [PATTERNS.md](PATTERNS.md) for format examples.

## Composition

Bundles can be **composed** to layer configuration:

```python
result = base.compose(overlay)  # Later overrides earlier
```

### Merge Rules

| Section | Rule |
|---------|------|
| `session` | Deep merge (nested dicts merged) |
| `providers` | Merge by module ID |
| `tools` | Merge by module ID |
| `hooks` | Merge by module ID |
| `spawn` | Deep merge (later overrides) |
| `instruction` | Replace (later wins) |

**Module ID merge**: Same ID = update config, new ID = add to list.

## Mount Plan

A **mount plan** is the final configuration dict consumed by AmplifierSession.

Contains: `session`, `providers`, `tools`, `hooks`, `agents`

**Not included**: `includes` (resolved), `context` (processed separately), `instruction` (injected into context).

## Prepared Bundle

A **PreparedBundle** is a bundle ready for execution with all modules activated.

```python
bundle = await load_bundle("/path/to/bundle.md")
prepared = await bundle.prepare()  # Downloads modules
async with prepared.create_session() as session:
    response = await session.execute("Hello!")
```

`prepare()` downloads modules from git URLs, installs dependencies, and returns a PreparedBundle with module resolver.

## @Mention Resolution

Instructions can reference context files from composed bundles using `@namespace:path` syntax:

```markdown
See @foundation:context/guidelines.md for guidelines.
```

How it works:
1. During composition, each bundle's `base_path` is tracked by namespace (from `bundle.name`)
2. PreparedBundle resolves `@namespace:path` references against the original bundle's path
3. Content is loaded and included inline

This allows instructions to reference files from any included bundle without knowing absolute paths.

## Bundle Loading: Structural Concepts

Bundles have a **structural** classification based on how they load. This affects namespace registration and path resolution.

### Root Bundles vs Nested Bundles

| Term | Definition | Registry State |
|------|------------|----------------|
| **Root bundle** | A `/bundle.md` or `/bundle.yaml` at the root of a repo or directory tree. Establishes the namespace and root directory for path resolution. | `is_root = True` |
| **Nested bundle** | Any bundle loaded via `#subdirectory=` URIs or `@namespace:path` references. Paths resolve relative to its location, shares namespace with root. | `is_root = False` |

**Key insight:** The namespace comes from `bundle.name`, not the repo URL or directory name.

### Namespace Registration

When a bundle loads, its `bundle.name` becomes a namespace for `@mention` resolution:

```
Load foundation → Registers "foundation" namespace
               → @foundation:docs/GUIDE.md resolves to /path/to/foundation/docs/GUIDE.md
```

Root bundles establish the namespace. Nested bundles (behaviors, providers, etc.) share the root's namespace and resolve paths relative to their own location.

### Loading Flow

```
Root bundle loads → Registers namespace → All nested bundles share that namespace
                                       → Paths resolve relative to bundle location
```

### Example: Nested Bundle Loading

```yaml
# A root bundle at repo root
# bundles/with-anthropic.yaml (loaded via foundation:bundles/with-anthropic)
includes:
  - bundle: foundation                          # The root bundle
  - bundle: foundation:providers/anthropic-opus  # A nested bundle
```

When `foundation:bundles/with-anthropic` loads:
1. It's detected as a **nested bundle** (there's a root `bundle.md` above it)
2. It shares the `foundation` namespace
3. Its own includes resolve relative to its location

### Structural vs Conventional

Bundles have two independent classification systems:

| Layer | Enforced By | Purpose |
|-------|-------------|---------|
| **Structural** | Code (registry.py) | How bundles load, namespace registration |
| **Conventional** | Patterns (see [BUNDLE_GUIDE.md](BUNDLE_GUIDE.md)) | How to organize repos for maximum utility |

A bundle can be:
- **Structurally:** Nested (`is_root=False`) - loaded under root's directory
- **Conventionally:** A "standalone bundle" - ready to use as-is from `/bundles/`

These aren't contradictions—they describe different aspects.

## Mount Plans vs Bundles

- **Mount plans are REQUIRED**: AmplifierSession needs a mount plan to run
- **Bundles are OPTIONAL**: They're a composition/sharing layer on top

Bundles exist for **sharing and remixing**. You can create mount plans directly in code without ever touching bundles. Bundles make it easy to share and compose configurations, but they're not mandatory.

## Agents

**Agents are bundles.** They use the same file format (markdown + YAML frontmatter) and are loaded via the same `load_bundle()` function.

The only difference is the frontmatter key:
- Bundles use `bundle:` with `name` and `version`
- Agents use `meta:` with `name` and `description`

```python
# Both use load_bundle()
bundle = await load_bundle("./bundle.md")
agent = await load_bundle("./agents/my-agent.md")
```

See [AGENT_AUTHORING.md](AGENT_AUTHORING.md) for agent-specific guidance (the `description` field pattern).

## Session Capabilities

**Capabilities** are named values registered on the coordinator that modules can query at runtime. Foundation registers several capabilities during session creation.

### session.working_dir

The session's working directory. Critical for server/web deployments where `Path.cwd()` returns the server's directory, not the user's project.

**Registered by**: `PreparedBundle.create_session()` and `spawn()`

**Value**: Absolute path string (e.g., `/home/user/myproject`)

**Module usage**:
```python
from amplifier_foundation import get_working_dir

# In mount() or tool execute()
working_dir = get_working_dir(coordinator)  # Returns Path
```

**Fallback behavior**: If capability not set, `get_working_dir()` returns `Path.cwd()` for backward compatibility.

**Dynamic updates**: Use `set_working_dir(coordinator, path)` to change the working directory mid-session (e.g., when assistant "cd"s into a subdirectory).

### bundle_package_paths

List of `src/` directories from bundles that need to be on `sys.path` for module imports.

**Registered by**: `PreparedBundle.create_session()` when bundles include Python packages.

## Philosophy

### Mechanism, Not Policy

Foundation provides **mechanism** for bundle composition. It doesn't decide which bundles to use or how to configure them—those are **policy** decisions for your application.

### Text-First

- Bundles are markdown (human-readable)
- Configuration is YAML (diffable)
- No binary formats

## Next Steps

- **[API_REFERENCE.md](API_REFERENCE.md)** - API index pointing to source
- **[PATTERNS.md](PATTERNS.md)** - Common usage patterns with code examples
- **[URI_FORMATS.md](URI_FORMATS.md)** - Source URI reference
