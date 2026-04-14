# Experimental Lean Bundles

Stripped-down Amplifier bundles for cost-effective sessions. Two tiers available:

## exp-lean-foundation

Minimal infrastructure — 84% token reduction vs standard Foundation (8.6K vs 54.6K tokens).

Includes core tools, delegate, skills, UX hooks. No modes, no expert consultants, no heavy context docs.

```bash
amplifier bundle add foundation:experiments/exp-lean/exp-lean-foundation --name exp-lean-foundation
amplifier bundle use exp-lean-foundation
```

## exp-lean-amplifier-dev

Dev tooling on top of lean-foundation — 71% token reduction vs standard amplifier-dev (17.8K vs 60.5K tokens).

Adds dev agents (explorer, git-ops, bug-hunter, zen-architect, modular-builder, file-ops, post-task-cleanup), Python tooling (ruff, pyright, LSP), and apply_patch.

```bash
amplifier bundle add foundation:experiments/exp-lean/exp-lean-amplifier-dev --name exp-lean-amplifier-dev
amplifier bundle use exp-lean-amplifier-dev
```

## Feedback

These are experiments. If sessions feel degraded compared to the standard bundles, report what's missing.
