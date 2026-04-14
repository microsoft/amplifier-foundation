# Amplifier Foundation

Foundational library for the Amplifier ecosystem: bundle composition, utilities, and reference content.

Foundation provides:
- **Bundle System** - Load, compose, validate, and resolve bundles from local and remote sources
- **@Mention System** - Parse and resolve `@namespace:path` references in instructions
- **Utilities** - YAML/frontmatter I/O, dict merging, path handling, caching
- **Reference Content** - Reusable providers, agents, behaviors, and context files

## Quick Start

```bash
pip install git+https://github.com/microsoft/amplifier-foundation
```

### Load, Compose, and Execute

```python
import asyncio
from amplifier_foundation import load_bundle

async def main():
    # Load foundation bundle and a provider
    foundation = await load_bundle("git+https://github.com/microsoft/amplifier-foundation@main")
    provider = await load_bundle("./providers/anthropic.yaml")

    # Compose bundles (later overrides earlier)
    composed = foundation.compose(provider)

    # Prepare: resolves module sources, downloads if needed
    prepared = await composed.prepare()

    # Create session and execute
    async with await prepared.create_session() as session:
        response = await session.execute("Hello! What can you help me with?")
        print(response)

asyncio.run(main())
```

For the complete workflow with provider selection and advanced features, see [`examples/04_full_workflow/`](examples/04_full_workflow/).

### Use Utilities Directly

```python
from amplifier_foundation import (
    # I/O
    read_yaml, write_yaml, parse_frontmatter, read_with_retry, write_with_retry,
    # Dict operations
    deep_merge, merge_module_lists, get_nested, set_nested,
    # Path handling
    parse_uri, normalize_path, find_files, find_bundle_root,
    # @Mentions
    parse_mentions, load_mentions,
    # Caching
    SimpleCache, DiskCache,
    # Session capabilities
    get_working_dir, set_working_dir, WORKING_DIR_CAPABILITY,
)

# Parse git URIs
parsed = parse_uri("git+https://github.com/org/repo@main#subdirectory=bundles/dev")
# → ParsedURI(scheme='git+https', host='github.com', path='/org/repo', ref='main', subpath='bundles/dev')

# Deep merge dicts (later wins)
result = deep_merge(base_config, overlay_config)

# Parse markdown frontmatter
frontmatter, body = parse_frontmatter(markdown_content)

# Find files recursively
md_files = find_files(Path("docs"), "**/*.md")
```

## What's Included

### Bundle System (`bundle.py`, `registry.py`, `validator.py`)

| Export | Purpose |
|--------|---------|
| `Bundle` | Core class - load, compose, validate bundles |
| `load_bundle(uri)` | Load bundle from local path or git URL |
| `BundleRegistry` | Track loaded bundles, check for updates |
| `validate_bundle()` | Validate bundle structure |

### @Mention System (`mentions/`)

| Export | Purpose |
|--------|---------|
| `parse_mentions(text)` | Extract `@namespace:path` references |
| `load_mentions(text, resolver)` | Resolve and load mentioned files |
| `BaseMentionResolver` | Base class for custom resolvers |
| `ContentDeduplicator` | Prevent duplicate content loading |

### Utilities

| Module | Exports | Purpose |
|--------|---------|---------|
| `io/` | `read_yaml`, `write_yaml`, `parse_frontmatter`, `read_with_retry`, `write_with_retry` | File I/O with cloud sync retry |
| `dicts/` | `deep_merge`, `merge_module_lists`, `get_nested`, `set_nested` | Dict manipulation |
| `paths/` | `parse_uri`, `normalize_path`, `find_files`, `find_bundle_root` | Path and URI handling |
| `cache/` | `SimpleCache`, `DiskCache` | In-memory and disk caching (apps can extend with TTL) |

### Reference Content (Co-located)

This repo also contains reference bundle content for common configurations:

| Path | Content |
|------|---------|
| `bundle.md` | **Main foundation bundle** - provider-agnostic base with streaming, tools, behaviors |
| `providers/` | Provider configurations (anthropic, openai, azure-openai, gemini, ollama) |
| `agents/` | Reusable agent definitions |
| `behaviors/` | Behavioral configurations (logging, redaction, status, etc.) |
| `context/` | Shared context files |
| `bundles/` | Complete bundle examples |

**Note**: This content is just files - discovered and loaded like any other bundle. See [PATTERNS.md](docs/PATTERNS.md) for usage examples.

## Examples

| Example | Description |
|---------|-------------|
| `examples/01_hello_world.py` | Minimal working example |
| `examples/04_load_and_inspect.py` | Loading bundles from various sources |
| `examples/05_composition.py` | Bundle composition and merge rules |
| `examples/06_sources_and_registry.py` | Git URLs and BundleRegistry |
| `examples/07_full_workflow.py` | Complete: prepare → create_session → execute |

See [`examples/README.md`](examples/README.md) for the full catalog of 20+ examples.

## Documentation

| Document | Description |
|----------|-------------|
| [BUNDLE_GUIDE.md](docs/BUNDLE_GUIDE.md) | Complete bundle authoring guide |
| [AGENT_AUTHORING.md](docs/AGENT_AUTHORING.md) | Agent creation and context sink pattern |
| [CONCEPTS.md](docs/CONCEPTS.md) | Mental model: bundles, composition, mount plans |
| [PATTERNS.md](docs/PATTERNS.md) | Common patterns with code examples |
| [URI_FORMATS.md](docs/URI_FORMATS.md) | Source URI quick reference |
| [API_REFERENCE.md](docs/API_REFERENCE.md) | API index pointing to source files |

**Code is authoritative**: Each source file has comprehensive docstrings. Use `help(ClassName)` or read source directly.

## For Bundle Authors

This README covers the Python library API. **For bundle authoring guidance:**

- **[BUNDLE_GUIDE.md](docs/BUNDLE_GUIDE.md)** - Complete authoring guide (thin bundle pattern, behaviors, composition)
- **[AGENT_AUTHORING.md](docs/AGENT_AUTHORING.md)** - Agent creation and the context sink pattern
- **`foundation:foundation-expert`** - Expert agent for guidance when building bundles
- **Canonical example**: [amplifier-bundle-recipes](https://github.com/microsoft/amplifier-bundle-recipes) - demonstrates proper structure

## Philosophy

Foundation follows Amplifier's core principles:

- **Mechanism, not policy**: Provides loading/composition mechanisms. Apps decide which bundles to use.
- **Ruthless simplicity**: One concept (bundle), one mechanism (`includes:` + `compose()`).
- **Text-first**: YAML/Markdown formats are human-readable, diffable, versionable.
- **Composable**: Small bundles compose into larger configurations.

This library is pure mechanism. It doesn't know about specific bundles. The co-located reference content is just content - discovered and loaded like any other bundle.

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
