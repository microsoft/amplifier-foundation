---
bundle:
  name: variant-b-why-foundation
  version: 0.1.0
  description: |
    Variant B for the bundle eval experiment — Strategy 1 (WHY over rules)
    combined with Strategy 9 (bundle constitution).

    Fork of the standard foundation bundle. Same agents, same tools, same
    behaviors — only the core instructional content changes. Every flat
    imperative in common-system-base.md, common-agent-base.md, and
    delegation-instructions.md has been rewritten as principle + "because"
    clause. A short Constitution section is prepended to the system prompt
    to make the underlying values explicit.

    Hypothesis: reasoning-anchored instructions degrade more gracefully
    under novel situations than imperative rules, and an explicit values
    section reduces conflicts between rules at decision time.

includes:
  # Ecosystem expert behaviors (provides @amplifier: and @core: namespaces)
  - bundle: git+https://github.com/microsoft/amplifier@main#subdirectory=behaviors/amplifier-expert.yaml
  - bundle: git+https://github.com/microsoft/amplifier-core@main#subdirectory=behaviors/core-expert.yaml
  # Foundation expert behavior
  - bundle: foundation:behaviors/foundation-expert
  # Foundation behaviors (UX hooks and session config — unchanged from foundation)
  - bundle: foundation:behaviors/sessions
  - bundle: foundation:behaviors/status-context
  - bundle: foundation:behaviors/redaction
  - bundle: foundation:behaviors/todo-reminder
  - bundle: foundation:behaviors/streaming-ui
  # Agent orchestration — REPLACED with our WHY-rewritten behavior
  - bundle: foundation:experiments/variant-b-why-foundation/behaviors/why-foundation
  # External bundles (same as foundation)
  - bundle: git+https://github.com/microsoft/amplifier-bundle-recipes@main#subdirectory=behaviors/recipes.yaml
  - bundle: git+https://github.com/microsoft/amplifier-bundle-design-intelligence@main#subdirectory=behaviors/design-intelligence.yaml
  - bundle: git+https://github.com/microsoft/amplifier-bundle-python-dev@main
  - bundle: git+https://github.com/microsoft/amplifier-bundle-shadow@main
  - bundle: git+https://github.com/microsoft/amplifier-bundle-skills@main#subdirectory=behaviors/skills.yaml
  - bundle: git+https://github.com/microsoft/amplifier-bundle-browser-tester@main#subdirectory=behaviors/browser-tester.yaml
  - bundle: git+https://github.com/microsoft/amplifier-bundle-superpowers@main#subdirectory=behaviors/superpowers-methodology.yaml
  - bundle: git+https://github.com/microsoft/amplifier-module-hook-shell@main#subdirectory=behaviors/hook-shell.yaml
  - bundle: git+https://github.com/microsoft/amplifier-module-tool-mcp@main#subdirectory=behaviors/mcp.yaml
  - bundle: git+https://github.com/microsoft/amplifier-bundle-filesystem@main#subdirectory=behaviors/apply-patch.yaml
  - bundle: git+https://github.com/microsoft/amplifier-bundle-routing-matrix@main

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
  # NOTE: delegate tool comes from the why-foundation behavior

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

# Variant B — WHY Foundation

This bundle is an experimental fork of `foundation` built for the bundle eval
experiment. It implements **Strategy 1 (WHY over rules)** layered with
**Strategy 9 (bundle constitution)**.

## What's different from foundation

| Surface | Foundation | Variant B |
|---------|------------|-----------|
| Agents | 11 agents | Same 11 agents |
| Tools | filesystem, bash, web, search, delegate | Same |
| UX behaviors | streaming-ui, status-context, redaction, todo-reminder, sessions | Same |
| Delegate tool | from `foundation:behaviors/agents` | Same config, exposed via `why-foundation` behavior |
| Core context (`common-system-base.md`, `common-agent-base.md`, `delegation-instructions.md`) | Imperative rules with CAPS/CRITICAL/MUST emphasis | Rewritten as principle + "because" clauses; constitution prepended |

Nothing else changes. The experiment isolates the effect of the system prompt
rewrite — same model, same agents, same tools, same hooks.

## Why this variant exists

The hypothesis under test: when the model encounters a situation no rule
explicitly covers, principles with attached reasoning generalize better than
imperative rules. The constitution provides a shared frame so rules don't
need CAPS to claim priority — when two rules seem to conflict, the underlying
value wins.

## The system prompt

@foundation:experiments/variant-b-why-foundation/context/system-base-why.md
