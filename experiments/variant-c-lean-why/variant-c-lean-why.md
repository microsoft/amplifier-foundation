---
bundle:
  name: variant-c-lean-why
  version: 0.1.0
  description: |
    EXPERIMENTAL Variant C: Lean-WHY -- Strategies 1 + 2 + 9 combined
    (WHY-over-rules + strip-to-essentials + bundle constitution).

    Starts from exp-lean (8.6K tokens, already stripped) and applies WHY
    reasoning to every rule in the remaining ~36 lines, plus a 5-line
    constitution at the top of the system prompt.

    Hypothesis: quality + brevity beats volume. Tests whether the model
    benefits from explicit reasoning even on an already-minimal prompt.
    Outperforming Variant B (WHY on full prompt) would mean stripping
    cut noise that hurt the model; underperforming it would mean some
    of the stripped content was load-bearing.

    To use this bundle:
      amplifier bundle add foundation:experiments/variant-c-lean-why/variant-c-lean-why --name variant-c-lean-why
      amplifier bundle use variant-c-lean-why

includes:
  - bundle: foundation:experiments/variant-c-lean-why/behaviors/lean-why
---

@foundation:experiments/variant-c-lean-why/context/system-base-why.md
