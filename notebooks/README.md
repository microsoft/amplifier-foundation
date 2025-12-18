# Amplifier Foundation Notebooks

**These are PLAYGROUNDS, not tutorials.**

For learning, see:
- `examples/` - How to use the APIs (copy/paste code)
- `docs/` - Why things work the way they do

Notebooks are for **interactive experimentation** after you understand the basics.

## Notebooks
- `01_hello_world.ipynb` - Fast path to load -> compose -> execute; edit prompt/provider.
- `02_validation_checker.ipynb` - Paste bundle YAML and see validation errors/warnings.
- `03_bundle_playground.ipynb` - Paste/load bundles and inspect mount plans interactively.
- `04_composition_explorer.ipynb` - Visualize composition merge rules side-by-side.
- `05_load_and_inspect.ipynb` - Load any bundle and inspect its mount plan sections.
- `06_custom_configuration.ipynb` - Toggle tool and streaming overlays to see composition effects.
- `07_custom_tool.ipynb` - Define and register a simple custom tool, then exercise it.
- `08_full_workflow.ipynb` - Full prepare -> create_session -> execute with your prompt.
- `09_multi_agent_system.ipynb` - Coordinate architect/implementer/reviewer agents through a handoff workflow.
- `10_provider_comparison.ipynb` - Run one prompt across multiple providers and compare timing/responses.
- `11_session_persistence.ipynb` - Save/resume session state to disk to continue later.

## Running Notebooks

### VS Code (Recommended)

Open notebooks in VS Code with the Jupyter extension:

1. Open VS Code in the `amplifier-foundation` directory
2. Open any `.ipynb` file
3. Select the Python interpreter from `.venv/` when prompted
4. Run cells interactively

### Jupyter Notebook (CLI)

If you prefer the browser-based Jupyter interface:

```bash
cd amplifier-foundation

# Install jupyter if not already installed
uv add --dev notebook

# Run jupyter
uv run jupyter notebook notebooks/
```

## Philosophy

Notebooks follow the "playground" principle:

1. **No new concepts** - They explore concepts taught elsewhere
2. **Link to sources** - Point to docs/examples for learning
3. **Ephemeral by design** - Your changes are for experimentation
4. **No duplication** - Don't repeat what examples show
