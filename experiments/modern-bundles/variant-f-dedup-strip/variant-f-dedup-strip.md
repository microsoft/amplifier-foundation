---
bundle:
  name: variant-f-dedup-strip
  version: 0.1.0
  description: |
    EVAL VARIANT F: Deduplicated + Stripped.
    
    Same architecture as amplifier-dev, ~50% content reduction by removing
    duplicate routing tables, tutorials modern models don't need, and
    implementation details the agent never uses.
    
    HYPOTHESIS: Removing noise improves signal-to-noise ratio. Quality
    should hold or improve because the model spends fewer tokens parsing
    redundant instructions and more tokens on the actual task.

includes:
  # --- Expert behaviors (agent definitions + thin awareness pointers) ---
  - bundle: git+https://github.com/microsoft/amplifier@main#subdirectory=behaviors/amplifier-expert.yaml
  - bundle: git+https://github.com/microsoft/amplifier-core@main#subdirectory=behaviors/core-expert.yaml
  - bundle: foundation:behaviors/foundation-expert

  # --- UX hooks ---
  - bundle: foundation:behaviors/sessions
  - bundle: foundation:behaviors/status-context
  - bundle: foundation:behaviors/redaction
  - bundle: foundation:behaviors/todo-reminder
  - bundle: foundation:behaviors/streaming-ui

  # --- Stripped delegation (replaces foundation:behaviors/agents) ---
  - bundle: experiments/variant-f-dedup-strip/behaviors/dedup-agents

  # --- External bundles (unchanged — their context is external to this experiment) ---
  - bundle: git+https://github.com/microsoft/amplifier-bundle-recipes@main#subdirectory=behaviors/recipes.yaml
  - bundle: git+https://github.com/microsoft/amplifier-bundle-design-intelligence@main#subdirectory=behaviors/design-intelligence.yaml
  - bundle: git+https://github.com/microsoft/amplifier-bundle-python-dev@main
  - bundle: git+https://github.com/microsoft/amplifier-bundle-skills@main#subdirectory=behaviors/skills.yaml
  - bundle: git+https://github.com/microsoft/amplifier-bundle-browser-tester@main#subdirectory=behaviors/browser-tester.yaml
  - bundle: git+https://github.com/microsoft/amplifier-bundle-superpowers@main#subdirectory=behaviors/superpowers-methodology.yaml
  - bundle: git+https://github.com/microsoft/amplifier-module-hook-shell@main#subdirectory=behaviors/hook-shell.yaml
  - bundle: git+https://github.com/microsoft/amplifier-module-tool-mcp@main#subdirectory=behaviors/mcp.yaml
  - bundle: git+https://github.com/microsoft/amplifier-bundle-filesystem@main#subdirectory=behaviors/apply-patch.yaml
  - bundle: git+https://github.com/microsoft/amplifier-bundle-routing-matrix@main

  # --- Amplifier-dev additions ---
  - bundle: git+https://github.com/microsoft/amplifier-bundle-amplifier-tester@main
  - bundle: foundation:behaviors/bundle-design

session:
  raw: true
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

agents:
  include:
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
    - foundation:ecosystem-expert

context:
  include:
    - experiments/variant-f-dedup-strip/context/dev-essentials.md
---

@experiments/variant-f-dedup-strip/context/system-core.md
