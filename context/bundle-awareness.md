# Bundle and Behavior Capabilities

You have access to Amplifier's bundle composition system.

## Core Concepts

**Bundles** are the mechanism for combining and composing Amplifier ecosystem components:
- Modules (tools, providers, hooks, orchestrators, context managers)
- Context files (instructions, documentation)
- Agents (specialized personas with focused context)
- Other bundles (composition/inheritance)

**Behavior bundles** are a convention (not code-enforced) for creating partial bundles that add complete capabilities to full bundles. They package related agents, modules, and context together for reuse.

**Context sinks** are agents that carry heavy documentation. Delegating work to them:
- Frees the parent session from token consumption
- Lets sub-sessions burn context doing work
- Returns concise results without bloating the main session
- Critical strategy for longer-running session success

## Required: Delegate to Expert

Bundle, behavior, and module work requires specialized knowledge about:
- Namespace resolution and URI formats
- Protocol contracts (Tool, Provider, Hook interfaces)
- Composition patterns and context flow
- The thin bundle pattern

**BEFORE any bundle/behavior/module work**, delegate to `foundation:foundation-expert`.

The expert has authoritative access to:
- `foundation:docs/BUNDLE_GUIDE.md` - Complete authoring guide
- `foundation:docs/URI_FORMATS.md` - Source URI syntax
- `core:docs/contracts/` - Protocol specifications

**Canonical example**: [amplifier-bundle-recipes](https://github.com/microsoft/amplifier-bundle-recipes) demonstrates proper bundle structure, formatting, and layout.

## Why Delegation is Required

Working without expert consultation results in preventable errors:
- Wrong module source syntax (namespace paths vs git URLs)
- Wrong protocol interfaces (`@property` vs method)
- Broken namespace references after renames
- Context injection that doesn't work as expected

## Quick Reference (Simple Lookups Only)

| Need | Location |
|------|----------|
| Bundle authoring | `foundation:docs/BUNDLE_GUIDE.md` |
| URI formats | `foundation:docs/URI_FORMATS.md` |
| Canonical example | `amplifier-bundle-recipes` on GitHub |
| Tool contract | `core:docs/contracts/TOOL_CONTRACT.md` |

For anything beyond a quick lookup, delegate to `foundation:foundation-expert`.
