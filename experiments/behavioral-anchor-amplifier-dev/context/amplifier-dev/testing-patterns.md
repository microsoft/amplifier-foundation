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
│ 3. Digital Twin Universe  (confidence: ███░░)           │
│    (DTU) Validation — local repos via Gitea             │
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
| Core contract change | 3. DTU validation |
| Multi-repo coordinated change | 3. DTU validation |
| Breaking change | 3. DTU + careful push order |
| **Core release / any tag** | **5. Docker E2E smoke test** |
| Multi-repo coordinated change | 3. DTU + 5. E2E |

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

## Level 3: DTU Validation

For changes that span multiple repos or need isolation, use the **amplifier-tester** bundle. It launches a Digital Twin Universe with your local repos mirrored via Gitea, installs Amplifier from those mirrors, and runs validation checks.

Always delegate — don't drive the CLI directly:

```
delegate(agent="amplifier-tester:setup-digital-twin",
         instruction="Set up a DTU validating my changes to <repo-paths>",
         context_depth="all", context_scope="full")
```

For follow-up checks against an existing DTU:

```
delegate(agent="amplifier-tester:validator",
         instruction="Validate DTU <instance-id>: verify <what>",
         context_depth="recent", context_scope="agents")
```

### When to Use DTU Validation

- Core contract or kernel changes with module compatibility concerns
- Multi-repo coordinated changes
- Verifying `uv tool install` works end-to-end with your changes
- Destructive tests that shouldn't touch your real environment

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
```

### What It Does

1. Builds a wheel from local source (`maturin build`)
2. Creates a fresh Docker container (`python:3.12-slim`)
3. Installs `amplifier` from git (CLI + foundation from GitHub)
4. Overrides `amplifier-core` with the local wheel
5. Runs a real session: `amplifier run "Ask recipe author to run one of its example recipes"`
6. Detects crashes, tool failures, and timeouts
7. Reports PASS/FAIL

### When Required

| Change Type | Minimum Level |
|-------------|---------------|
| Core internal change | 2. Local override |
| Core contract change | 3. DTU validation |
| **Core release / any tag** | **5. Docker E2E smoke test** |
| Multi-repo coordinated change | 3. DTU + 5. E2E |

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
| Module protocol changes | DTU validation with affected modules |

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

# 2. DTU validation with dependent modules
# Delegate to amplifier-tester:setup-digital-twin with the paths to
# amplifier-core and each affected module. Then run tests via
# amplifier-digital-twin exec <instance-id> "pytest"

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

### DTU Validation Issues

For DTU-specific troubleshooting (Gitea mirror failures, profile generation issues, container provisioning), consult the `digital-twin-universe` skill or delegate to `digital-twin-universe:dtu-profile-builder`.

### Integration Test Failures

1. Check if dependency versions changed
2. Verify all local changes are reflected in the DTU's Gitea mirror
3. Test each repo individually first
4. Check push order - did you push dependencies first?
