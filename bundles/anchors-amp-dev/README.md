# Anchors + Amplifier-Ecosystem Knowledge

The [`anchors`](../anchors/) bundle plus one layer: knowledge of the Amplifier
ecosystem itself — repo dependency order, cross-repo validation in a Digital
Twin Universe, and bundle/agent authoring.

Both bundles are for software development. The difference is not what kind of
work they do, it is what they *know*: this one additionally knows the ecosystem
it is being used to build.

## Install

`anchors-amp-dev` is a registered bundle, so it can be selected by name:

```bash
amplifier bundle use anchors-amp-dev
```

Or add it explicitly by URI (single-quote to prevent shell expansion of the `#`
fragment; the `.md` suffix is required):

```bash
amplifier bundle add 'git+https://github.com/microsoft/amplifier-foundation@main#subdirectory=bundles/anchors-amp-dev/bundle.md' --name anchors-amp-dev
amplifier bundle use anchors-amp-dev
```

## What it is, mechanically

`bundle.md` is about 25 lines and declares no runtime of its own. It has two
includes and a two-line body:

```yaml
includes:
  - bundle: git+https://github.com/microsoft/amplifier-foundation@main#subdirectory=bundles/anchors/bundle.md
  - bundle: git+https://github.com/microsoft/amplifier-bundle-amplifier-tester@main
```

```
@anchors:context/system.md

@anchors-amp-dev:context/amplifier-ecosystem.md
```

Everything else — `session:`, `tools:`, `hooks:`, the six agents, the behaviors
— comes from the anchors include. There is no second copy to keep in sync, and
`tests/test_anchors_bundles_dry.py` fails if one reappears.

The body order is load-bearing: instruction `@mention`s lead the system prompt
and are emitted in body order (#359), so the principles come first and the
ecosystem layer second.

## The principle core (from anchors)

1. **Investigate before acting** — understand the problem fully before proposing solutions.
2. **Minimum viable change** — nothing speculative; every line and abstraction earns its place.
3. **Verify at every step** — never claim "done" without proof.

## What this bundle adds

| Addition | Notes |
|---|---|
| `context/amplifier-ecosystem.md` | Three ecosystem principles (dependency order, prove cross-repo changes in a DTU, safe push order) and how to touch session data safely. Appended to the anchors system prompt, not a replacement for it. |
| `amplifier-dev-expert` agent | Authority for multi-repo development, push order, DTU validation, and bundle/agent authoring. Loads the three ecosystem docs from `@foundation:context/amplifier-dev/` on spawn. |
| `amplifier-tester` include | Cross-repo validation in a Digital Twin Universe (pulls in `digital-twin-universe` and `gitea` transitively). |

The three ecosystem docs live in exactly one place — the repo root's
`context/amplifier-dev/`. The `foundation:` namespace resolves inside this
bundle's sessions because this bundle is nested inside the amplifier-foundation
repo, and the registry registers the enclosing root bundle's namespace at load
time; no `foundation` include is needed or wanted.

## Files

```
anchors-amp-dev/
├── README.md                        # this file
├── bundle.md                        # ~25 lines: two includes, one agent, two mentions
├── agents/
│   └── amplifier-dev-expert.md      # the ecosystem authority
└── context/
    └── amplifier-ecosystem.md       # the ecosystem layer appended to anchors' system.md
```

## Status

Version 0.2.0. Previously a full copy of the anchors tree with an added agent;
now a thin include, so the evaluated anchors text is the only copy of it.
