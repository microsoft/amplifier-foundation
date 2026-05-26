---
bundle:
  name: minimal-delegate-foundation
  version: 0.1.0
  description: |
    Constrained orchestrator experimental bundle. Token budget 64k.

    Has ONLY meta-tools: delegate, todo, load_skill (mode is platform-native).
    No direct tools — every real action must route through a specialized agent.
    Use this bundle to study how a minimum-instruction orchestrator performs
    under tight tooling constraints when it can only orchestrate other agents.

    Baseline for the optimize_bundle experimental campaign:
    - Hypothesis: constrained orchestrator achieves comparable outcomes with
      less token consumption (never reads files itself) and more reliable
      behavior (each agent has specialized context).

    To use this bundle:
      amplifier bundle add 'git+https://github.com/microsoft/amplifier-foundation@main#subdirectory=experiments/minimal-delegate/minimal-delegate-foundation.md' --name minimal-delegate-foundation
      amplifier bundle use minimal-delegate-foundation

session:
  orchestrator:
    module: loop-streaming
    source: git+https://github.com/microsoft/amplifier-module-loop-streaming@main
  context:
    module: context-simple
    source: git+https://github.com/microsoft/amplifier-module-context-simple@main
    config:
      max_tokens: 64000

providers:
  - module: provider-anthropic
    source: git+https://github.com/microsoft/amplifier-module-provider-anthropic@main

# ONLY meta-tools — these gate access to other capabilities.
# CRITICAL: NO direct tools. Specifically absent:
#   tool-filesystem (read_file, write_file, edit_file, glob)
#   tool-bash
#   tool-web (web_fetch, web_search)
#   tool-search (grep)
#   tool-lsp
#   tool-python-check
#   Any other tool that performs real work directly.
tools:
  - module: tool-delegate
    source: git+https://github.com/microsoft/amplifier-foundation@main#subdirectory=modules/tool-delegate
    config:
      features:
        self_delegation:
          # Disabled at parent level: orchestrator with only todo+delegate
          # spawning itself produces a sub-instance with no real tools.
          enabled: false
        session_resume:
          enabled: true
        context_inheritance:
          enabled: true
          max_turns: 10
        provider_selection:
          enabled: true
      settings:
        exclude_tools: [tool-delegate]
  - module: tool-todo
    source: git+https://github.com/microsoft/amplifier-module-tool-todo@main
  - module: tool-skills
    source: git+https://github.com/microsoft/amplifier-bundle-skills@main#subdirectory=modules/tool-skills

agents:
  include:
    # Foundation core agents — cover the full task surface:
    - foundation:explorer          # multi-file read-only reconnaissance
    - foundation:file-ops          # read, write, edit, search files
    - foundation:web-research      # web search and fetch
    - foundation:git-ops           # git, GitHub CLI, PRs
    - foundation:bug-hunter        # root-cause analysis, debugging
    - foundation:modular-builder   # implementation from spec
    - foundation:zen-architect     # design, architecture, spec writing
    - foundation:session-analyst   # review session history and outcomes
    - foundation:test-coverage     # tests, coverage, verification
    - foundation:security-guardian # security review and audit
    - foundation:ecosystem-expert  # Amplifier ecosystem knowledge
    - foundation:integration-specialist  # cross-repo integration work
  # Python-specific specialists (for code-heavy scenarios):
  code-intel:
    name: code-intel
    description: >
      Code intelligence specialist. LSP operations: go-to-definition,
      find-references, symbol lookup, call hierarchies, diagnostics.
      Use for: finding where something is defined, tracing all callers,
      understanding type signatures, cross-file semantic navigation.
    source: git+https://github.com/microsoft/amplifier-bundle-python-dev@main
  python-dev:
    name: python-dev
    description: >
      Python development specialist. Implementation, debugging, testing,
      type-checked code, packaging, and Python ecosystem patterns.
      Use for: writing Python code, fixing Python bugs, reviewing Python
      implementations, pyright/ruff compliance, pytest authoring.
    source: git+https://github.com/microsoft/amplifier-bundle-python-dev@main
---

You are a session orchestrator. You have NO direct tools for file access, command execution, web access, or any real work. Your only capabilities:

- `delegate` — spawn a specialized agent for any task
- `todo` — track multi-step plans
- `mode` — switch to a workflow mode (some modes grant gated tool access)
- `load_skill` — load knowledge packages for context

For all real work, delegate to an agent. Use `todo` to plan multi-step tasks before delegating. Use `mode` or `load_skill` when a workflow or knowledge package fits the task.
