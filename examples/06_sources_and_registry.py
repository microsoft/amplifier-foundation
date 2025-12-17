#!/usr/bin/env python3
"""Example 3: Loading from remote sources and using BundleRegistry.

TEACHABLE MOMENT: load_bundle() supports multiple source formats

Source Formats:
- Local path: "/path/to/bundle" or "file:///path/to/bundle"
- Git URL: "git+https://github.com/org/repo@branch"
- Git subdirectory: "git+https://...@branch#subdirectory=path/in/repo"

BundleRegistry provides named bundle management:
- register() maps names to source URIs
- load() resolves names to bundles with caching
"""

import asyncio
from pathlib import Path

from amplifier_foundation import Bundle
from amplifier_foundation import BundleRegistry
from amplifier_foundation import BundleState
from amplifier_foundation import load_bundle


async def main() -> None:
    """Demonstrate source formats and registry usage."""

    # =========================================================================
    # PART 1: Direct loading from git
    # =========================================================================
    print("=" * 60)
    print("PART 1: Loading from Git URL")
    print("=" * 60)

    git_uri = "git+https://github.com/microsoft/amplifier-foundation@main"
    print(f"\nURI: {git_uri}")
    print("(Clones repo to cache, loads bundle.md)")

    try:
        bundle = await load_bundle(git_uri)
        print(f"Loaded: {bundle.name} v{bundle.version}")
    except Exception as e:
        print(f"Skipped (no network): {type(e).__name__}")

    # =========================================================================
    # PART 2: BundleRegistry for named bundles
    # =========================================================================
    print("\n" + "=" * 60)
    print("PART 2: Using BundleRegistry")
    print("=" * 60)

    # Create registry with custom home
    registry = BundleRegistry(home=Path.home() / ".cache" / "amplifier-example")

    # Register well-known bundles (APP-LAYER POLICY)
    # Your app decides which bundles are "well-known"
    registry.register(
        {
            "foundation": "git+https://github.com/microsoft/amplifier-foundation@main",
            "local-dev": f"file://{Path(__file__).parent.parent.resolve()}",
        }
    )

    print(f"\nRegistered: {registry.list_registered()}")

    # Load by name (resolves URI, caches, returns Bundle)
    try:
        bundle = await registry.load("local-dev")
        if isinstance(bundle, Bundle):
            print(f"Loaded 'local-dev': {bundle.name} v{bundle.version}")

            # Check state
            state = registry.get_state("local-dev")
            if isinstance(state, BundleState):
                print(f"Local path: {state.local_path}")
    except Exception as e:
        print(f"Error: {e}")

    # =========================================================================
    # SUMMARY
    # =========================================================================
    print("\n" + "=" * 60)
    print("SOURCE FORMAT REFERENCE")
    print("=" * 60)
    print("  Local:         /path/to/bundle")
    print("  File URI:      file:///path/to/bundle")
    print("  Git:           git+https://github.com/org/repo@branch")
    print("  Git subdir:    git+...@branch#subdirectory=path")


if __name__ == "__main__":
    asyncio.run(main())
