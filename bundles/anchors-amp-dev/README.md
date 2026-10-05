# Anchors + Amplifier-Ecosystem Knowledge

The [`anchors`](../anchors.md) bundle plus one portable capability: knowledge of
the Amplifier ecosystem itself — repo dependency order, cross-repo validation in
a Digital Twin Universe, and bundle/agent authoring.

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
amplifier bundle add 'git+https://github.com/microsoft/amplifier-foundation@main#subdirectory=bundles/anchors-amp-dev.md' --name anchors-amp-dev
amplifier bundle use anchors-amp-dev
```

## What it is, mechanically

The canonical `bundles/anchors-amp-dev.md` declares no runtime of its own. Its
includes and instruction body are:

```yaml
includes:
  - bundle: foundation:bundles/anchors.md
  - bundle: foundation:behaviors/amp-dev.yaml
```

```
@anchors:context/system.md
```

Anchors owns the runtime, standard tools, hooks, and six engineering agents.
The `amp-dev` behavior owns **all** Amplifier-development additions, including
the tester capability; the variant does not repeat any of them. Complete roots
choose orchestrator and context-manager defaults, and applications may override
them. The behavior chooses neither.

The explicit body preserves the Anchors principles: a root's body replaces
included bodies, and instruction `@mention`s lead the context block (#359).
The ecosystem instruction accumulates through the behavior's `context.include`,
so it is not repeated in the root body.

## The principle core (from anchors)

1. **Investigate before acting** — understand the problem fully before proposing solutions.
2. **Minimum viable change** — nothing speculative; every line and abstraction earns its place.
3. **Verify at every step** — never claim "done" without proof.

## What this bundle adds

| Addition | Notes |
|---|---|
| `context/amplifier-dev/amplifier-ecosystem.md` | Short ecosystem instructions: dependency order, cross-repo DTU validation, safe push order, behavior-first packaging, and safe session-data handling. Accumulates through the behavior, not a replacement for the host's instruction. |
| `amp-dev:amplifier-dev-expert` | Lean authority for multi-repo development and bundle/agent authoring, relocated to `agents/amplifier-dev-expert.md`. Loads the shared baseline as a Foundation resource and three ecosystem reference docs on spawn; reads relevant authoring guides on demand. It does not require Anchors runtime or named agents. |
| Tester behavior | Includes `amplifier-bundle-amplifier-tester`'s `behaviors/amplifier-tester.yaml`, providing tester setup/validator, DTU profile-builder, and DTU/Gitea skills and awareness transitively. |

The behavior's `namespace_root: ..` maps `amp-dev:` to the repository root.
The enclosing `bundle.md` anchors the `foundation:` resource namespace without
composing the Foundation runtime. The ecosystem docs and expert each have one
canonical copy at repository level. The former
`anchors-amp-dev:amplifier-dev-expert` alias is **not** retained; use
`amp-dev:amplifier-dev-expert`.

Existing hosts can compose just
`git+https://github.com/microsoft/amplifier-foundation@main#subdirectory=behaviors/amp-dev.yaml`
without adopting Anchors. Tool configuration lists accumulate parent-first:
this root loads Anchors before the capability, so include order affects skill
search precedence. Test the intended precedence rather than assuming reordered
includes produce a byte-identical mount plan.

## Files

```
amplifier-foundation/
├── bundles/
│   ├── anchors.md                  # complete Anchors host
│   ├── anchors-amp-dev.md          # Anchors + amp-dev behavior
│   └── anchors-amp-dev/
│       ├── README.md               # this file
│       └── bundle.md               # compatibility wrapper
├── behaviors/amp-dev.yaml          # complete portable amp-dev capability
├── agents/amplifier-dev-expert.md   # lean ecosystem authority
└── context/amplifier-dev/
    ├── amplifier-ecosystem.md      # short operating instruction
    └── …                           # canonical ecosystem reference docs
```

## Status

Version 0.3.0. The flat root composes Anchors plus the shared capability. The old
`bundles/anchors-amp-dev/bundle.md` URI remains a compatibility wrapper, not a
second implementation. The migration passes 227 focused composition, namespace,
skill-precedence, prompt-contract and recipe checks. Live DTU qualification
remains pending; local configuration checks do not establish live execution.
