# Amplifier Foundation

Ultra-thin mechanism layer for bundle composition in the Amplifier ecosystem.

Foundation provides the mechanisms for loading, composing, and resolving bundles from local and remote sources. It sits between `amplifier-core` (kernel) and applications, enabling teams to share and compose AI tool configurations.

## Quick Start

```bash
pip install amplifier-foundation
```

```python
import asyncio
from amplifier_foundation import load_bundle

async def main():
    # Load a bundle
    bundle = await load_bundle("./my-bundle")

    # Get mount plan for AmplifierSession
    mount_plan = bundle.to_mount_plan()
    print(f"Loaded: {bundle.name} v{bundle.version}")

asyncio.run(main())
```

**For complete examples, see:**

- `examples/01_load_and_inspect.py` - Loading bundles from various sources
- `examples/02_composition.py` - Bundle composition and merge rules
- `examples/03_sources_and_registry.py` - Git URLs and BundleRegistry
- `examples/04_full_workflow/` - Complete: prepare → create_session → execute

## Documentation

| Document                                  | Description                                     |
| ----------------------------------------- | ----------------------------------------------- |
| [CONCEPTS.md](docs/CONCEPTS.md)           | Mental model: bundles, composition, mount plans |
| [PATTERNS.md](docs/PATTERNS.md)           | Common patterns with code examples              |
| [URI_FORMATS.md](docs/URI_FORMATS.md)     | Source URI quick reference                      |
| [API_REFERENCE.md](docs/API_REFERENCE.md) | API index pointing to source files              |

**Code is the authoritative reference**: Each source file has comprehensive docstrings. Read `bundle.py`, `validator.py`, etc. directly or use `help(ClassName)`.

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
