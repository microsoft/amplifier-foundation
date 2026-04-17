---
bundle:
  name: exp-lean-amplifier-dev
  version: 0.1.0
  description: |
    EXPERIMENTAL lean amplifier-dev bundle -- minimal token footprint with dev tooling.
    
    Stripped amplifier-dev for cost-effective dev sessions: 71% reduction (60K -> 17.8K tokens).
    Includes lean-foundation base plus dev agents, Python tooling, and LSP.
    
    To use this bundle:
      amplifier bundle add 'git+https://github.com/microsoft/amplifier-foundation@main#subdirectory=experiments/exp-lean/exp-lean-amplifier-dev.md' --name exp-lean-amplifier-dev
      amplifier bundle use exp-lean-amplifier-dev

includes:
  - bundle: foundation:experiments/exp-lean/behaviors/lean-foundation
  - bundle: foundation:experiments/exp-lean/behaviors/lean-amplifier-dev
---

@foundation:experiments/exp-lean/context/system-base.md
