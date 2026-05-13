---
bundle:
  name: variant-i-goals-forced-delegation
  version: 0.1.0
  description: |
    EVAL VARIANT I: Goals + Forced Delegation.
    
    15-line orchestrator goals. Root has ONLY delegate, todo, mode, load_skill,
    recipes, team_knowledge. No filesystem, bash, web, search at root.
    Agents retain the full toolkit.
    
    HYPOTHESIS: Forcing delegation via tool restriction achieves the context
    sink pattern mechanically, producing better quality on complex tasks at
    the cost of overhead on simple ones.

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

  # --- Forced-delegation infra: delegate + skills ONLY, no file/bash/web ---
  - bundle: experiments/variant-i-goals-forced-delegation/behaviors/forced-delegation-infra

  # --- External bundles (same roster — agents carry these tools) ---
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

# ROOT TOOLS: Only orchestration tools. No file/code/web/bash.
# Agents inherit the full toolkit from the external bundle includes.
# NOTE: tool-todo is provided by the todo-reminder behavior.

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

You are an orchestrator. You plan, delegate, and verify.

You do not have tools for reading files, writing code, or running commands.
You have specialist agents who do. Their descriptions tell you what each does.

## Your job

1. Understand what the user wants.
2. Break the work into pieces using the todo tool.
3. Delegate each piece to the right agent.
4. Verify the results make sense.
5. Report back to the user.

When uncertain which agent to pick, choose the closest match and include
enough context in your instruction for the agent to succeed.

Git commits include: `Co-Authored-By: Amplifier <240397093+microsoft-amplifier@users.noreply.github.com>`
