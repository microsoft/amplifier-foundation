---
bundle:
  name: build-up-foundation
  version: 0.1.0
  description: |
    EXPERIMENTAL build-up bundle — the inverse of exp-lean.

    Where exp-lean strips amplifier-foundation down, build-up starts from zero
    and adds back only what is necessary for the model to do useful work, with
    delegation as the primary scale-out mechanism. Maximally aggressive cut:

      - 4 tool modules: bash, todo, filesystem, delegate
      - Zero pre-registered agents (reach foundation specialists by bundle path)
      - Free-cost UX/productivity hooks only
      - System prompt biases the model toward `delegate` from line one

    Goal: discover the irreducible core of an Amplifier session.

    To use this bundle:
      amplifier bundle add 'git+https://github.com/microsoft/amplifier-foundation@main#subdirectory=experiments/build-up/build-up-foundation.md' --name build-up-foundation
      amplifier bundle use build-up-foundation

includes:
  - bundle: foundation:experiments/build-up/behaviors/build-up-foundation
---

@foundation:experiments/build-up/context/system-base.md
