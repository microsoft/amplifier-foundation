#!/usr/bin/env python3
"""Example 1: Load a bundle and inspect what's inside.

TEACHABLE MOMENT: load_bundle() + to_mount_plan()
- load_bundle() reads a bundle from any source (local path, git URL)
- to_mount_plan() converts it to the dict format AmplifierSession needs

This is the simplest possible amplifier-foundation usage.
"""

import asyncio
from pathlib import Path

from amplifier_foundation import load_bundle


async def main() -> None:
    """Load the foundation bundle and display its mount plan."""

    # Load from local path (the foundation bundle in this repo)
    foundation_path = Path(__file__).parent.parent
    bundle = await load_bundle(str(foundation_path))

    # Basic info
    print(f"Bundle: {bundle.name} v{bundle.version}")
    print(f"Description: {bundle.description}")

    # Get mount plan - this is what AmplifierSession.create() uses
    mount_plan = bundle.to_mount_plan()

    # Show what's configured
    print("\nMount Plan Contents:")
    print(f"  Providers: {len(mount_plan.get('providers', []))}")
    print(f"  Tools: {len(mount_plan.get('tools', []))}")
    print(f"  Hooks: {len(mount_plan.get('hooks', []))}")
    print(f"  Agents: {len(mount_plan.get('agents', {}))}")

    # Show system instruction preview
    if instruction := bundle.get_system_instruction():
        preview = instruction[:100] + "..." if len(instruction) > 100 else instruction
        print(f"\nInstruction: {preview}")


if __name__ == "__main__":
    asyncio.run(main())
