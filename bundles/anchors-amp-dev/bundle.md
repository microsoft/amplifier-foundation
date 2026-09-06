---
bundle:
  name: anchors-amp-dev
  version: 0.2.0
  description: |
    The anchors bundle plus Amplifier-ecosystem knowledge.
    Same principle-driven core, same runtime, same agent roster -- with one
    added layer for sessions that work on the Amplifier ecosystem itself:
    repo dependency order, cross-repo validation in a Digital Twin Universe,
    and bundle/agent authoring. Everything else is inherited from anchors, so
    the two bundles cannot drift.

includes:
  # Cross-repo validation in a Digital Twin Universe.
  #
  # Listed FIRST on purpose, even though anchors is the base. Include order is
  # what fixes the order of the merged `tools:` list and of tool-skills'
  # `skills:` search path; this order reproduces the previously shipped mount
  # plan byte-for-byte (verified: session/tools/hooks identical before and
  # after this refactor), so the DRY change moves no wire bytes. It also leaves
  # anchors last, so anchors' values win any scalar conflict -- which is what
  # "anchors is the base" should mean.
  - bundle: git+https://github.com/microsoft/amplifier-bundle-amplifier-tester@main
  # The whole runtime -- session, tools, hooks, agents, behaviors -- comes from
  # anchors. Full URL, not a bare name, so this bundle stays liftable.
  - bundle: git+https://github.com/microsoft/amplifier-foundation@main#subdirectory=bundles/anchors/bundle.md

agents:
  include:
    - anchors-amp-dev:amplifier-dev-expert
---

@anchors:context/system.md

@anchors-amp-dev:context/amplifier-ecosystem.md
