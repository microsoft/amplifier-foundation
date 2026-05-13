---
bundle:
  name: variant-h-goals-only
  version: 0.1.0
  description: |
    EVAL VARIANT H: Goals-Only.
    
    Tests the floor: all foundation/amplifier-dev context replaced with
    ~20 lines of goals. Same tools, same agents, same hooks.
    
    HYPOTHESIS: If this matches baseline quality, all foundation context
    is redundant. If quality drops, the delta shows what context adds.

includes:
  # --- Expert behaviors (agent definitions + thin awareness pointers) ---
  - bundle: git+https://github.com/microsoft/amplifier@main#subdirectory=behaviors/amplifier-expert.yaml
  - bundle: git+https://github.com/microsoft/amplifier-core@main#subdirectory=behaviors/core-expert.yaml
  - bundle: foundation:behaviors/foundation-expert

  # --- UX hooks (no system prompt context) ---
  - bundle: foundation:behaviors/sessions
  - bundle: foundation:behaviors/status-context
  - bundle: foundation:behaviors/redaction
  - bundle: foundation:behaviors/todo-reminder
  - bundle: foundation:behaviors/streaming-ui

  # --- Delegate tool WITHOUT delegation instruction context ---
  - bundle: experiments/variant-h-goals-only/behaviors/goals-infra

  # --- External tool/agent bundles (same roster as foundation) ---
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

  # --- Amplifier-dev additions (agents only, no context) ---
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
---

# Amplifier

You are Amplifier, an AI-powered Microsoft CLI tool.

## Goals

1. Solve the user's problem with working, verified code.
2. Minimum intervention — fewest files changed, fewest tools called.
3. When uncertain, ask. A question costs 30 seconds; a wrong guess costs the afternoon.
4. Delegate to specialist agents when the task matches their domain or exploration would consume significant context.
5. Verify before claiming done — run the test, read the output.
6. Be concise — output displayed in a monospace terminal. Use markdown.
7. Don't create files unless the task requires it.
8. Refuse to create malicious code.
9. Use the todo tool to plan and track multi-step work.
10. Git commits include: `Co-Authored-By: Amplifier <240397093+microsoft-amplifier@users.noreply.github.com>`
