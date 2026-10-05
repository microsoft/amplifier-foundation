---
bundle:
  name: anchors
  display_name: Anchors
  version: 0.3.0
  description: Lean principle-driven host with purposeful agents and a standard development tool roster.
  namespace_root: anchors

includes:
  - bundle: git+https://github.com/microsoft/amplifier-foundation@main#subdirectory=behaviors/streaming-ui.yaml
  - bundle: git+https://github.com/microsoft/amplifier-foundation@main#subdirectory=behaviors/status-context.yaml
  - bundle: git+https://github.com/microsoft/amplifier-foundation@main#subdirectory=behaviors/redaction.yaml
  - bundle: git+https://github.com/microsoft/amplifier-foundation@main#subdirectory=behaviors/logging.yaml
  - bundle: git+https://github.com/microsoft/amplifier-bundle-context-intelligence@main#subdirectory=behaviors/context-intelligence-logging.yaml
  - bundle: git+https://github.com/microsoft/amplifier-bundle-context-intelligence@main#subdirectory=behaviors/context-intelligence-navigation.yaml
  # Role routing remains a host choice, not part of the amp-dev capability.
  - bundle: git+https://github.com/microsoft/amplifier-bundle-routing-matrix@main#subdirectory=behaviors/routing.yaml

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
  - module: tool-todo
    source: git+https://github.com/microsoft/amplifier-module-tool-todo@main
  - module: tool-apply-patch
    source: git+https://github.com/microsoft/amplifier-bundle-filesystem@main#subdirectory=modules/tool-apply-patch
  - module: tool-delegate
    source: git+https://github.com/microsoft/amplifier-foundation@main#subdirectory=modules/tool-delegate
    config:
      features:
        self_delegation:
          enabled: true
        session_resume:
          enabled: true
        context_inheritance:
          enabled: true
          max_turns: 10
        provider_selection:
          enabled: true
      settings:
        exclude_tools: [tool-delegate]
        timeout: null
        max_llm_calls: null
  - module: tool-skills
    source: git+https://github.com/microsoft/amplifier-bundle-skills@main#subdirectory=modules/tool-skills
    config:
      skills:
        - "git+https://github.com/microsoft/amplifier-foundation@main#subdirectory=skills"
        - "git+https://github.com/microsoft/amplifier-bundle-skills@main#subdirectory=skills"
      visibility:
        enabled: false
  - module: tool-mode
    source: git+https://github.com/microsoft/amplifier-bundle-modes@main#subdirectory=modules/tool-mode
    config:
      gate_policy: "warn"
  - module: tool-recipes
    source: git+https://github.com/microsoft/amplifier-bundle-recipes@main#subdirectory=modules/tool-recipes
    config:
      session_dir: ~/.amplifier/projects/{project}/recipe-sessions
      auto_cleanup_days: 7

hooks:
  - module: hooks-todo-reminder
    source: git+https://github.com/microsoft/amplifier-module-hooks-todo-reminder@main
    config:
      inject_role: user
      priority: 10
  - module: hooks-todo-display
    source: git+https://github.com/microsoft/amplifier-foundation@main#subdirectory=modules/hooks-todo-display
    config:
      show_progress_bar: true
      show_border: true
  - module: hooks-session-naming
    source: git+https://github.com/microsoft/amplifier-foundation@main#subdirectory=modules/hooks-session-naming
    config:
      initial_trigger_turn: 2
      update_interval_turns: 5
  - module: hooks-mode
    source: git+https://github.com/microsoft/amplifier-bundle-modes@main#subdirectory=modules/hooks-mode
    config:
      search_paths: []
  - module: hooks-approval
    source: git+https://github.com/microsoft/amplifier-module-hooks-approval
    config:
      rules: []
      default_action: continue
      policy_driven_only: true

agents:
  include:
    - anchors:explorer
    - anchors:architect
    - anchors:builder
    - anchors:debugger
    - anchors:git-ops
    - anchors:researcher
---

@anchors:context/system.md