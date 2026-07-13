# Experimental Behavioral-Anchor Bundle — Amplifier-Dev Variant

A lean experimental bundle that shapes the agent's conduct with a short, explicit
set of **behavioral principles** at the top of the system prompt — specialized for
developing **on the Amplifier ecosystem itself** (multi-repo coordination, bundle
authoring, DTU validation).

This is the [`anchors`](../anchors/) experiment extended with an
amplifier-dev domain: one additional expert agent and a small set of dev-domain
context files, layered on the same principle-driven core.

## Install

`anchors-amp-dev` is a registered bundle, so it can be selected by name. It lives
side by side with `amplifier-dev` and changes no defaults:

```bash
amplifier bundle use anchors-amp-dev
```

Or add it explicitly by URI (single-quote to prevent shell expansion of the `#`
fragment; the `.md` suffix is required):

```bash
amplifier bundle add 'git+https://github.com/microsoft/amplifier-foundation@main#subdirectory=bundles/anchors-amp-dev/bundle.md' --name anchors-amp-dev
amplifier bundle use anchors-amp-dev
```

## The idea

Same bet as the base behavioral-anchor experiment: a handful of sharp, named
principles — re-read on every turn — steer conduct more cheaply and reliably than
verbose policy text. This variant asks whether that same lean core can carry
**domain-specialized** work (developing Amplifier) by adding only a thin domain
layer rather than heavy rule documents.

The principle core:

1. **Investigate before acting** — understand the problem fully before proposing solutions.
2. **Minimum viable change** — nothing speculative; every line and abstraction earns its place.
3. **Verify at every step** — run tests, check types, validate assumptions; evidence before assertions.
4. **Delegate complex work** — push multi-file exploration, design, implementation, debugging, and git work to sub-agents so the parent context stays lean.

## What the amplifier-dev layer adds

- **`amplifier-dev-expert`** agent — authority for multi-repo development, dependency/push order, DTU validation, and bundle/agent authoring.
- **`context/amplifier-dev/`** — `ecosystem-map.md`, `dev-workflows.md`, `testing-patterns.md`, loaded by the expert agent on demand.

Everything else mirrors the base experiment: a minimal system prompt and the same
thin, delegation-aware agent roster (explorer, architect, builder, debugger,
researcher, git-ops).
