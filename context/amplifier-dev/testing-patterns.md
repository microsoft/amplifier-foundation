# Amplifier Testing Patterns

## The Testing Ladder

Each level provides more confidence but requires more setup:

```
┌─────────────────────────────────────────────────────────┐
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
amplifier shadow create \
  --local-source amplifier-core:/path/to/amplifier-core \
  --local-source amplifier-foundation:/path/to/amplifier-foundation

# Run Amplifier in the shadow
amplifier shadow run interactive

# Clean up
amplifier shadow destroy
```

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
amplifier shadow create \
  --local-source amplifier-core:~/repos/amplifier-core \
  --local-source amplifier-module-xyz:~/repos/amplifier-module-xyz

# 3. Test in shadow
amplifier shadow run interactive
# or
amplifier shadow run -- pytest tests/

# 4. If tests pass, commit and push your changes

# 5. Destroy shadow
amplifier shadow destroy
```

## Level 4: Push & CI

Full CI validation on GitHub:

1. Push branch
2. CI runs all tests
3. Integration tests with real dependencies
4. Cross-repo CI if configured

**When required**: Before merging any PR.

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
amplifier interactive
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
# 1. Validate YAML
amplifier bundle validate path/to/bundle.md

# 2. Load and check
amplifier bundle use path/to/bundle.md
amplifier interactive
# Verify agents available, context loaded

# 3. Test specific agents
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
