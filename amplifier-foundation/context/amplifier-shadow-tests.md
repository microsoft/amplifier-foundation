# Amplifier Shadow Testing Patterns

This document provides Amplifier-specific patterns for testing local changes in shadow environments.

## Common Amplifier Testing Scenarios

### Testing amplifier-core Changes

```python
# Create shadow with local amplifier-core
shadow.create(local_sources=["~/repos/amplifier-core:microsoft/amplifier-core"])

# Install amplifier - it will use YOUR local amplifier-core as a dependency
shadow.exec(shadow_id, "uv tool install git+https://github.com/microsoft/amplifier")

# Verify local code is installed (check commit in output)
# Look for: amplifier-core @ git+...@<your-snapshot-commit>

# Install providers
shadow.exec(shadow_id, "amplifier provider install -q")

# Test functionality
# Simple functionality test - just verify amplifier can start and respond
shadow.exec(shadow_id, 'amplifier --version')
```

### Testing amplifier-foundation Changes

```python
# Create shadow with local foundation
shadow.create(local_sources=["~/repos/amplifier-foundation:microsoft/amplifier-foundation"])

# Install amplifier 
shadow.exec(shadow_id, "uv tool install git+https://github.com/microsoft/amplifier")

# Verify bundle loading works with your changes
shadow.exec(shadow_id, "python -c 'from amplifier_foundation import load_bundle; print(\"OK\")'")
```

### Testing amplifier-app-cli Changes

```python
# Create shadow with local CLI
shadow.create(local_sources=["~/repos/amplifier-app-cli:microsoft/amplifier-app-cli"])

# Install amplifier (uses your local CLI)
shadow.exec(shadow_id, "uv tool install git+https://github.com/microsoft/amplifier")

# Test CLI changes
shadow.exec(shadow_id, "amplifier --version")
shadow.exec(shadow_id, "amplifier --help")
```

### Testing Multi-Repo Changes

When testing changes that span multiple Amplifier repos:

```python
# Create shadow with ALL affected repos
shadow.create(local_sources=[
    "~/repos/amplifier-core:microsoft/amplifier-core",
    "~/repos/amplifier-foundation:microsoft/amplifier-foundation",
    "~/repos/amplifier-app-cli:microsoft/amplifier-app-cli"
])

# Install - all dependencies use local snapshots
shadow.exec(shadow_id, "uv tool install git+https://github.com/microsoft/amplifier")

# Verify each repo's changes are included (check commits in install output)
```

### Testing Module Changes

For testing changes to specific modules (providers, tools, hooks):

```python
# Create shadow with local module
shadow.create(local_sources=["~/repos/amplifier-module-tool-xyz:microsoft/amplifier-module-tool-xyz"])

# Option 1: Install via bundle that uses the module
shadow.exec(shadow_id, "uv tool install git+https://github.com/microsoft/amplifier")

# Option 2: Install module directly for testing
shadow.exec(shadow_id, "uv pip install git+https://github.com/microsoft/amplifier-module-tool-xyz")

# Test module loads
shadow.exec(shadow_id, "python -c 'from amplifier_module_tool_xyz import mount; print(\"OK\")'")
```

### Testing Bundle Changes

For testing changes to bundles:

```python
# Create shadow with local bundle
shadow.create(local_sources=["~/repos/amplifier-bundle-xyz:microsoft/amplifier-bundle-xyz"])

# Install amplifier
shadow.exec(shadow_id, "uv tool install git+https://github.com/microsoft/amplifier")

# Add and use the bundle
shadow.exec(shadow_id, "amplifier bundle add git+https://github.com/microsoft/amplifier-bundle-xyz")
shadow.exec(shadow_id, "amplifier bundle use xyz")

# Test the bundle
shadow.exec(shadow_id, "amplifier run 'test the bundle'")
```

---

## Provider Setup in Shadow

Shadow environments need provider configuration to run Amplifier:

### Quick Setup (Manual settings.yaml)

```python
# Create settings with provider
shadow.exec(shadow_id, '''mkdir -p ~/.amplifier && cat > ~/.amplifier/settings.yaml << 'EOF'
providers:
  - module: provider-anthropic
    config:
      priority: 1
      model: claude-sonnet-4-5-20250514
EOF''')

# Install provider module
shadow.exec(shadow_id, "amplifier provider install anthropic -q")
```

### Using Environment Variables

Shadow automatically passes common API keys:
- `ANTHROPIC_API_KEY`
- `OPENAI_API_KEY`
- `AZURE_OPENAI_ENDPOINT`
- `GOOGLE_API_KEY`

Verify they're present:
```python
shadow.exec(shadow_id, "env | grep -E '(ANTHROPIC|OPENAI|AZURE|GOOGLE)_'")
```

---

## Verification Patterns

### Verify Local Code Is Being Used

```python
# Get snapshot commits from creation
result = shadow.create(local_sources=["~/repos/amplifier-core:microsoft/amplifier-core"])
expected_commit = result["snapshot_commits"]["microsoft/amplifier-core"]

# After installation, check what was installed
install_output = shadow.exec(shadow_id, "uv tool install git+https://github.com/microsoft/amplifier")

# Look for: amplifier-core @ git+https://...@{expected_commit}
# If commit matches, local code is being used!
```

### Verify Specific Code Changes

```python
# If you changed a specific function, test it directly
shadow.exec(shadow_id, """python -c '
from amplifier_core.session import Session
# Test your specific change
s = Session()
result = s.your_new_method()
print(f"New method works: {result}")
'""")
```

### Verify Import Paths

```python
# Check where modules are loaded from
shadow.exec(shadow_id, """python -c '
import amplifier_core
print(f"amplifier_core loaded from: {amplifier_core.__file__}")
'""")
```

---

## Troubleshooting Amplifier in Shadow

### "amplifier: command not found"

The CLI isn't installed as a tool:
```python
shadow.exec(shadow_id, "uv tool install git+https://github.com/microsoft/amplifier")
```

### "No providers mounted"

Create settings.yaml:
```python
shadow.exec(shadow_id, '''mkdir -p ~/.amplifier && cat > ~/.amplifier/settings.yaml << 'EOF'
providers:
  - module: provider-anthropic
    config:
      priority: 1
EOF''')
shadow.exec(shadow_id, "amplifier provider install anthropic -q")
```

### Import Errors for amplifier_core

Package not installed:
```python
shadow.exec(shadow_id, "uv pip install git+https://github.com/microsoft/amplifier-core")
```

### Commit Mismatch

If installed commit differs from snapshot commit, this may be expected:
- Uncommitted changes in your working directory get snapshotted as a new commit
- The snapshot commit will differ from HEAD

Check your working directory status:
```bash
cd ~/repos/amplifier-core
git status  # Shows if you have uncommitted changes
```

### Provider Module Not Found

Install the specific provider:
```python
shadow.exec(shadow_id, "amplifier provider install anthropic -q")
# Or for other providers:
shadow.exec(shadow_id, "amplifier provider install openai -q")
```

---

## Integration Test Pattern

Full integration test for Amplifier changes:

```python
# 1. Create shadow with all local changes
shadow.create(
    local_sources=[
        "~/repos/amplifier-core:microsoft/amplifier-core",
        "~/repos/amplifier-foundation:microsoft/amplifier-foundation"
    ],
    name="integration-test"
)

# 2. Install Amplifier
shadow.exec(shadow_id, "uv tool install git+https://github.com/microsoft/amplifier")

# 3. Configure provider
shadow.exec(shadow_id, '''mkdir -p ~/.amplifier && cat > ~/.amplifier/settings.yaml << 'EOF'
providers:
  - module: provider-anthropic
    config:
      priority: 1
EOF''')
shadow.exec(shadow_id, "amplifier provider install anthropic -q")

# 4. Run integration test - verify amplifier CLI works
result = shadow.exec(shadow_id, 'amplifier --version')

# 5. Check result
if result["exit_code"] == 0 and "hello" in result["stdout"].lower():
    print("Integration test PASSED")
else:
    print(f"Integration test FAILED: {result['stderr']}")

# 6. Cleanup
shadow.destroy(shadow_id)
```

---

## Reference

For generic shadow documentation: @shadow:context/shadow-instructions.md
For smoke test rubric: See `shadow-smoke-test` agent instructions
