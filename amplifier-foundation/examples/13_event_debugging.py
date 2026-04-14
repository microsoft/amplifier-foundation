#!/usr/bin/env python3
"""
Example 13: Event-Driven Debugging
===================================

AUDIENCE: Developers debugging Amplifier integrations or building custom modules
VALUE: Shows how to observe and debug Amplifier's internal event flow
PATTERN: Hook-based observability and debugging

What this demonstrates:
  - How to observe all events flowing through Amplifier
  - Debugging tool execution, context updates, and provider calls
  - Understanding the event lifecycle
  - Building custom debugging and monitoring tools

When you'd use this:
  - Debugging why a tool isn't being called
  - Understanding what data flows between components
  - Building monitoring and observability tools
  - Troubleshooting integration issues
  - Learning how Amplifier works internally
"""

import asyncio
import json
from datetime import datetime
from pathlib import Path
from typing import Any

from amplifier_core import AmplifierSession
from amplifier_core import HookResult
from amplifier_foundation import load_bundle

# ============================================================================
# Event Debugging Utilities
# ============================================================================


class EventLogger:
    """
    Comprehensive event logger that captures all Amplifier events.

    Features:
    - Logs all events with timestamps
    - Pretty-prints event data
    - Filters events by pattern
    - Shows event flow and timing
    """

    def __init__(self, filter_pattern: str | None = None, verbose: bool = True):
        self.filter_pattern = filter_pattern
        self.verbose = verbose
        self.events: list[dict[str, Any]] = []
        self.start_time = datetime.now()

    async def log_event(self, event: str, data: dict[str, Any]) -> HookResult:
        """Log an event."""
        # Filter if pattern specified
        if self.filter_pattern and self.filter_pattern not in event:
            return HookResult(action="continue")

        # Record event
        timestamp = datetime.now()
        elapsed = (timestamp - self.start_time).total_seconds()

        event_record = {
            "event": event,
            "timestamp": timestamp.isoformat(),
            "elapsed": elapsed,
            "data": data,
        }
        self.events.append(event_record)

        # Print if verbose
        if self.verbose:
            self._print_event(event, data, elapsed)

        return HookResult(action="continue")

    def _print_event(self, event: str, data: dict[str, Any], elapsed: float):
        """Pretty-print an event."""
        print(f"\n[{elapsed:6.2f}s] 📡 {event}")

        # Print key data points based on event type
        if "tool:" in event:
            if "name" in data:
                print(f"           Tool: {data['name']}")
            if "arguments" in data:
                print(f"           Args: {self._truncate_json(data['arguments'])}")
            if "result" in data:
                result_str = self._truncate_str(str(data.get("result", "")))
                print(f"           Result: {result_str}")

        elif "content_block:" in event:
            if "type" in data:
                print(f"           Type: {data['type']}")
            if "text" in data:
                text = self._truncate_str(data["text"])
                print(f"           Text: {text}")

        elif "context:" in event:
            if "message_count" in data:
                print(f"           Messages: {data['message_count']}")
            if "token_count" in data:
                print(f"           Tokens: {data['token_count']}")

        elif "session:" in event:
            if "status" in data:
                print(f"           Status: {data['status']}")

        # Print all data in verbose mode
        if len(data) > 0 and self.verbose:
            # Filter out large/noisy fields
            filtered_data = {
                k: v for k, v in data.items() if k not in ["text", "arguments", "result"] and len(str(v)) < 100
            }
            if filtered_data:
                print(f"           Data: {json.dumps(filtered_data, indent=19)}")

    def _truncate_str(self, s: str, max_len: int = 80) -> str:
        """Truncate a string for display."""
        if len(s) <= max_len:
            return s
        return s[:max_len] + "..."

    def _truncate_json(self, obj: Any, max_len: int = 100) -> str:
        """Truncate JSON for display."""
        s = json.dumps(obj)
        return self._truncate_str(s, max_len)

    def print_summary(self):
        """Print summary of captured events."""
        if not self.events:
            print("\n📊 No events captured")
            return

        print("\n" + "=" * 80)
        print("📊 EVENT SUMMARY")
        print("=" * 80)

        # Count events by type
        event_counts: dict[str, int] = {}
        for record in self.events:
            event = record["event"]
            event_counts[event] = event_counts.get(event, 0) + 1

        print(f"\nTotal events: {len(self.events)}")
        print(f"Duration: {self.events[-1]['elapsed']:.2f}s")
        print("\nEvent breakdown:")
        for event, count in sorted(event_counts.items()):
            print(f"  {event:40} {count:3} events")

    def save_to_file(self, filename: str):
        """Save events to JSON file."""
        with open(filename, "w") as f:
            json.dump(self.events, f, indent=2)
        print(f"\n💾 Saved {len(self.events)} events to {filename}")


class EventFilter:
    """
    Selective event filter for focused debugging.

    Features:
    - Filter events by category (tool, context, session, etc.)
    - Combine multiple filters
    - Show only specific event types
    """

    def __init__(self, include: list[str] | None = None, exclude: list[str] | None = None):
        self.include = include or []
        self.exclude = exclude or []

    async def filter_event(self, event: str, data: dict[str, Any]) -> HookResult:
        """Filter events based on include/exclude rules."""
        # Check exclude patterns first
        for pattern in self.exclude:
            if pattern in event:
                return HookResult(action="continue")  # Skip this event

        # Check include patterns
        if self.include:
            for pattern in self.include:
                if pattern in event:
                    print(f"✓ {event}: {self._summarize_data(data)}")
                    return HookResult(action="continue")
            # Not in include list, skip
            return HookResult(action="continue")

        # No filters, pass through
        print(f"✓ {event}: {self._summarize_data(data)}")
        return HookResult(action="continue")

    def _summarize_data(self, data: dict[str, Any]) -> str:
        """Create a one-line summary of event data."""
        if "name" in data:
            return f"name={data['name']}"
        if "type" in data:
            return f"type={data['type']}"
        if "status" in data:
            return f"status={data['status']}"
        return f"{len(data)} fields"


# ============================================================================
# Demo Scenarios
# ============================================================================


async def scenario_full_event_trace():
    """
    Scenario: Capture complete event trace of a session.

    Shows every event that flows through Amplifier.
    """
    print("\n" + "=" * 80)
    print("SCENARIO 1: Full Event Trace")
    print("=" * 80)
    print("\nCaptures ALL events during a simple task.")
    print("Watch how tools, context, and content blocks flow through the system.")
    print("-" * 80)

    # Load foundation
    foundation_path = Path(__file__).parent.parent
    foundation = await load_bundle(str(foundation_path))

    # Create event logger
    logger = EventLogger(verbose=True)

    # Create session with event logging hook
    mount_plan = foundation.to_mount_plan()
    session = AmplifierSession(config=mount_plan)

    # Register logger for ALL events
    session.coordinator.hooks.register("*", logger.log_event)

    await session.initialize()

    prompt = "List all Python files in the current directory using glob."

    print(f"\n📝 Prompt: {prompt}")
    print("\n" + "-" * 80)
    print("EVENT STREAM:")
    print("-" * 80)

    try:
        await session.execute(prompt)
        print("\n" + "=" * 80)
        print("✅ Task completed")
        print("=" * 80)

        # Show summary
        logger.print_summary()

        # Offer to save
        save = input("\n💾 Save event trace to file? (y/n): ").strip().lower()
        if save == "y":
            filename = f"event_trace_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
            logger.save_to_file(filename)

    finally:
        await session.cleanup()


async def scenario_tool_debugging():
    """
    Scenario: Debug tool execution flow.

    Shows only tool-related events for focused debugging.
    """
    print("\n" + "=" * 80)
    print("SCENARIO 2: Tool Execution Debugging")
    print("=" * 80)
    print("\nFilters events to show only tool-related activity.")
    print("Perfect for debugging why tools aren't being called or are failing.")
    print("-" * 80)

    # Load foundation
    foundation_path = Path(__file__).parent.parent
    foundation = await load_bundle(str(foundation_path))

    # Create focused logger
    logger = EventLogger(filter_pattern="tool:", verbose=True)

    # Create session
    mount_plan = foundation.to_mount_plan()
    session = AmplifierSession(config=mount_plan)

    # Register tool-focused logger
    session.coordinator.hooks.register("*", logger.log_event)

    await session.initialize()

    prompt = """Do these tasks:
1. Use glob to find all .py files
2. Use read_file to read this script's main function
3. Use grep to search for 'async def' in current directory"""

    print(f"\n📝 Prompt: {prompt}")
    print("\n" + "-" * 80)
    print("TOOL EVENTS ONLY:")
    print("-" * 80)

    try:
        await session.execute(prompt)
        print("\n" + "=" * 80)
        print("✅ Task completed")
        print("=" * 80)

        logger.print_summary()

    finally:
        await session.cleanup()


async def scenario_selective_filtering():
    """
    Scenario: Use selective filters for specific debugging.

    Shows how to include/exclude specific event patterns.
    """
    print("\n" + "=" * 80)
    print("SCENARIO 3: Selective Event Filtering")
    print("=" * 80)
    print("\nDemonstrates include/exclude filters for focused debugging.")
    print("Useful when you know what you're looking for.")
    print("-" * 80)

    # Load foundation
    foundation_path = Path(__file__).parent.parent
    foundation = await load_bundle(str(foundation_path))

    # Create selective filter (only content blocks, exclude deltas)
    event_filter = EventFilter(include=["content_block:"], exclude=["content_block:delta"])

    # Create session
    mount_plan = foundation.to_mount_plan()
    session = AmplifierSession(config=mount_plan)

    # Register filter
    session.coordinator.hooks.register("*", event_filter.filter_event)

    await session.initialize()

    prompt = "Explain what Amplifier is in one sentence."

    print(f"\n📝 Prompt: {prompt}")
    print("\n" + "-" * 80)
    print("FILTERED EVENTS (content blocks, no deltas):")
    print("-" * 80)

    try:
        await session.execute(prompt)
        print("\n" + "=" * 80)
        print("✅ Task completed")
        print("=" * 80)

    finally:
        await session.cleanup()


# ============================================================================
# Interactive Menu
# ============================================================================


async def main():
    """Run interactive demo menu."""
    print("\n" + "=" * 80)
    print("🔍 Event-Driven Debugging")
    print("=" * 80)
    print("\nVALUE: Understand and debug Amplifier's internal event flow")
    print("AUDIENCE: Developers building with Amplifier")
    print("\nWhat this demonstrates:")
    print("  - Observing all events in an Amplifier session")
    print("  - Debugging tool execution and failures")
    print("  - Filtering events for focused debugging")
    print("  - Building custom monitoring and observability")

    scenarios = [
        ("Full Event Trace (see everything)", scenario_full_event_trace),
        ("Tool Debugging (tool events only)", scenario_tool_debugging),
        ("Selective Filtering (custom filters)", scenario_selective_filtering),
    ]

    print("\n" + "=" * 80)
    print("Choose a scenario:")
    print("=" * 80)
    for i, (name, _) in enumerate(scenarios, 1):
        print(f"  {i}. {name}")
    print("  q. Quit")
    print("-" * 80)

    choice = input("\nYour choice: ").strip().lower()

    if choice == "q":
        print("\n👋 Goodbye!")
        return

    try:
        idx = int(choice) - 1
        if 0 <= idx < len(scenarios):
            _, scenario_func = scenarios[idx]
            await scenario_func()
        else:
            print("\n❌ Invalid choice")
    except ValueError:
        print("\n❌ Invalid choice")

    print("\n" + "=" * 80)
    print("💡 KEY TAKEAWAYS")
    print("=" * 80)
    print("""
1. **Hook into Everything**: Register hooks with "*" pattern to see all events
2. **Filter Strategically**: Use patterns to focus on specific event types
3. **Event Naming**: Events follow "category:action" pattern (tool:pre, context:update)
4. **Debugging Flow**: Watch the sequence to understand what's happening when
5. **Build Monitoring**: Use the same patterns for production observability

**Common debugging patterns:**
- Tool not called? → Filter "tool:" events to see what's registered
- Wrong context? → Filter "context:" to see updates
- Performance issues? → Log timestamps to find bottlenecks
- Integration issues? → Full trace shows where data flow breaks

**Event categories:**
- session:* - Session lifecycle (start, end, status)
- tool:* - Tool execution (pre, post, error)
- content_block:* - LLM output streaming (start, delta, end)
- context:* - Conversation context (update, compact)
- provider:* - LLM provider calls (pre, post, error)
""")


if __name__ == "__main__":
    asyncio.run(main())
