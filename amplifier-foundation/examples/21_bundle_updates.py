#!/usr/bin/env python3
"""
Example 21: Bundle Updates (Status Checking & Updating)
=======================================================

AUDIENCE: Developers building apps with bundles loaded from git sources
VALUE: Shows how to detect and apply updates to bundles without rebuilding
PATTERN: Check status (no side effects) → Optionally update (side effects)

What this demonstrates:
  - Checking if bundle sources have updates available
  - Getting detailed status for each source in a bundle
  - Updating specific or all sources with updates
  - Foundation provides mechanism, app provides policy

When you'd use this:
  - CLI update commands ("check for updates", "update all")
  - Startup checks ("newer versions available")
  - CI/CD pipelines (ensure pinned versions haven't drifted)
  - Development workflows (pull latest from upstream)
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from amplifier_foundation import Bundle
from amplifier_foundation import BundleStatus
from amplifier_foundation import check_bundle_status
from amplifier_foundation import load_bundle
from amplifier_foundation import update_bundle
from amplifier_foundation.sources.protocol import SourceStatus

# ============================================================================
# Example 1: Basic Status Checking
# ============================================================================


async def check_status_example():
    """
    Demonstrate basic bundle status checking.

    This is a READ-ONLY operation - it checks for updates without
    downloading anything. Uses `git ls-remote` for git sources.
    """
    print("\n" + "=" * 80)
    print("EXAMPLE 1: Basic Status Checking")
    print("=" * 80)

    # Load a bundle from local path (simulates any bundle source)
    bundle_path = Path(__file__).parent.parent
    bundle = await load_bundle(str(bundle_path))

    print(f"\nBundle: {bundle.name}")
    print(f"Source: {getattr(bundle, '_source_uri', 'local')}")

    # Check status - NO SIDE EFFECTS
    print("\n[Checking for updates...]")
    status = await check_bundle_status(bundle)

    # Display summary
    print(f"\n{status.summary}")
    print(f"  Total sources: {len(status.sources)}")
    print(f"  Updates available: {len(status.updateable_sources)}")
    print(f"  Up to date: {len(status.up_to_date_sources)}")
    print(f"  Unknown: {len(status.unknown_sources)}")

    return status


# ============================================================================
# Example 2: Detailed Source Status
# ============================================================================


def display_source_status(source: SourceStatus, index: int):
    """Display detailed information about a single source."""
    status_icon = "🔄" if source.has_update else ("✅" if source.has_update is False else "❓")

    print(f"\n  [{index}] {status_icon} {source.source_uri}")
    print(f"      Cached: {source.is_cached}")

    if source.cached_at:
        print(f"      Cached at: {source.cached_at.isoformat()}")

    if source.cached_commit:
        print(f"      Local commit: {source.cached_commit[:8]}")

    if source.remote_commit:
        print(f"      Remote commit: {source.remote_commit[:8]}")

    if source.is_pinned:
        print("      Pinned: Yes (version tag or commit SHA)")

    print(f"      Summary: {source.summary}")

    if source.error:
        print(f"      Error: {source.error}")


async def detailed_status_example():
    """
    Show detailed status for each source in a bundle.

    This is useful for:
    - Debugging update issues
    - Understanding what will change
    - Building detailed UI displays
    """
    print("\n" + "=" * 80)
    print("EXAMPLE 2: Detailed Source Status")
    print("=" * 80)

    # Create a bundle with git sources for demonstration
    # In real usage, you'd load this from a bundle.yaml
    demo_bundle = Bundle(
        name="demo-with-git-sources",
        version="1.0.0",
        providers=[
            {
                "module": "provider-anthropic",
                "source": "git+https://github.com/microsoft/amplifier-module-provider-anthropic@main",
            }
        ],
        tools=[
            {
                "module": "tool-filesystem",
                "source": "git+https://github.com/microsoft/amplifier-module-tool-filesystem@main",
            }
        ],
    )

    print(f"\nBundle: {demo_bundle.name}")
    print("\nChecking status of all sources...")

    status = await check_bundle_status(demo_bundle)

    print(f"\nSources in bundle ({len(status.sources)}):")
    for i, source in enumerate(status.sources, 1):
        display_source_status(source, i)

    return status


# ============================================================================
# Example 3: Selective Update
# ============================================================================


async def selective_update_example():
    """
    Demonstrate updating specific sources.

    This shows the two-phase pattern:
    1. Check status (no side effects) - inform user
    2. Update selected sources (side effects) - user decides
    """
    print("\n" + "=" * 80)
    print("EXAMPLE 3: Selective Update Pattern")
    print("=" * 80)

    # Create demo bundle
    demo_bundle = Bundle(
        name="selective-refresh-demo",
        providers=[
            {
                "module": "provider-mock",
                "source": "git+https://github.com/microsoft/amplifier-module-provider-mock@main",
            }
        ],
    )

    # Phase 1: Check status
    print("\n[Phase 1: Checking status...]")
    status = await check_bundle_status(demo_bundle)

    if not status.has_updates:
        print("\nAll sources are up to date!")
        return

    # Display what's updateable
    print(f"\nUpdates available for {len(status.updateable_sources)} source(s):")
    for source in status.updateable_sources:
        print(f"  - {source.source_uri}")
        print(f"    {source.summary}")

    # Phase 2: App decides policy (interactive demo)
    print("\n[Phase 2: App decides what to update]")
    print("Options:")
    print("  1. Update all sources with updates")
    print("  2. Update specific sources only")
    print("  3. Skip update")

    # In real app, this would be user input or config
    # Here we demonstrate the selective API
    print("\nDemonstrating selective update of first source only...")

    if status.updateable_sources:
        first_source = status.updateable_sources[0].source_uri
        print(f"Refreshing: {first_source}")

        # Selective refresh - only specific URIs
        await update_bundle(demo_bundle, selective=[first_source])

        print("Done!")


# ============================================================================
# Example 4: Full Update Workflow
# ============================================================================


async def full_update_workflow():
    """
    Complete update workflow as an app might implement it.

    This shows how foundation provides mechanism while
    the app provides policy decisions.
    """
    print("\n" + "=" * 80)
    print("EXAMPLE 4: Full Update Workflow")
    print("=" * 80)

    # Simulating app loading its configured bundle
    print("\n[Loading bundle configuration...]")
    bundle_path = Path(__file__).parent.parent
    bundle = await load_bundle(str(bundle_path))

    print(f"Loaded: {bundle.name}")

    # Step 1: Check status
    print("\n[Step 1: Checking for updates...]")
    status = await check_bundle_status(bundle)

    # Step 2: Report findings
    print("\n[Step 2: Status Report]")
    print("-" * 40)
    print(f"Bundle: {status.bundle_name}")
    print(f"Status: {status.summary}")

    if status.updateable_sources:
        print("\nSources with updates:")
        for source in status.updateable_sources:
            print(f"  🔄 {source.source_uri}")
            print(f"     {source.summary}")

    if status.up_to_date_sources:
        print(f"\nSources up to date: {len(status.up_to_date_sources)}")

    if status.unknown_sources:
        print(f"\nSources with unknown status: {len(status.unknown_sources)}")
        for source in status.unknown_sources:
            print(f"  ❓ {source.source_uri}: {source.summary}")

    # Step 3: App policy decision
    print("\n[Step 3: Policy Decision]")
    if not status.has_updates:
        print("No action needed - all sources current")
        return None

    # App decides whether to auto-update or require confirmation
    # This is POLICY - foundation doesn't decide this
    auto_update = False  # Example: could be from config

    if auto_update:
        print("Auto-update enabled - refreshing...")
        await update_bundle(bundle)
        print("Bundle refreshed!")
    else:
        print("Manual update required - skipping refresh")
        print("(In real app: prompt user or respect config)")

    return status


# ============================================================================
# Example 5: Building a CLI Update Command
# ============================================================================


def print_status_table(status: BundleStatus):
    """Print a formatted status table."""
    print(f"\n{'Source':<60} {'Status':<20}")
    print("-" * 80)

    for source in status.sources:
        # Truncate long URIs
        uri = source.source_uri
        if len(uri) > 57:
            uri = uri[:54] + "..."

        if source.has_update:
            status_str = "🔄 Update available"
        elif source.has_update is False:
            status_str = "✅ Up to date"
        else:
            status_str = "❓ Unknown"

        print(f"{uri:<60} {status_str:<20}")


async def cli_update_command_example():
    """
    Example of how an app-cli update command might work.

    Pattern:
      amplifier update --check     # Just check, don't update
      amplifier update             # Check and update
      amplifier update --source X  # Update specific source
    """
    print("\n" + "=" * 80)
    print("EXAMPLE 5: CLI Update Command Pattern")
    print("=" * 80)

    # Simulate CLI flags
    check_only = True  # --check flag
    specific_source = None  # --source flag

    # Load current bundle
    bundle_path = Path(__file__).parent.parent
    bundle = await load_bundle(str(bundle_path))

    print(f"\nChecking bundle: {bundle.name}")

    # Always check first
    status = await check_bundle_status(bundle)

    # Display status table
    print_status_table(status)

    # Summary
    print(f"\n{status.summary}")

    if check_only:
        print("\n(--check flag: skipping refresh)")
        return status

    if not status.has_updates:
        print("\nNothing to update.")
        return status

    # Perform refresh
    if specific_source:
        print(f"\nRefreshing specific source: {specific_source}")
        await update_bundle(bundle, selective=[specific_source])
    else:
        print(f"\nRefreshing {len(status.updateable_sources)} source(s)...")
        await update_bundle(bundle)

    print("Update complete!")
    return status


# ============================================================================
# Interactive Menu
# ============================================================================


async def main():
    """Run interactive demo menu."""
    print("\n" + "=" * 80)
    print("🔄 Bundle Updates (Status Checking & Refresh)")
    print("=" * 80)
    print("\nVALUE: Detect and apply bundle updates without rebuilding")
    print("AUDIENCE: App developers building update workflows")
    print("\nWhat this demonstrates:")
    print("  - Checking bundle sources for updates (no side effects)")
    print("  - Getting detailed status per source")
    print("  - Refreshing sources selectively or all at once")
    print("  - Foundation mechanism + App policy pattern")

    examples = [
        ("Basic Status Checking", check_status_example),
        ("Detailed Source Status", detailed_status_example),
        ("Selective Update Pattern", selective_update_example),
        ("Full Update Workflow", full_update_workflow),
        ("CLI Update Command Pattern", cli_update_command_example),
    ]

    print("\n" + "=" * 80)
    print("Choose an example:")
    print("=" * 80)
    for i, (name, _) in enumerate(examples, 1):
        print(f"  {i}. {name}")
    print("  a. Run all examples")
    print("  q. Quit")
    print("-" * 80)

    choice = input("\nYour choice: ").strip().lower()

    if choice == "q":
        print("\n👋 Goodbye!")
        return

    if choice == "a":
        for name, func in examples:
            try:
                await func()
            except Exception as e:
                print(f"\n❌ Error in {name}: {e}")
    else:
        try:
            idx = int(choice) - 1
            if 0 <= idx < len(examples):
                _, example_func = examples[idx]
                await example_func()
            else:
                print("\n❌ Invalid choice")
        except ValueError:
            print("\n❌ Invalid choice")

    # Key takeaways
    print("\n" + "=" * 80)
    print("💡 KEY TAKEAWAYS")
    print("=" * 80)
    print(
        """
1. **Two-Phase Pattern**: Check (no side effects) → Update (side effects)
   - check_bundle_status() - safe to call anytime
   - update_bundle() - downloads updates and reinstalls dependencies

2. **Foundation = Mechanism, App = Policy**:
   - Foundation provides: status checking, update capability
   - App decides: when to check, whether to auto-update, which sources

3. **Detailed Status Information**:
   - cached_at, cached_commit, remote_commit
   - has_update: True/False/None (unknown)
   - is_pinned: detects version tags and commit SHAs

4. **Selective Update**:
   - Update all: await update_bundle(bundle)
   - Update specific: await update_bundle(bundle, selective=[uri1, uri2])

5. **Git Source Support**:
   - Uses `git ls-remote` for efficient status checking
   - Shallow clones for minimal bandwidth
   - Metadata tracking for cache freshness

**Production Implementation**:

```python
from amplifier_foundation import load_bundle, check_bundle_status, update_bundle

# In your CLI update command:
bundle = await load_bundle("your-bundle-uri")
status = await check_bundle_status(bundle)

if status.has_updates:
    print(f"Updates available: {status.summary}")
    if user_confirms_update():
        await update_bundle(bundle)
        print("Updated!")
```
"""
    )


if __name__ == "__main__":
    asyncio.run(main())
