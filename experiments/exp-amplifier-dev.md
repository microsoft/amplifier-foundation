---
bundle:
  name: exp-amplifier-dev
  version: 0.1.0
  description: |
    EXPERIMENTAL amplifier-dev bundle with NEW delegate tool.
    
    Bundle for developing ON the Amplifier ecosystem with enhanced agent delegation:
    - Multi-repo coordination
    - Testing patterns  
    - Shadow environments
    - NEW delegate tool with two-param context
    
    To use this bundle:
      amplifier bundle add foundation:experiments/exp-amplifier-dev --name exp-amplifier-dev
      amplifier bundle use exp-amplifier-dev

includes:
  # Base experimental foundation (has new delegate tool)
  - bundle: foundation:experiments/exp-foundation
  # Amplifier dev behavior (agents + context)
  - bundle: foundation:behaviors/amplifier-dev
  # Shadow environments with Amplifier-specific helpers
  - bundle: foundation:behaviors/shadow-amplifier

# Development-specific session settings
session:
  raw_debug: true  # Enable raw debug events (debug: true inherited from exp-foundation)
---

# Experimental Amplifier Development Bundle

This is an **EXPERIMENTAL** bundle for Amplifier ecosystem development, using the new delegate tool.

## What's Included

| Component | Source |
|-----------|--------|
| Base capabilities | `exp-foundation` (with new delegate tool) |
| Amplifier dev behavior | Multi-repo workflows, testing patterns |
| Shadow environments | Amplifier-specific shadow helpers |

## New Delegate Tool Features

This bundle inherits the new delegate tool from exp-foundation:

### Two-Parameter Context Control

```python
# Clean slate - agent starts fresh
delegate(agent="foundation:explorer", context_depth="none", ...)

# Multi-agent collaboration - see other agent results
delegate(agent="foundation:architect", context_scope="agents", ...)

# Self-delegation with full context
delegate(agent="self", context_depth="all", context_scope="full", ...)
```

### Session Resume

```python
result = delegate(agent="foundation:explorer", instruction="Survey codebase")
# result.session_id = "abc123-def456-..._foundation:explorer"

# Resume with full session_id
delegate(session_id=result.session_id, instruction="Now check the tests")
```

## Use Cases

- Multi-repo development with coordinated changes
- Shadow testing of local Amplifier ecosystem changes
- Ecosystem-wide analysis and refactoring

## Feedback

Please report issues and feedback to help refine the delegate tool before wider rollout.
