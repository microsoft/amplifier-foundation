---
bundle:
  name: foundation
  version: 2.0.0
  description: |
    Foundation bundle with enhanced delegate tool.
    
    Features:
    - Two-parameter context inheritance (context_depth + context_scope)
    - Session resume (use full session_id from delegate calls)
    - Fixed tool inheritance (explicit declarations always honored)
    - Multi-agent collaboration patterns

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
  # Agent orchestration with delegate tool
  - bundle: foundation:behaviors/agents
  # External bundles
  - bundle: git+https://github.com/microsoft/amplifier-bundle-recipes@main#subdirectory=behaviors/recipes.yaml
  - bundle: git+https://github.com/microsoft/amplifier-bundle-design-intelligence@main#subdirectory=behaviors/design-intelligence.yaml
  - bundle: git+https://github.com/microsoft/amplifier-bundle-python-dev@main
  - bundle: git+https://github.com/microsoft/amplifier-bundle-shadow@main
  - bundle: git+https://github.com/microsoft/amplifier-module-tool-skills@main#subdirectory=behaviors/skills.yaml
  - bundle: git+https://github.com/microsoft/amplifier-module-hook-shell@main#subdirectory=behaviors/hook-shell.yaml
  - bundle: git+https://github.com/microsoft/amplifier-module-tool-mcp@main#subdirectory=behaviors/mcp.yaml


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
  # NOTE: delegate tool comes from agents behavior

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

# Foundation Bundle v2.0

This bundle provides the standard Amplifier foundation with the enhanced delegate tool for agent orchestration.

## Key Features

| Feature | Description |
|---------|-------------|
| **Delegate tool** | Two-parameter context control (depth + scope) |
| **Session resume** | Continue agent sessions with full session_id |
| **Tool inheritance** | Explicit declarations always honored |
| **MCP support** | Model Context Protocol integration (configure via mcp.json) |

## Delegate Tool

```python
# Context depth: HOW MUCH to inherit
context_depth: "none" | "recent" | "all"

# Context scope: WHICH content to include
context_scope: "conversation" | "agents" | "full"
```

## MCP Configuration

To use MCP servers, create `.amplifier/mcp.json`:

```json
{
  "mcpServers": {
    "your-server": {
      "url": "https://example.com/mcp"
    }
  }
}
```

@foundation:context/shared/common-system-base.md
