#!/usr/bin/env python3
"""
Example 2: Custom Configuration - Tailoring Your Agent
=======================================================

VALUE PROPOSITION:
Amplifier uses composition, not configuration. Want different behavior? Swap modules.
No flags to toggle, no YAML hell - just compose different capabilities.

WHAT YOU'LL LEARN:
- How to add tools to give your agent capabilities
- How to configure streaming for real-time responses
- How composition works: base + overlay = customized agent

TIME TO VALUE: 5 minutes
"""

import asyncio
import os
from pathlib import Path

from amplifier_foundation import Bundle
from amplifier_foundation import load_bundle


async def basic_agent():
    """A minimal agent with no tools."""
    print("\n" + "=" * 60)
    print("EXAMPLE 1: Basic Agent (No Tools)")
    print("=" * 60)

    foundation_path = Path(__file__).parent.parent  # examples/ -> amplifier-foundation/
    foundation = await load_bundle(str(foundation_path))
    provider = await load_bundle(str(foundation_path / "providers" / "anthropic-sonnet.yaml"))

    # Compose foundation + provider (no custom config needed)
    composed = foundation.compose(provider)

    print("⏳ Preparing...")
    prepared = await composed.prepare()
    session = await prepared.create_session()

    async with session:
        print("📝 Asking: What tools do you have available?")
        response = await session.execute("What tools do you have available?")
        print(f"\n✓ Response: {response[:200]}...")


async def agent_with_tools():
    """An agent with filesystem and bash capabilities."""
    print("\n" + "=" * 60)
    print("EXAMPLE 2: Agent with Tools (Filesystem + Bash)")
    print("=" * 60)

    foundation_path = Path(__file__).parent.parent  # examples/ -> amplifier-foundation/
    foundation = await load_bundle(str(foundation_path))
    provider = await load_bundle(str(foundation_path / "providers" / "anthropic-sonnet.yaml"))

    # Add tools via composition
    tools_config = Bundle(
        name="tools-config",
        version="1.0.0",
        tools=[
            {
                "module": "tool-filesystem",
                "source": "git+https://github.com/microsoft/amplifier-module-tool-filesystem@main",
            },
            {"module": "tool-bash", "source": "git+https://github.com/microsoft/amplifier-module-tool-bash@main"},
        ],
    )

    composed = foundation.compose(provider).compose(tools_config)

    print("⏳ Preparing (downloading tool modules, may take 30s first time)...")
    prepared = await composed.prepare()
    session = await prepared.create_session()

    async with session:
        print("📝 Asking: List files in current directory")
        response = await session.execute("List the files in the current directory and tell me what you find.")
        print(f"\n✓ Response: {response[:300]}...")


async def streaming_agent():
    """An agent with streaming for real-time responses."""
    print("\n" + "=" * 60)
    print("EXAMPLE 3: Streaming Agent (Real-time Responses)")
    print("=" * 60)
    print("Key: Swap orchestrator from 'loop-basic' to 'loop-streaming'")

    foundation_path = Path(__file__).parent.parent  # examples/ -> amplifier-foundation/
    foundation = await load_bundle(str(foundation_path))
    provider = await load_bundle(str(foundation_path / "providers" / "anthropic-sonnet.yaml"))

    # Configure streaming via session config
    streaming_config = Bundle(
        name="streaming-config",
        version="1.0.0",
        session={
            "orchestrator": {
                "module": "loop-streaming",  # Streaming orchestrator!
                "source": "git+https://github.com/microsoft/amplifier-module-loop-streaming@main",
            }
        },
        hooks=[
            {
                "module": "hooks-streaming-ui",  # Hook to display streaming output
                "source": "git+https://github.com/microsoft/amplifier-module-hooks-streaming-ui@main",
            }
        ],
    )

    composed = foundation.compose(provider).compose(streaming_config)

    print("⏳ Preparing...")
    prepared = await composed.prepare()
    session = await prepared.create_session()

    print("\n📝 Watch the response stream in real-time:\n")
    async with session:
        response = await session.execute("Write a short poem about software modularity.")
        print(f"\n✓ Final response captured: {len(response)} chars")


async def main():
    """Run all examples to showcase configuration patterns."""

    print("🎨 Amplifier Configuration Showcase")
    print("=" * 60)
    print("\nKEY CONCEPT: Composition over Configuration")
    print("- Want different behavior? Swap modules, don't toggle flags")
    print("- Each module is independently testable and upgradeable")
    print("- Compose your perfect agent from building blocks")

    # Run examples
    await basic_agent()
    await agent_with_tools()
    await streaming_agent()

    print("\n" + "=" * 60)
    print("📚 WHAT YOU LEARNED:")
    print("=" * 60)
    print("1. Orchestrators: loop-basic (simple) vs loop-streaming (real-time)")
    print("2. Tools: Add capabilities by composing tool modules with 'source' field")
    print("3. Composition: foundation.compose(provider).compose(tools)")
    print("\n✅ All configuration through composition - no flags, no YAML hell!")
    print("\n💡 Next: Try 03_custom_tool.py to build your own custom tool")


if __name__ == "__main__":
    if not os.getenv("ANTHROPIC_API_KEY"):
        print("❌ ERROR: Set ANTHROPIC_API_KEY environment variable")
        print("\nExample:")
        print("  export ANTHROPIC_API_KEY='your-key-here'")
        print("  python 02_custom_configuration.py")
        exit(1)

    asyncio.run(main())
