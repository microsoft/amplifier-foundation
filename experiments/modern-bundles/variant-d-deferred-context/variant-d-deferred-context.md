---
bundle:
  name: variant-d-deferred-context
  version: 0.1.0
  description: |
    Strategy 6 + partial 2 -- heavy context moved behind skills, light strip.

    Variant of the standard foundation bundle. The two heavy context docs
    (delegation-instructions.md and multi-agent-patterns.md) are NOT loaded
    at session start. Instead they live as skills loaded on demand via
    load_skill(). The system prompt carries:
      - common-system-base (trimmed of verbose validation protocol)
      - common-agent-base (lightly stripped of restated defaults)
      - a brief delegation summary
      - a "pitch-then-link" pointer to the deferred skills
    Tests whether the model still routes correctly when detailed delegation
    instructions are deferred rather than preloaded.

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
  # Agent orchestration with delegate tool -- VARIANT D replaces the standard
  # foundation:behaviors/agents (which loads the heavy 308-line delegation
  # instructions) with a behavior that loads only a brief summary and exposes
  # the full content via load_skill("delegation-patterns").
  - bundle: foundation:experiments/variant-d-deferred-context/behaviors/deferred-context
  # External bundles
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
  # NOTE: delegate tool and tool-skills come from the deferred-context behavior

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

# Variant D: Deferred-Context Bundle

This is an experimental variant of the standard foundation bundle for the
Session A bundle-eval experiment. It tests **Strategy 6 (Move Context Behind
Mechanisms)** with a partial **Strategy 2 (Strip Until It Breaks)** applied
to the common-agent-base.

## What's different from `foundation`

| Aspect | Standard `foundation` | Variant D |
|--------|----------------------|-----------|
| Delegation instructions (~308 lines) | Loaded at session start | Behind `load_skill("delegation-patterns")` |
| Multi-agent patterns (~195 lines) | Loaded at session start | Behind `load_skill("multi-agent-patterns")` |
| System prompt | Full `common-system-base.md` | Trimmed: verbose validation protocol cut to essentials |
| Agent base | Full `common-agent-base.md` | Lightly stripped of restated default behaviors |
| Session-start hint | Heavy doc preloaded | "Pitch-then-link" pointer + 12-line delegation brief |
| Skills directory | Default skills only | Default + this variant's `skills/` |

## The test question

> Does the model still route to the right agent and use the right delegation
> patterns when the detailed instructions are behind a skill instead of in
> the system prompt?

If yes, this strategy reclaims a large chunk of base context for free.

@foundation:experiments/variant-d-deferred-context/context/system-base-deferred.md
