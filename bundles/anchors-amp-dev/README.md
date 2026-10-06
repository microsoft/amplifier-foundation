# Anchors + Amplifier-Ecosystem Knowledge

The [`anchors`](../anchors/bundle.md) bundle plus one portable capability: knowledge of
the Amplifier ecosystem itself — repo dependency order, cross-repo validation in
a Digital Twin Universe, and bundle/agent authoring.

Both bundles are for software development. The difference is not what kind of
work they do, it is what they *know*: this one additionally knows the ecosystem
it is being used to build.

## Install

### Resource identifiers

The nested root is canonical. Its added resource identifiers use the portable
capability's namespace:

| Former identifier | Replacement |
|---|---|
| `anchors-amp-dev:amplifier-dev-expert` | `amp-dev:amplifier-dev-expert` |
| `anchors-amp-dev:context/amplifier-ecosystem.md` | `amp-dev:context/amplifier-dev/amplifier-ecosystem.md` |

Update explicit delegate calls and context references. The old agent alias and
context path are not compatibility exports. The expert uses the enclosing
`foundation:` resource namespace for documentation, not its runtime. There are
no flat manifests or compatibility wrappers.

### Select the complete root

`anchors-amp-dev` is a registered bundle, so it can be selected by name:

```bash
amplifier bundle use anchors-amp-dev
```

Or add it explicitly by URI (single-quote to prevent shell expansion of the `#`
fragment):

```bash
amplifier bundle add 'git+https://github.com/microsoft/amplifier-foundation@main#subdirectory=bundles/anchors-amp-dev' --name anchors-amp-dev
amplifier bundle use anchors-amp-dev
```

## What it is, mechanically

The canonical `bundles/anchors-amp-dev/bundle.md` declares no runtime of its own.
The directory URI selects that manifest; an explicit
`#subdirectory=bundles/anchors-amp-dev/bundle.md` also works. Its own namespace
stays at the manifest directory, without a `namespace_root` override or variant
assets. Self-namespaced relative includes select the base and capability from the
same repository, including direct local file/directory loads without separately
registering Foundation. Its includes and instruction body are:

```yaml
includes:
  - bundle: anchors-amp-dev:../anchors/bundle.md
  - bundle: anchors-amp-dev:../../behaviors/amp-dev.yaml
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
│   ├── anchors/
│   │   └── bundle.md               # complete Anchors host, alongside its assets
│   └── anchors-amp-dev/
│       ├── README.md               # this file
│       └── bundle.md               # Anchors + amp-dev behavior
├── behaviors/amp-dev.yaml          # complete portable amp-dev capability
├── agents/amplifier-dev-expert.md   # lean ecosystem authority
└── context/amplifier-dev/
    ├── amplifier-ecosystem.md      # short operating instruction
    └── …                           # canonical ecosystem reference docs
```

## Status

Version 0.3.0. The nested root composes canonical nested Anchors plus the shared
capability, without a second implementation or flat entry point.

**Historical qualification (before the nested-only layout):** Local targeted
qualification passed 232 checks. Separate `amplifier-tester` acceptance through
official `amplifier-app-cli` passed 17 bounded checks covering the then-flat roots
and compatibility entry points:
real model responses, file and Bash tools, named-agent spawning, skills loading,
candidate resource provenance, and retained streaming/simple runtimes.

The portable capability also historically passed real root and expert-spawn
checks in an isolated non-Anchors host. These observations do not qualify the
nested-only layout or establish a clean repository-wide recipe verdict; full
validation failures and coverage limits remain separate evidence.
