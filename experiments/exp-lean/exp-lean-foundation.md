---
bundle:
  name: exp-lean-foundation
  version: 0.1.0
  description: |
    EXPERIMENTAL lean foundation bundle -- minimal token footprint.
    
    Stripped Foundation for cost-effective sessions: 84% reduction (54K -> 8.6K tokens).
    Core tools, essential UX hooks, delegate tool, skills. No modes, no expert
    consultants, no heavy context docs.
    
    To use this bundle:
      amplifier bundle add foundation:experiments/exp-lean/exp-lean-foundation --name exp-lean-foundation
      amplifier bundle use exp-lean-foundation

includes:
  - bundle: foundation:experiments/exp-lean/behaviors/lean-foundation
---

@foundation:experiments/exp-lean/context/system-base.md
