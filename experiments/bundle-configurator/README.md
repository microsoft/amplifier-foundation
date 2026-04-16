# amplifier-configurator

A bundle editor for [Amplifier](https://github.com/microsoft/amplifier). Load any bundle — a well-known name like `foundation` or `amplifier-dev`, a user-registered bundle, a git URI, or a local `.md` file. See the token cost broken down by behavior. Add or remove parts. Save a new bundle file that Amplifier can load.

The value: know exactly where your session's tokens come from, and trim the ones you don't need.

This is an experiment co-located in the [amplifier-foundation](https://github.com/microsoft/amplifier-foundation) repo. It does not ship in the Foundation wheel. The merge path, if Brian approves the API, is to move `amplifier_configurator/` into `amplifier_foundation/configurator/` — same pattern as `amplifier_foundation/session/`.

## Install

```bash
pip install "git+https://github.com/microsoft/amplifier-foundation@main#subdirectory=experiments/bundle-configurator"
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

# See token cost by behavior, sorted descending.
# A "behavior" is a named group of capabilities (tools, context files, etc.)
# that a bundle includes — e.g. "code-intel", "bug-hunter", "python-dev".
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

`source` accepts anything Amplifier can resolve: a well-known bundle name (`"foundation"`), a git URI, or a local file path.

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

**`BehaviorInfo`** — what a behavior contributes:
- `name: str` — short name (e.g. `"code-intel"`)
- `uri: str` — fully-qualified URI (e.g. `"foundation:code-intel"`)
- `parts: tuple[TrackedPart, ...]` — de-duplicated parts this behavior owns after conflict resolution
- `total_tokens: int` — token cost of this behavior's parts (context files + instructions)
- `depth: int` — 0 = directly included by the root bundle, 1 = included by a depth-0 behavior, etc.
- `include_chain: tuple[str, ...]` — path from root to this behavior

**`TrackedPart`** — a single element with its provenance (which behavior contributed it):
- `kind: PartKind` — `TOOL | HOOK | AGENT | PROVIDER | CONTEXT`
- `name: str` — module name or context key (e.g. `"tool-bash"`, `"python-dev:lsp-config"`)
- `source_behavior: str | None` — which behavior contributed it (`None` = root bundle)
- `tokens: int` — estimated token cost (non-zero only for `CONTEXT` parts)
- `config: dict` — the raw configuration dict from the bundle YAML

**`BundleDiff`** — the delta between two configurations:
- `added_parts / removed_parts: tuple[TrackedPart, ...]`
- `added_behaviors / removed_behaviors: tuple[str, ...]`
- `before_tokens / after_tokens / token_delta: int`

**`PartKind`** (enum): `TOOL`, `HOOK`, `AGENT`, `PROVIDER`, `CONTEXT`

### Errors

- `ConfiguratorError` — base error class
- `LoadError` — the bundle could not be loaded from the given source
- `DependencyError` — attempted to remove a required part (e.g. `tool-bash`, `tool-filesystem`, `tool-search`)

## What It Counts

The library measures **context files and instruction text** — the portions of a bundle that are loaded into the model's context window at session start. These are the parts you can control by editing the bundle.

It does not measure tool schemas. Tool schemas are generated at runtime by Foundation based on installed module versions; they are not present in the `.md` bundle file and cannot be measured statically.

For most optimization work, context files and instructions are the dominant cost. Tool schemas are relatively stable across sessions.

## How It Works

1. The library uses Foundation's `BundleRegistry` to load each included behavior separately, building a complete include tree.
2. Each behavior's contributions (tools, hooks, agents, context files) are attributed to that behavior using a last-write-wins, bottom-up rule: deeper (leaf) behaviors win over shallower ones on conflicts.
3. The resulting `ProvenanceMap` tracks every part with its source behavior, token cost, and original config dict. ("Provenance" here just means: which behavior contributed this part, and how it got there.)
4. All mutation methods (`remove_behavior`, `remove_part`, `add_behavior`) work on the `ProvenanceMap` directly and return a new `BundleConfigurator` without reloading from disk.
5. `save()` serializes the current state to YAML frontmatter + markdown body, referencing behaviors by URI rather than flattening them inline.

## Relationship to Runtime Configurator

This library edits **bundle files on disk**. It produces `.md` files that you can register and use in future sessions.

Brian's runtime configurator (in Foundation's session layer) toggles behaviors **within a running session** without touching the bundle file on disk.

They are complementary: this library produces the files that the runtime configurator operates on. A typical workflow: use this library to produce a lean bundle variant, then use the runtime configurator to dynamically adjust it mid-session.

## Status

Experiment. Not production-ready. API may change based on Brian's feedback.

Test coverage:
- 129 unit tests (no network calls, no Foundation install required)
- 6 integration tests (load real bundles via Foundation)
- 1 end-to-end round-trip test (`e2e_shadow_test.py`)

## Run Tests

```bash
# Install dependencies
pip install -e ".[dev]"

# Unit tests only (fast, no network calls)
pytest tests/ -k "not integration"

# Integration tests (loads real bundles via Foundation)
pytest tests/test_real_bundles.py -m integration -v

# End-to-end round trip
pytest tests/e2e_shadow_test.py -v
```

Or, using the Amplifier-managed Python:

```bash
~/.local/share/uv/tools/amplifier/bin/python -m pytest tests/ -k "not integration"
```
