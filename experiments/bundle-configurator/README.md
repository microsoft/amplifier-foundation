# Bundle Configurator (Experiment)

A bundle editor for Amplifier. Load any bundle — a well-known name like `foundation` or `amplifier-dev`, a registered bundle, a git URI, or a local `.md` file. See the token cost broken down by behavior. Add or remove parts. Save a new bundle file that Amplifier can load.

**The value:** know exactly where your session's tokens come from, and trim the ones you don't need.

This is an experiment co-located in the amplifier-foundation repo. It does not ship in the amplifier-foundation wheel. The merge path, if Brian approves the API, is to move `amplifier_configurator/` into `amplifier_foundation/configurator/` — same pattern as `amplifier_foundation/session/`.

## Key Concepts

A few terms used throughout this README:

- **Behavior** — a composable unit in an Amplifier bundle (e.g. `dot-graph`, `browser-tester`). Each behavior contributes agents, context files, tools, and other parts to the bundle.
- **Part** — a single element contributed by a behavior: a tool, hook, agent, provider, or context file. For example, the `code-intel` behavior contributes the `tool-lsp` tool and several context files.
- **Provenance** — tracking which behavior contributed which part. This is how the configurator attributes token cost to individual behaviors.
- **Composition** — the process of merging multiple behaviors into a final bundle. When behaviors overlap (e.g. two behaviors both contribute the same tool), the deeper behavior wins.

## Install

```bash
pip install "git+https://github.com/microsoft/amplifier-foundation@feat/bundle-configurator-experiment#subdirectory=experiments/bundle-configurator"
```

After this branch is merged to main:

```bash
pip install "git+https://github.com/microsoft/amplifier-foundation#subdirectory=experiments/bundle-configurator"
```

## Using This in an Amplifier Chat Session

If you're running Amplifier, you don't need to write Python to use this library. Just ask the agent:

- "Load the foundation bundle and show me the token cost per behavior"
- "Remove `behavior-agents` from foundation, then save to `~/.amplifier/bundles/lean-foundation/bundle.md`"
- "Activate that new bundle for this project"
- "Show me the diff between foundation and my edited version"

The agent will call the library methods shown below, run the relevant Amplifier CLI commands (`amplifier bundle add`, `amplifier bundle use`), and report back.

The Python examples in the rest of this README show what the agent does on your behalf — you don't need to know Python to use this tool, just to understand what's possible.

## Quick Start

```python
from amplifier_configurator import BundleConfigurator

# Load any bundle by name, git URI, or file path
cfg = BundleConfigurator.load_sync("foundation")

# See token cost by behavior, sorted descending
for behavior, tokens in cfg.tokens_by_behavior().items():
    print(f"  {behavior:30s} {tokens:5d} tokens")

# Total
print(f"\nTotal: {cfg.total_tokens()} tokens")

# Remove a behavior (returns a new instance — original unchanged)
lean = cfg.remove_behavior("code-intel")

# See what changed
diff = cfg.diff(lean)
print(f"\nRemoved: {diff.token_delta} tokens")
for part in diff.removed_parts:
    print(f"  - {part.kind.value} {part.name}")

# Save the result as a valid .md bundle file
lean.save("/tmp/lean-foundation/bundle.md")
```

After running this, `/tmp/lean-foundation/bundle.md` is a valid bundle that Amplifier can load directly — identical to `foundation` but without the `code-intel` behavior and the tokens it contributed.

## API

### BundleConfigurator

The main class. Constructed via class methods; do not instantiate directly.

**Loading:**

```python
# Async (preferred in async contexts)
cfg = await BundleConfigurator.load("foundation")

# Synchronous wrapper
cfg = BundleConfigurator.load_sync("foundation")
```

`source` accepts any identifier that amplifier-foundation can resolve: a bundle name (e.g. `"foundation"`), a git URI (e.g. `"git+https://github.com/org/repo"`), or a file path (e.g. `"./my-bundle/bundle.md"`).

**Querying:**

| Method | Returns | Description |
|--------|---------|-------------|
| `list_behaviors()` | `list[BehaviorInfo]` | All included behaviors, leaves first |
| `list_parts(kind=None)` | `list[TrackedPart]` | All tracked parts; optional `PartKind` filter |
| `tokens_by_behavior()` | `dict[str, int]` | Token cost per behavior, sorted descending |
| `total_tokens()` | `int` | Sum of all part tokens + root instruction |
| `get_behavior(name)` | `BehaviorInfo` | Behavior by short name; raises `KeyError` if not found |
| `get_part(kind, name)` | `TrackedPart` | Part by `(PartKind, name)`; raises `KeyError` if not found |

**Mutation (immutable — each method returns a new instance):**

| Method | Returns | Description |
|--------|---------|-------------|
| `remove_behavior(name)` | `BundleConfigurator` | Remove behavior and all parts it exclusively contributed; cascades to orphaned children |
| `remove_part(kind, name)` | `BundleConfigurator` | Remove a single part; raises `DependencyError` if part is required |
| `await add_behavior(uri)` | `BundleConfigurator` | Load and merge a new behavior by URI |

**Analysis and output:**

| Method | Returns | Description |
|--------|---------|-------------|
| `diff(other)` | `BundleDiff` | Parts and behaviors added/removed, token delta |
| `validate()` | `(list[str], list[str])` | `(errors, warnings)` from dependency analysis |
| `save(path)` | `Path` | Write `.md` bundle file; raises `ConfiguratorError` on validation errors |

### Data Models

**`BehaviorInfo`** — what a behavior contributes to the bundle:
- `name: str` — short name (e.g. `"code-intel"`)
- `uri: str` — full bundle URI used to load this behavior
- `parts: tuple[TrackedPart, ...]` — de-duplicated parts this behavior won
- `total_tokens: int` — context token cost (context files + instructions only)
- `depth: int` — 0 = direct include of root, 1 = transitive, etc.
- `include_chain: tuple[str, ...]` — path from root to this behavior

**`TrackedPart`** — a single part with its provenance:
- `kind: PartKind` — `TOOL | HOOK | AGENT | PROVIDER | CONTEXT`
- `name: str` — module name or context key
- `source_behavior: str | None` — which behavior contributed it (`None` = root)
- `tokens: int` — estimated token cost (non-zero only for `CONTEXT` parts)
- `config: dict` — the raw dict from the bundle YAML

**`BundleDiff`**:
- `added_parts / removed_parts: tuple[TrackedPart, ...]`
- `added_behaviors / removed_behaviors: tuple[str, ...]`
- `before_tokens / after_tokens / token_delta: int`

**`PartKind`** (enum): `TOOL`, `HOOK`, `AGENT`, `PROVIDER`, `CONTEXT`

### Errors

- `ConfiguratorError` — base error class
- `LoadError` — amplifier-foundation failed to resolve or load the bundle
- `DependencyError` — attempted to remove a required part (e.g. `tool-bash`, `tool-filesystem`, `tool-search`)

## What It Counts

The library measures **context files and instruction text** — the portions of a bundle that are loaded into the model's context at session start. This is the controllable portion of token cost.

It does **not** measure tool schemas. Tool schemas are injected by the runtime based on installed module versions; they are not present in the `.md` bundle file and cannot be measured from the bundle alone.

For most optimization work, context files and instructions are the dominant cost. Tool schemas are relatively stable across sessions.

## How It Works

1. The library calls amplifier-foundation's `BundleRegistry` to load each included behavior separately, building a complete include tree.
2. Each behavior's contributions (tools, hooks, agents, context files) are attributed to that behavior using a last-write-wins, bottom-up rule: deeper (leaf) behaviors win over shallower ones on conflicts.
3. The resulting provenance map tracks every part with its source behavior, token cost, and original config dict.
4. All mutation methods (`remove_behavior`, `remove_part`, `add_behavior`) work on the provenance map directly and return a new `BundleConfigurator` without reloading from disk.
5. `save()` serializes the current state to YAML frontmatter + markdown body using the include-reference pattern (behaviors referenced by URI, not flattened).

## Relationship to Runtime Configurator

This library is the **file-level** bundle editor. It reads and writes `.md` bundle files on disk.

Brian's runtime configurator (in the amplifier-foundation session layer) is the **session-level** toggle — it switches behaviors on and off within a running session without touching the bundle file on disk.

They are complementary: this library produces the files that the runtime configurator operates on. A workflow might be: use this library to produce a lean bundle variant, then use the runtime configurator to dynamically adjust it mid-session.

## Status

Experiment. Not production-ready. API may change based on Brian's feedback.

Test coverage:
- 129 unit tests (no amplifier-foundation required)
- 6 integration tests (require amplifier-foundation to be installed and reachable)
- 1 end-to-end round-trip test (`e2e_shadow_test.py`)

## Run Tests

```bash
# Install dependencies
pip install -e ".[dev]"

# Unit tests only (fast, no network calls)
pytest tests/ -k "not integration"

# Integration tests (loads real bundles via amplifier-foundation)
pytest tests/test_real_bundles.py -m integration -v

# End-to-end round trip
pytest tests/e2e_shadow_test.py -v
```

Or, using the Amplifier-managed Python:

```bash
~/.local/share/uv/tools/amplifier/bin/python -m pytest tests/ -k "not integration"
```
