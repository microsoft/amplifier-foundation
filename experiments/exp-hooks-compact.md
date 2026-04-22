---
bundle:
  name: exp-hooks-compact
  version: 0.1.0
  description: |
    EXPERIMENTAL: foundation + stdout compression hook.

    Adds hooks-compact to compress verbose bash STDOUT output (stderr untouched).
    Everything else is identical to the standard foundation bundle.
    DTU-verified (36 sessions): 30-98% compression on matched bash output, zero
    correctness regressions. Session-level turn/token effects are within LLM variance;
    compression budget is reinvested in deeper exploration. See PR #178 comments for
    full evidence (R2-R5).

    Scope note: stderr is NOT compressed. Commands like `cargo build`, `clippy`,
    and `curl -v` that write primarily to stderr see no compression.

    Note: foundation already includes hook-shell which truncates output in a
    different format. The A/B comparison is hook-shell truncation vs hooks-compact
    compression, not raw vs compressed.

    To use:
      amplifier bundle add git+https://github.com/microsoft/amplifier-foundation@main#subdirectory=experiments/exp-hooks-compact.md --name exp-hooks-compact
      amplifier bundle use exp-hooks-compact

    To revert to baseline:
      amplifier bundle use foundation

includes:
  - bundle: foundation:bundle

hooks:
  - module: hooks-compact
    source: git+https://github.com/samueljklee/amplifier-module-hooks-compact@v0.1.0-canary.1
    config:
      enabled: true
      min_lines: 5
      strip_ansi: true
      show_savings: true
      debug: false
      telemetry:
        local: true
        remote: false
        db_path: "~/.amplifier/hooks-compact/telemetry.db"
        retention_days: 90
---

# Experimental Foundation + Hooks Compact

EXPERIMENTAL bundle for A/B testing stdout compression before wider rollout.

## What's Different

| Feature | foundation | exp-hooks-compact |
|---------|-----------|-------------------|
| Stdout compression | hook-shell truncation (display layer) | hooks-compact (context-window layer, 30-98% on matched cmds) |
| Stderr | unchanged | unchanged |
| Session efficiency | baseline | within LLM variance; compression reinvested in deeper exploration |

## A/B Testing

```bash
amplifier bundle use foundation          # baseline
amplifier bundle use exp-hooks-compact   # experiment
```

## Scope Limitation

Hooks-compact compresses stdout only. Commands writing primarily to stderr see no compression.

@foundation:context/shared/common-system-base.md
