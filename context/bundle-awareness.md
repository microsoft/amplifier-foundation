# Bundle and Behavior Capabilities

You have access to Amplifier's bundle composition system.

## Core Concepts

**For complete terminology**: See `foundation:docs/CONCEPTS.md` for bundle definitions and structural concepts (root bundles, nested bundles, namespace registration).

**Key terms** (details in CONCEPTS.md):
- **Bundles** = mechanism for composing modules, context, agents, and other bundles
- **Behavior bundles** = convention for reusable capability packages (agents + context)
- **Context sinks** = agents carrying heavy docs, freeing parent sessions from token consumption

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

---

**Diagrams are auto-maintained:** `validate-bundle-repo` auto-regenerates a stale or missing `bundle.dot`. Set `enhance_diagrams: "false"` for structural-only DOT (no LLM enhancement). Run `generate-bundle-docs` directly for bulk generation.
