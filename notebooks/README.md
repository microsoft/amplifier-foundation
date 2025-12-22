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
## Notebooks

### bundle_playground.ipynb

**Purpose**: Interactive bundle exploration

Paste your bundle YAML, see the resulting Bundle object and mount plan, experiment with changes.

**Good for**:
- Debugging bundle configurations
- Exploring mount plan structure
- Quick iteration on bundle design

### composition_explorer.ipynb

**Purpose**: Visualize composition merge rules

Define base and overlay bundles, see exactly how they merge.

**Good for**:
- Understanding merge behavior
- Testing composition scenarios
- Seeing how configs combine

### validation_checker.ipynb

**Purpose**: Test validation interactively

Paste bundles with errors, see validation messages with explanations.

**Good for**:
- Learning validation rules
- Debugging validation errors
- Testing bundle validity

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
