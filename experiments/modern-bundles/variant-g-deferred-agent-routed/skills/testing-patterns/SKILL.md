---
skill:
  name: testing-patterns
  description: Amplifier testing patterns — unit tests, local override, DTU validation, E2E smoke test. Load when testing Amplifier changes.
  version: 1.0.0
---

# Amplifier Testing Patterns

## Level 2: Local Source Override

```yaml
# .amplifier/settings.yaml
sources:
  amplifier-module-xyz:
    type: local
    path: /home/user/repos/amplifier-module-xyz
```

## Level 3: DTU Validation

Always delegate:
```python
delegate(agent="amplifier-tester:setup-digital-twin",
         instruction="Validate my changes to <repo-paths>",
         context_depth="all", context_scope="full")
```

## Level 5: Docker E2E Smoke Test

```bash
cd amplifier-core
./scripts/e2e-smoke-test.sh
```

Builds wheel, installs in fresh Docker container, runs real LLM session. Required before any core release tag.

## Test Specific Scenarios

```bash
# Module: unit tests
pytest tests/ -v

# Bundle: load and verify
amplifier run --bundle ./path/to/bundle.md "test prompt"

# Core contract change: DTU + E2E
```
