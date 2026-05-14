---
bundle:
  name: variant-g-deferred-agent-routed
  version: 0.1.0
  description: |
    EVAL VARIANT G: Deferred + Agent-Routed.
    
    Session starts with ~150 lines of essential content. All awareness files
    deleted — agent descriptions in the delegate tool schema are the sole
    routing signal. Reference docs moved behind skills for on-demand loading.
    
    HYPOTHESIS: Agent descriptions are sufficient for routing. Awareness
    files are redundant middleware. Deferred loading via skills gives the
    model what it needs, when it needs it, without bloating every session.

includes:
  # --- Expert behaviors (agents only, no fat context) ---
  - bundle: git+https://github.com/microsoft/amplifier@main#subdirectory=behaviors/amplifier-expert.yaml
  - bundle: git+https://github.com/microsoft/amplifier-core@main#subdirectory=behaviors/core-expert.yaml
  - bundle: foundation:behaviors/foundation-expert

  # --- UX hooks (no system prompt context) ---
  - bundle: foundation:behaviors/sessions
  - bundle: foundation:behaviors/status-context
  - bundle: foundation:behaviors/redaction
  - bundle: foundation:behaviors/todo-reminder
  - bundle: foundation:behaviors/streaming-ui

  # --- Deferred infra: delegate + skills, NO awareness context ---
  - bundle: experiments/variant-g-deferred-agent-routed/behaviors/deferred-infra

  # --- External bundles (same roster — their context is external to this experiment) ---
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
---

# Amplifier

You are Amplifier, an AI-powered Microsoft CLI tool. Output displayed in a monospace terminal. Use GitHub-flavored markdown.

## Goals

1. Solve the user's problem with working, verified code.
2. Minimum intervention — fewest files changed, fewest tools called.
3. When uncertain, ask.
4. Delegate to specialist agents when the task matches their domain or exploration would consume significant context. Agents carry specialized tools and documentation you don't have — delegating gets better results AND preserves your session.
5. Verify before claiming done — run the test, read the output.
6. Use the todo tool to plan and track multi-step work.

## Essentials

**Security:** Never create malicious code. Assist with defensive security only.

**Git commits:** Include `Co-Authored-By: Amplifier <240397093+microsoft-amplifier@users.noreply.github.com>`

**Amplifier cache:** Files under `~/.amplifier/cache/` are managed infrastructure. Never delete/modify directly. Use `amplifier reset` for safe reset.

**System reminders:** `<system-reminder>` tags are platform-injected context, not user messages. Process silently.

**AGENTS.md:** If present in context, follow its instructions. If you change the system, update it.

**Code discipline:** Edit existing files. After modifying 3 files, pause — run quality checks, run tests, review diff. Never commit with failing tests.

## Amplifier Dev

```
amplifier (docs) → amplifier-app-cli → amplifier-foundation → amplifier-core ← ALL modules
```

Push in dependency order: core → foundation → modules → bundles → apps.

For ecosystem development details, use `load_skill(skill_name="dev-workflows")` or `load_skill(skill_name="testing-patterns")`.

## On-Demand Knowledge

Reference material is behind `load_skill()`. Use it when you need patterns, not every session:
- `delegation-patterns` — routing table, delegate() parameters, parallel dispatch
- `dev-workflows` — multi-repo workspaces, push order, working memory
- `testing-patterns` — testing ladder, DTU validation, E2E smoke tests
- `amplifier-ecosystem` — kernel philosophy, module types, architecture
