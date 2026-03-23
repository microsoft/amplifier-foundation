# Amplifier Testing Patterns

## The Testing Ladder

Each level provides more confidence but requires more setup:

```
┌─────────────────────────────────────────────────────────┐
│ 5. Docker E2E Smoke Test  (confidence: █████)           │
│    Built wheel in isolated container, real LLM calls    │
│    Tests: Does the artifact actually work end-to-end?   │
├─────────────────────────────────────────────────────────┤
│ 4. Push & CI              (confidence: ████░)           │
│    Full CI pipeline, all tests, real dependencies       │
├─────────────────────────────────────────────────────────┤
│ 3. Shadow Environment     (confidence: ███░░)           │
│    OS-isolated sandbox with local source snapshots      │
│    Tests: Does my change work with other local changes? │
├─────────────────────────────────────────────────────────┤
│ 2. Local Source Override  (confidence: ██░░░)           │
│    settings.yaml points to local checkout               │
│    Tests: Does Amplifier load my local module?          │
├─────────────────────────────────────────────────────────┤
│ 1. Unit Tests             (confidence: █░░░░)           │
│    pytest in the module/repo                            │
│    Tests: Does my code work in isolation?               │
└─────────────────────────────────────────────────────────┘
```

## When to Use Each Level

| Change Type | Minimum Testing Level |
|-------------|----------------------|
| Module internal change | 1. Unit tests |
| Module API change | 2. Local override |
| Core internal change | 2. Local override + sample modules |
| Core contract change | 3. Shadow environment |
| Multi-repo coordinated change | 3. Shadow environment |
| Breaking change | 3. Shadow + careful push order |
| **Core release / any tag** | **5. Docker E2E smoke test** |
| Multi-repo coordinated change | 3. Shadow + 5. E2E |

## Level 1: Unit Tests

Standard pytest in the repo:

```bash
cd amplifier-module-xyz
pytest tests/ -v

# With coverage
pytest tests/ --cov=amplifier_module_xyz
```

**When sufficient**: Internal changes that don't affect the public API.

## Level 2: Local Source Override

Use `.amplifier/settings.yaml` to point to local checkouts:

```yaml
# .amplifier/settings.yaml
sources:
  # Override a module to use local version
  amplifier-module-xyz:
    type: local
    path: /home/user/repos/amplifier-module-xyz
    
  # Override core (rarely needed)
  amplifier-core:
    type: local
    path: /home/user/repos/amplifier-core
```

Then run Amplifier normally - it will use your local sources.

**When sufficient**: Testing that Amplifier correctly loads and uses your changes.

## Level 3: Shadow Environment

For testing changes that span multiple repos or need isolation:

```bash
# Create shadow environment with local sources
amplifier-shadow create \
  --local ~/repos/amplifier-core:microsoft/amplifier-core \
  --local ~/repos/amplifier-foundation:microsoft/amplifier-foundation

# Execute commands in the shadow
amplifier-shadow exec <shadow-id> "uv tool install git+https://github.com/microsoft/amplifier"
amplifier-shadow exec <shadow-id> "amplifier run 'test my changes'"

# Clean up
amplifier-shadow destroy <shadow-id>
```

> **Note**: If `amplifier-shadow` is not installed, install it with:
> ```bash
> uv tool install git+https://github.com/microsoft/amplifier-bundle-shadow
> ```

### What Shadow Provides

1. **OS-level isolation** - Sandboxed filesystem (bubblewrap on Linux, sandbox-exec on macOS)
2. **Local source snapshots** - Your uncommitted changes captured as bare git repos
3. **Git URL rewriting** - `git clone https://github.com/microsoft/amplifier-core` fetches from local snapshot
4. **Full network access** - Can still reach PyPI, other dependencies

### When to Use Shadow

- Testing core changes with module compatibility
- Testing multi-repo changes together
- Verifying `uv tool install` works with your changes
- Destructive tests that shouldn't affect real environment

### Shadow Workflow

```bash
# 1. Make changes in your local repos (don't need to commit)

# 2. Create shadow with those changes
amplifier-shadow create \
  --local ~/repos/amplifier-core:microsoft/amplifier-core \
  --local ~/repos/amplifier-module-xyz:microsoft/amplifier-module-xyz \
  --name my-test

# 3. Test in shadow
amplifier-shadow exec my-test "uv tool install git+https://github.com/microsoft/amplifier"
amplifier-shadow exec my-test "amplifier run 'verify changes work'"
# or run tests
amplifier-shadow exec my-test "cd /workspace && pytest tests/"

# 4. If tests pass, commit and push your changes

# 5. Destroy shadow
amplifier-shadow destroy my-test
```

## Level 4: Push & CI

Full CI validation on GitHub:

1. Push branch
2. CI runs all tests
3. Integration tests with real dependencies
4. Cross-repo CI if configured

**When required**: Before merging any PR.

## Level 5: Docker E2E Smoke Test

The highest-confidence validation — tests the actual built artifact in a clean, isolated environment with real LLM calls.

```bash
# In amplifier-core:
./scripts/e2e-smoke-test.sh

# Cross-repo smoke test (validates local changes to CLI, foundation, modules, etc.):
./scripts/e2e-smoke-test.sh \
    --local-source ../amplifier-app-cli \
    --local-source ../amplifier-foundation \
    --local-source ../amplifier-bundle-modes/modules/hooks-mode \
    --local-source ../amplifier-bundle-modes/modules/tool-mode \
    --local-source ../amplifier-module-hooks-approval
```

**Note:** For bundles with modules in subdirectories, point `--local-source` at the module path (e.g., `../amplifier-bundle-modes/modules/hooks-mode`), not the bundle root. The module subdirectory must have its own `pyproject.toml`.

### What It Does

1. Builds a wheel from local source (`maturin build`)
2. Creates a fresh Docker container (`python:3.12-slim`)
3. Installs `amplifier` from git (CLI + foundation from GitHub)
4. Overrides `amplifier-core` with the local wheel
5. Overrides additional packages with `--local-source` repos (if any)
6. Runs a real session: `amplifier run "Ask recipe author to run one of its example recipes"`
7. Detects crashes, tool failures, and timeouts
8. Reports PASS/FAIL

The `--local-source` flag can be specified multiple times. Each path is copied into the container and installed with `pip install --force-reinstall --no-deps`, following the same override pattern used for the core wheel. This enables testing cross-repo changes before pushing.

### When Required

| Change Type | Minimum Level |
|-------------|---------------|
| Core internal change | 2. Local override |
| Core contract change | 3. Shadow environment |
| **Core release / any tag** | **5. Docker E2E smoke test** |
| Multi-repo coordinated change | 3. Shadow + 5. E2E |

### Why It Exists

Added after the v1.2.3/v1.2.4 incidents where ALL unit tests (549) and integration tests passed, but the actual installed wheel crashed on startup. The bugs were in the Rust↔Python FFI boundary and only manifested through the full CLI startup → tool dispatch → agent delegation path.

**Key insight:** Unit tests validate code correctness. E2E tests validate artifact correctness — that `maturin build` → wheel → `uv tool install` → `amplifier run` actually works.

## Cross-Repo Validation Requirements

### The Problem

Bugs in one repo may only manifest through another repo's code paths. Example: amplifier-core v1.2.3 shipped a Rust FFI bug (`__dict__` support missing on RustCoordinator) that passed all core unit tests but broke ALL tool dispatch — only catchable by running a real session through foundation's tool-delegate module and CLI's CommandProcessor.

### The Rule

Changes to **kernel contracts** (amplifier-core) MUST be validated through at least one full E2E path exercising: session init → tool dispatch → agent delegation → sub-session spawning.

### Cross-Repo Dependency Map

| If you change... | You MUST test through... |
|-----------------|--------------------------|
| Core coordinator/session | Full `amplifier run` E2E (Level 5) |
| Core tool dispatch | Foundation tool-delegate + real tool call |
| Foundation bundle loading | CLI `amplifier run --bundle` with real bundle |
| Module protocol changes | Shadow test with affected modules |

## Testing Specific Scenarios

### Testing a New Module

```bash
# 1. Unit tests
cd amplifier-module-new
pytest tests/

# 2. Local override test
# In a test project:
cat > .amplifier/settings.yaml << EOF
sources:
  amplifier-module-new:
    type: local
    path: /path/to/amplifier-module-new
EOF
amplifier  # Start interactive session (no subcommand = interactive mode)
# Verify module loads and works

# 3. Push and verify CI
```

### Testing Core Contract Change

```bash
# 1. Unit tests in core
cd amplifier-core
pytest tests/

# 2. Shadow test with dependent modules
amplifier shadow create \
  --local-source amplifier-core:. \
  --local-source amplifier-module-affected1:/path/to/module1 \
  --local-source amplifier-module-affected2:/path/to/module2

amplifier shadow run -- pytest  # Run all module tests in shadow

# 3. If passing, push core first
git push origin feat/contract-change
# Wait for merge

# 4. Then update and push modules
```

### Testing Bundle Composition

```bash
# 1. Test bundle loads directly (file paths work with `amplifier run --bundle`)
amplifier run --bundle ./path/to/bundle.md "test prompt"

# 2. Register and set as active (for repeated use)
amplifier bundle add ./path/to/bundle.md --name my-bundle
amplifier bundle use my-bundle
amplifier  # Start interactive session with the active bundle

# 3. Test specific agents (in interactive session)
> List available agents
> Use the new-agent to do X
```

## Debugging Test Failures

### Module Won't Load

1. Check module exports in `__init__.py`
2. Verify protocol compliance (Tool, Provider, etc.)
3. Check for missing dependencies
4. Use `amplifier --verbose` to see load errors

### Shadow Environment Issues

1. Verify local paths are correct
2. Check that repos have content (not empty)
3. On Linux, verify bubblewrap is installed: `which bwrap`
4. On macOS, sandbox-exec should be available by default

### Integration Test Failures

1. Check if dependency versions changed
2. Verify all local changes are captured in shadow
3. Test each repo individually first
4. Check push order - did you push dependencies first?
