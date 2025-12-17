#!/usr/bin/env python3
"""
Example 1: Hello World - Your First Amplifier Agent
====================================================

VALUE PROPOSITION:
Get an AI agent running in ~15 lines of code. No boilerplate, no configuration hell.
Just load, compose, and execute.

WHAT YOU GET:
- Pre-configured foundation bundle with sensible defaults
- Automatic module discovery and loading
- Production-ready orchestration and context management

TIME TO VALUE: 2 minutes
"""

import asyncio
import os
from pathlib import Path

from amplifier_foundation import load_bundle


async def main():
    """The simplest possible Amplifier agent."""

    # Step 1: Load the foundation bundle (comes with orchestrator, context manager, etc.)
    foundation_path = Path(__file__).parent.parent  # examples/ -> amplifier-foundation/
    foundation = await load_bundle(str(foundation_path))
    print(f"✓ Loaded foundation: {foundation.name} v{foundation.version}")

    # Step 2: Load a provider bundle (includes module source for auto-download)
    # Use the pre-configured Anthropic Sonnet provider
    provider_path = foundation_path / "providers" / "anthropic-sonnet.yaml"
    provider = await load_bundle(str(provider_path))
    print(f"✓ Loaded provider: {provider.name}")

    # Step 3: Compose foundation + provider
    composed = foundation.compose(provider)
    print("✓ Composed bundles")

    # Step 4: Prepare (resolves and downloads modules if needed)
    print("⏳ Preparing (downloading modules if needed, this may take 30s first time)...")
    prepared = await composed.prepare()
    print("✓ Modules prepared")

    # Step 5: Create session and execute
    print("⏳ Creating session...")
    session = await prepared.create_session()
    print("✓ Session ready")

    # Step 6: Execute a prompt
    print("\n" + "=" * 60)
    print("Executing prompt...")
    print("=" * 60)
    async with session:
        response = await session.execute(
            "Write a Python function to check if a number is prime. Include docstring and type hints."
        )
        print("\nResponse:")
        print("-" * 60)
        print(response)


if __name__ == "__main__":
    # Check for API key
    if not os.getenv("ANTHROPIC_API_KEY"):
        print("❌ ERROR: Set ANTHROPIC_API_KEY environment variable")
        print("\nExample:")
        print("  export ANTHROPIC_API_KEY='your-key-here'")
        print("  python 01_hello_world.py")
        exit(1)

    print("🚀 Amplifier Hello World\n")
    asyncio.run(main())
    print("\n✅ That's it! You just ran your first AI agent.")
    print("\n💡 Next: Try 02_custom_configuration.py to see different configurations")
