---
bundle:
  name: minimal-delegate-foundation
  version: 0.2.0
  description: |
    EXPERIMENTAL minimal-delegate bundle — constrained orchestrator with only
    meta-tools (delegate, todo, load_skill) and a rich agent pool. No direct
    file/bash/web access. Token budget 64k.

    Where build-up uses 4 purpose-built agents (explorer/planner/coder/tester),
    minimal-delegate exposes the full foundation specialist pool (12 agents) plus
    2 python-dev specialists. The hypothesis: a minimal system prompt + rich
    agent menu, under 64k token budget, with no direct tools forces the
    orchestrator to learn clean delegation discipline.

    Use this bundle to study how a minimum-instruction orchestrator performs
    under tight tooling constraints when it can only orchestrate other agents.

    To use this bundle:
      amplifier bundle add 'git+https://github.com/microsoft/amplifier-foundation@main#subdirectory=experiments/minimal-delegate/minimal-delegate-foundation.md' --name minimal-delegate-foundation
      amplifier bundle use minimal-delegate-foundation

includes:
  - bundle: foundation:experiments/minimal-delegate/behaviors/minimal-delegate-foundation
---

@foundation:experiments/minimal-delegate/context/system-base.md
