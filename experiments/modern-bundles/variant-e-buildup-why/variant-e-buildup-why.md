---
bundle:
  name: variant-e-buildup-why
  version: 0.1.0
  description: |
    Variant E -- Build-Up-WHY. The "full kitchen sink" experiment.

    Forked from the build-up experiment (inverse of exp-lean: starts from
    zero, parent has ONLY `todo` + `delegate`, every concrete action goes
    through one of four self-sufficient sub-session agents). On top of that
    base, this variant applies the WHY-over-rules rewriting strategy plus a
    bundle constitution -- so it stacks Strategies 1, 2, 3, 4, 5, 7, 8, 9,
    10, 11, and 13 from the Session A strategy catalogue into a single
    bundle, testing the radical from-zero architecture combined with
    principled reasoning throughout every instruction.

    What's in:
      - 4 tool modules at the sub-session layer: bash, todo, filesystem, delegate
      - 4 forked WHY-rewritten agents: explorer, planner, coder, tester
      - Constitution (5 values) prepended to system-base
      - WHY reasoning applied to every flat imperative in system-base,
        delegation-mechanics, and all four agent prompts
      - Free-cost UX/productivity hooks
      - System prompt biases the model toward `delegate` from line one

    Goal: discover whether principled, reasoning-rich instructions improve
    behaviour on top of the irreducible-core architecture build-up
    established. If Variant E outperforms build-up, the WHY-rewrites are
    paying their token cost. If it underperforms, some of build-up's
    terseness was load-bearing.

    Caveat: build-up's parent has NO tools beyond `todo` + `delegate`.
    Scenarios that require direct file ops at the root level will fail by
    design; that's intentional, and matches the build-up testbed.

    To use this bundle:
      amplifier bundle add 'git+https://github.com/microsoft/amplifier-foundation@main#subdirectory=experiments/variant-e-buildup-why/variant-e-buildup-why.md' --name variant-e-buildup-why
      amplifier bundle use variant-e-buildup-why

includes:
  - bundle: foundation:experiments/variant-e-buildup-why/behaviors/buildup-why
---

@foundation:experiments/variant-e-buildup-why/context/system-base-why.md
