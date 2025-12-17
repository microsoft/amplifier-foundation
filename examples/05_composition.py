#!/usr/bin/env python3
"""Example 2: Bundle composition and merge rules.

TEACHABLE MOMENT: How bundle.compose(overlay) merges configuration

Composition Rules:
- session: DEEP MERGE - nested dicts are merged recursively
- providers/tools/hooks: MERGE BY MODULE ID - same module updates, new modules added
- instruction: REPLACE - overlay completely replaces base
- includes: NOT MERGED - resolved before composition

This is the SINGLE SOURCE for understanding composition.
"""

import asyncio

from amplifier_foundation import Bundle


async def main() -> None:
    """Demonstrate composition merge rules with concrete examples."""

    # BASE BUNDLE: Starting configuration
    base = Bundle(
        name="base",
        version="1.0.0",
        session={
            "orchestrator": {"module": "loop-basic"},
            "context": {"module": "context-simple", "config": {"max_tokens": 100000}},
        },
        providers=[
            {"module": "provider-mock", "config": {"debug": False}},
        ],
        tools=[
            {"module": "tool-filesystem"},
            {"module": "tool-bash"},
        ],
        instruction="Base instructions.",
    )

    # OVERLAY BUNDLE: Changes to apply
    overlay = Bundle(
        name="overlay",
        version="1.0.0",
        session={
            # Rule: DEEP MERGE - only updates context.config, preserves orchestrator
            "context": {"config": {"max_tokens": 200000, "auto_compact": True}},
        },
        providers=[
            # Rule: MERGE BY MODULE - updates provider-mock's debug setting
            {"module": "provider-mock", "config": {"debug": True}},
            # Rule: MERGE BY MODULE - adds new provider (doesn't exist in base)
            {"module": "provider-anthropic", "config": {"default_model": "claude-sonnet-4-5"}},
        ],
        tools=[
            # Rule: MERGE BY MODULE - adds tool-web (filesystem and bash preserved)
            {"module": "tool-web"},
        ],
        instruction="Overlay instructions.",  # Rule: REPLACE - completely replaces base
    )

    # COMPOSE: base + overlay
    result = base.compose(overlay)

    # Show results
    print("=" * 60)
    print("COMPOSITION RESULT")
    print("=" * 60)

    print("\n1. SESSION (deep merge):")
    print(f"   orchestrator: {result.session.get('orchestrator')}")  # From base
    print(f"   context: {result.session.get('context')}")  # Merged

    print("\n2. PROVIDERS (merge by module ID):")
    for p in result.providers:
        print(f"   - {p.get('module')}: {p.get('config', {})}")

    print("\n3. TOOLS (merge by module ID):")
    for t in result.tools:
        print(f"   - {t.get('module')}")

    print("\n4. INSTRUCTION (replace):")
    print(f"   '{result.instruction}'")  # From overlay, not base

    # Summary of rules
    print("\n" + "=" * 60)
    print("MERGE RULES SUMMARY")
    print("=" * 60)
    print("  session:     DEEP MERGE (nested dicts merged)")
    print("  providers:   MERGE BY MODULE (same ID = update, new ID = add)")
    print("  tools:       MERGE BY MODULE (same ID = update, new ID = add)")
    print("  hooks:       MERGE BY MODULE (same ID = update, new ID = add)")
    print("  instruction: REPLACE (overlay wins)")
    print("  includes:    RESOLVED FIRST (not merged)")


if __name__ == "__main__":
    asyncio.run(main())
