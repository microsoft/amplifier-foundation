---
bundle:
  name: exp-foundation
  version: 0.1.0
  description: |
    EXPERIMENTAL foundation bundle with NEW delegate tool.
    
    This bundle uses the enhanced agent delegation system with:
    - Two-parameter context inheritance (context_depth + context_scope)
    - Short session ID resolution (6+ character prefixes)
    - Fixed tool inheritance (explicit declarations always honored)
    
    USE FOR: Testing and validating the new delegate tool before wider rollout.
    
    To use this bundle:
      amplifier bundle add foundation:experiments/exp-foundation --name exp-foundation
      amplifier bundle use exp-foundation

includes:
  # Ecosystem expert behaviors (provides @amplifier: and @core: namespaces)
  - bundle: git+https://github.com/microsoft/amplifier@main#subdirectory=behaviors/amplifier-expert.yaml
  - bundle: git+https://github.com/microsoft/amplifier-core@main#subdirectory=behaviors/core-expert.yaml
  # Foundation expert behavior
  - bundle: foundation:behaviors/foundation-expert
  # Foundation behaviors
  - bundle: foundation:behaviors/sessions
  - bundle: foundation:behaviors/status-context
  - bundle: foundation:behaviors/redaction
  - bundle: foundation:behaviors/todo-reminder
  - bundle: foundation:behaviors/streaming-ui
  # NEW: agents behavior with delegate tool (instead of tasks behavior)
  - bundle: foundation:behaviors/agents
  # External bundles
  - bundle: git+https://github.com/microsoft/amplifier-bundle-recipes@main#subdirectory=behaviors/recipes.yaml
  - bundle: git+https://github.com/microsoft/amplifier-bundle-design-intelligence@main#subdirectory=behaviors/design-intelligence.yaml
  - bundle: git+https://github.com/microsoft/amplifier-bundle-python-dev@main
  - bundle: git+https://github.com/microsoft/amplifier-bundle-shadow@main
  - bundle: git+https://github.com/microsoft/amplifier-module-tool-skills@main#subdirectory=behaviors/skills.yaml
  - bundle: git+https://github.com/microsoft/amplifier-module-hook-shell@main#subdirectory=behaviors/hook-shell.yaml


session:
  debug: true
  orchestrator:
    module: loop-streaming
    source: git+https://github.com/microsoft/amplifier-module-loop-streaming@main
    config:
      extended_thinking: true
  context:
    module: context-simple
    source: git+https://github.com/microsoft/amplifier-module-context-simple@main
    config:
      max_tokens: 300000
      compact_threshold: 0.8
      auto_compact: true

tools:
  - module: tool-filesystem
    source: git+https://github.com/microsoft/amplifier-module-tool-filesystem@main
  - module: tool-bash
    source: git+https://github.com/microsoft/amplifier-module-tool-bash@main
  - module: tool-web
    source: git+https://github.com/microsoft/amplifier-module-tool-web@main
  - module: tool-search
    source: git+https://github.com/microsoft/amplifier-module-tool-search@main
  # NOTE: delegate tool comes from agents behavior, task tool is NOT included

agents:
  include:
    # Note: amplifier-expert, core-expert, and foundation-expert come via included behaviors above
    - foundation:bug-hunter
    - foundation:explorer
    - foundation:file-ops
    - foundation:git-ops
    - foundation:integration-specialist
    - foundation:modular-builder
    - foundation:post-task-cleanup
    - foundation:security-guardian
    - foundation:test-coverage
    - foundation:web-research
    - foundation:zen-architect
---

# Experimental Foundation Bundle

This is an **EXPERIMENTAL** bundle for testing the new delegate tool.

## What's Different

| Feature | Standard Foundation | exp-foundation |
|---------|--------------------|--------------------|
| Delegation tool | `task` (legacy) | `delegate` (new) |
| Context params | `inherit_context` | `context_depth` + `context_scope` |
| Session resume | Full UUID only | Short ID (6+ chars) |
| Tool inheritance | Bug: exclusions override declarations | Fixed: declarations honored |

## New Delegate Tool Features

### Two-Parameter Context

```python
# HOW MUCH context to inherit
context_depth: "none" | "recent" | "all"

# WHICH content to include
context_scope: "conversation" | "agents" | "full"
```

### Short Session IDs

```python
result = delegate(agent="foundation:explorer", instruction="...")
# result.short_id = "a3f2b8"

# Resume with short ID
delegate(session_id="a3f2b8", instruction="Continue...")
```

## Feedback

Please report issues and feedback to help refine the delegate tool before wider rollout.

@foundation:context/shared/common-system-base.md
