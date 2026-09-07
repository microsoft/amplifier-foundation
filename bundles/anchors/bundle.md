---
bundle:
  name: anchors
  version: 0.2.0
  description: |
    Lean bundle driven by a small set of behavioral principles.
    A minimal system prompt, thin purposeful agents, and a standard tool roster.
    Explores how far concise behavior-shaping -- principles loaded once at the
    head of the system prompt -- can carry an Amplifier session at a fraction of
    the usual context cost.

includes:
  # Free-cost UX hooks (no context injection, only runtime behavior)
  - bundle: git+https://github.com/microsoft/amplifier-foundation@main#subdirectory=behaviors/streaming-ui.yaml
  - bundle: git+https://github.com/microsoft/amplifier-foundation@main#subdirectory=behaviors/status-context.yaml
  - bundle: git+https://github.com/microsoft/amplifier-foundation@main#subdirectory=behaviors/redaction.yaml
  - bundle: git+https://github.com/microsoft/amplifier-foundation@main#subdirectory=behaviors/logging.yaml
  # Context Intelligence: session event capture + local-JSONL navigation
  - bundle: git+https://github.com/microsoft/amplifier-bundle-context-intelligence@main#subdirectory=behaviors/context-intelligence-logging.yaml
  - bundle: git+https://github.com/microsoft/amplifier-bundle-context-intelligence@main#subdirectory=behaviors/context-intelligence-navigation.yaml
  # Model routing: the hook that makes every agent's `model_role` mean something.
  #
  # Every anchors agent declares a role (explorer: [general, fast]; architect:
  # reasoning; ...). Without hooks-routing those declarations are dead config
  # and every sub-agent silently inherits the parent's provider -- measured
  # 2026-09-07: a session on anchors-amp-dev spawned explorer with
  # `provider_preferences: null` and ran it on the parent's opus while
  # `amplifier routing show` said "balanced ... active" for every role.
  #
  # amplifier-app-cli ALSO composes this behavior on every session (routing is
  # app-level policy there, like skills/wayfinder). Including it here too is
  # deliberate, not redundant: anchors does not need the CLI to work, and a
  # host that mounts anchors without app-cli must still route. Compose dedupes
  # by module id, so the two never produce a second hook. Cost: one ~750-byte
  # context file (the model_role contract) and the role-definitions skill.
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
      max_tokens: 300000
      compact_threshold: 0.8
      auto_compact: true

tools:
  # Core tools (inherited by all sub-agents)
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

  # Agent delegation
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

  # Skills (user-invocable skills callable via /command + load_skill; auto-injection off to save tokens)
  - module: tool-skills
    source: git+https://github.com/microsoft/amplifier-bundle-skills@main#subdirectory=modules/tool-skills
    config:
      skills:
        - "git+https://github.com/microsoft/amplifier-foundation@main#subdirectory=skills"
        - "git+https://github.com/microsoft/amplifier-bundle-skills@main#subdirectory=skills"
      visibility:
        enabled: false

  # Mode switching
  - module: tool-mode
    source: git+https://github.com/microsoft/amplifier-bundle-modes@main#subdirectory=modules/tool-mode
    config:
      gate_policy: "warn"

  # Recipes
  - module: tool-recipes
    source: git+https://github.com/microsoft/amplifier-bundle-recipes@main#subdirectory=modules/tool-recipes
    config:
      session_dir: ~/.amplifier/projects/{project}/recipe-sessions
      auto_cleanup_days: 7

hooks:
  # Todo tracking
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

  # Session naming
  - module: hooks-session-naming
    source: git+https://github.com/microsoft/amplifier-foundation@main#subdirectory=modules/hooks-session-naming
    config:
      initial_trigger_turn: 2
      update_interval_turns: 5

  # Mode enforcement
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
