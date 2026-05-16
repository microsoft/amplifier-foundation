---
name: python-development
description: "Use when working on Python code in the Amplifier ecosystem -- project conventions, ruff/pyright configuration, package layout, testing entry points."
---

> *Loaded on demand. The full guidance below is not in your default context --
> you reached for this skill because the situation called for it. Treat the
> sections that follow as authoritative for the duration of this task.*


This bundle provides comprehensive Python development capabilities for Amplifier.

## Available Tools

### python_check

Run code quality checks on Python files or code content.

```
python_check(paths=["src/"])           # Check a directory
python_check(paths=["src/main.py"])    # Check a specific file
python_check(content="def foo(): ...")  # Check code string
python_check(paths=["src/"], fix=True)  # Auto-fix issues
```

**Checks performed:**
- **ruff format**: Code formatting (PEP 8 style)
- **ruff lint**: Linting rules (pycodestyle, pyflakes, isort, bugbear, comprehensions, etc.)
- **pyright**: Static type checking
- **stub detection**: TODOs, placeholders, incomplete code

### Code Intelligence (via python-dev:code-intel)

For semantic code understanding (tracing calls, finding definitions, understanding types), delegate to `python-dev:code-intel`. That agent is the LSP specialist with Pyright expertise.

| Capability | When to delegate |
|------------|-----------------|
| Type info and docstrings | Understanding signatures and inferred types |
| Go to definition | Finding where a symbol is defined |
| Find references | Finding all usages of a symbol |
| Incoming/outgoing calls | Tracing call hierarchies |
| Multi-step navigation | Complex cross-module code exploration |

## Automatic Checking Hook

When enabled, Python files are automatically checked after write/edit operations.

**Behavior:**
- Triggers on `write_file`, `edit_file`, and similar tools
- Checks `*.py` files only
- Runs lint and type checks (fast subset)
- Injects issues into agent context for awareness

**Configuration** (in `pyproject.toml`):
```toml
[tool.amplifier-python-dev.hook]
enabled = true
file_patterns = ["*.py"]
report_level = "warning"  # error | warning | info
auto_inject = true
```

## CLI Usage

For standalone use outside Amplifier:

```bash
# Install and run
uvx --from git+https://github.com/microsoft/amplifier-bundle-python-dev amplifier-python-check src/

# With options
amplifier-python-check src/ --fix           # Auto-fix issues
amplifier-python-check src/ --format=json   # JSON output for CI
amplifier-python-check src/ --no-types      # Skip type checking
```

## Configuration

Configure via `pyproject.toml`:

```toml
[tool.amplifier-python-dev]
# Enable/disable specific checks
enable_ruff_format = true
enable_ruff_lint = true
enable_pyright = true
enable_stub_check = true

# Paths to exclude
exclude_patterns = [
    ".venv/**",
    "__pycache__/**",
    "build/**",
]

# Behavior
fail_on_warning = false  # Exit code 1 on warnings
auto_fix = false         # Auto-fix by default

[tool.amplifier-python-dev.hook]
enabled = true
file_patterns = ["*.py"]
report_level = "warning"
auto_inject = true
```

## Prerequisites

The `python_check` tool invokes `ruff` and `pyright` as Python modules via `sys.executable -m <tool>`. Both must be installed as Python packages in the **active environment** (not just on PATH).

**Verify installation:**
```bash
python -m ruff --version && python -m pyright --version
```

**Install if missing:**
```bash
uv add ruff pyright        # or: pip install ruff pyright
```

> **Why Python modules?** The bundle uses `sys.executable -m ruff` to ensure it runs the tools from the same Python environment as the checker itself. Having `ruff` on PATH via Homebrew or pipx is not sufficient — it must be importable by the active interpreter.

### Troubleshooting

| Symptom | Fix |
|---------|-----|
| `TOOL-NOT-FOUND: ruff not found` | `uv add ruff` |
| `TOOL-NOT-FOUND: pyright not found` | `uv add pyright` |
| Checks return no issues but ruff isn't running | Verify with `python -m ruff --version` |
| `pyright-langserver` not found (LSP side) | `npm install -g pyright` |

## Best Practices

See @python-dev:context/PYTHON_BEST_PRACTICES.md for the full development philosophy.

**Key points:**
1. Run `python_check` after writing Python code
2. Fix issues immediately - don't accumulate debt
3. Delegate to `python-dev:code-intel` to understand code before modifying
4. Type hints at boundaries, not everywhere
5. Readability over cleverness

