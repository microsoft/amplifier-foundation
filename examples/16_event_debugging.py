#!/usr/bin/env python3
"""
Example 13: Event-Driven Debugging
==================================

VALUE PROPOSITION:
Teaches how Amplifier works internally by visualizing the complete event flow.
Essential for power users who want to understand and debug agent behavior.

WHAT YOU'LL LEARN:
- Hook system mastery for debugging
- Complete observability of Amplifier internals
- Event stream visualization
- Debugging techniques for complex agent behavior
- How to export and analyze event logs

AUDIENCE:
Developers (advanced), anyone wanting to understand Amplifier internals
"""

import asyncio
import json
import os
from datetime import datetime
from pathlib import Path
from typing import Any

from amplifier_core import HookResult
from amplifier_foundation import Bundle, load_bundle


# =============================================================================
# SECTION 1: Event Debugger Implementation
# =============================================================================

class EventDebugger:
    """Comprehensive event debugger that captures and visualizes all events."""
    
    # Color codes for terminal output
    COLORS = {
        "session": "\033[95m",  # Magenta
        "turn": "\033[94m",     # Blue
        "provider": "\033[92m",  # Green
        "tool": "\033[93m",      # Yellow
        "content": "\033[96m",   # Cyan
        "hook": "\033[91m",      # Red
        "reset": "\033[0m"
    }
    
    def __init__(self, filter_patterns: list[str] = None, verbose: bool = True):
        """Initialize event debugger.
        
        Args:
            filter_patterns: List of event prefixes to capture (None = all)
            verbose: Whether to print events in real-time
        """
        self.filter_patterns = filter_patterns
        self.verbose = verbose
        self.events = []
        self.start_time = datetime.now()
    
    def _should_capture(self, event: str) -> bool:
        """Check if event matches filter patterns."""
        if not self.filter_patterns:
            return True
        
        for pattern in self.filter_patterns:
            if event.startswith(pattern):
                return True
        
        return False
    
    def _get_color(self, event: str) -> str:
        """Get color code for event type."""
        for prefix, color in self.COLORS.items():
            if event.startswith(prefix):
                return color
        return self.COLORS["reset"]
    
    def _abbreviate_data(self, data: dict, max_length: int = 100) -> str:
        """Abbreviate event data for display."""
        data_str = json.dumps(data, default=str)
        
        if len(data_str) <= max_length:
            return data_str
        
        return data_str[:max_length] + "..."
    
    async def __call__(self, event: str, data: dict) -> HookResult:
        """Hook handler that captures all events.
        
        Args:
            event: Event name
            data: Event data
            
        Returns:
            HookResult with action='continue'
        """
        # Check if we should capture this event
        if not self._should_capture(event):
            return HookResult(action="continue")
        
        # Capture event with timestamp
        timestamp = datetime.now()
        elapsed = (timestamp - self.start_time).total_seconds()
        
        event_record = {
            "timestamp": timestamp.isoformat(),
            "elapsed_seconds": elapsed,
            "event": event,
            "data": data
        }
        
        self.events.append(event_record)
        
        # Print in real-time if verbose
        if self.verbose:
            color = self._get_color(event)
            reset = self.COLORS["reset"]
            abbreviated_data = self._abbreviate_data(data)
            
            print(f"{color}[{elapsed:>6.2f}s] {event:30s}{reset} {abbreviated_data}")
        
        return HookResult(action="continue")
    
    def get_events(self, filter_patterns: list[str] = None) -> list[dict]:
        """Get captured events, optionally filtered."""
        if not filter_patterns:
            return self.events
        
        filtered = []
        for event_record in self.events:
            for pattern in filter_patterns:
                if event_record["event"].startswith(pattern):
                    filtered.append(event_record)
                    break
        
        return filtered
    
    def export_to_json(self, filepath: str):
        """Export events to JSON file."""
        with open(filepath, 'w') as f:
            json.dump(self.events, f, indent=2, default=str)
        
        print(f"\n✅ Events exported to: {filepath}")
    
    def print_summary(self):
        """Print summary statistics of captured events."""
        print("\n" + "="*60)
        print("📊 Event Summary")
        print("="*60)
        
        # Count events by type
        event_counts = {}
        for record in self.events:
            event_type = record["event"].split(":")[0]
            event_counts[event_type] = event_counts.get(event_type, 0) + 1
        
        print(f"\nTotal events captured: {len(self.events)}")
        if self.events:
            print(f"Duration: {self.events[-1]['elapsed_seconds']:.2f}s")
        print("\nEvents by type:")
        for event_type, count in sorted(event_counts.items()):
            print(f"  {event_type:20s}: {count:3d}")
    
    def print_event_flow(self):
        """Print simplified event flow diagram."""
        print("\n" + "="*60)
        print("🔄 Event Flow Diagram")
        print("="*60)
        print()
        
        prev_type = None
        for record in self.events:
            event = record["event"]
            event_type = event.split(":")[0]
            
            # Add separator between different event types
            if prev_type and prev_type != event_type:
                print("  ↓")
            
            color = self._get_color(event)
            reset = self.COLORS["reset"]
            elapsed = record["elapsed_seconds"]
            
            print(f"{color}[{elapsed:>6.2f}s] {event}{reset}")
            
            prev_type = event_type


# =============================================================================
# SECTION 2: Demo Scenario
# =============================================================================

async def demo_event_debugging():
    """Demonstrate event debugging."""
    
    print("\n" + "="*60)
    print("Demo: Event-Driven Debugging")
    print("="*60)
    print("\nCapturing all tool and provider events...")
    
    # Load foundation and provider
    foundation_path = Path(__file__).parent.parent
    foundation = await load_bundle(str(foundation_path))
    provider = await load_bundle(str(foundation_path / "providers" / "anthropic-sonnet.yaml"))
    
    # Add filesystem tool
    tools_config = Bundle(
        name="tools-with-filesystem",
        version="1.0.0",
        tools=[
            {
                "module": "tool-filesystem",
                "source": "git+https://github.com/microsoft/amplifier-module-tool-filesystem@main"
            }
        ]
    )
    
    composed = foundation.compose(provider).compose(tools_config)
    
    print("⏳ Preparing session...")
    prepared = await composed.prepare()
    session = await prepared.create_session()
    
    # Create event debugger
    debugger = EventDebugger(filter_patterns=["tool", "provider"], verbose=True)
    
    # Register debugger hook for tool and provider events
    # Note: Must pass the method reference .__call__
    session.coordinator.hooks.register("tool:pre", debugger.__call__)
    session.coordinator.hooks.register("tool:post", debugger.__call__)
    session.coordinator.hooks.register("tool:error", debugger.__call__)
    session.coordinator.hooks.register("provider:request", debugger.__call__)
    session.coordinator.hooks.register("provider:response", debugger.__call__)
    session.coordinator.hooks.register("provider:error", debugger.__call__)
    
    print("\n" + "="*60)
    print("🎬 Starting Task Execution")
    print("="*60)
    
    async with session:
        response = await session.execute(
            "Read the file at ./examples/README.md and tell me what the first example is about."
        )
        
        print("\n" + "="*60)
        print("📤 Agent Response")
        print("="*60)
        print(f"\n{response[:300]}...")
    
    # Print summary and flow
    debugger.print_summary()
    debugger.print_event_flow()
    
    # Export to JSON
    export_path = Path(__file__).parent / "event_log.json"
    debugger.export_to_json(str(export_path))
    
    return debugger


# =============================================================================
# SECTION 3: Main Entry Point
# =============================================================================

async def main():
    """Main entry point."""
    
    print("🔍 Event-Driven Debugging with Amplifier")
    print("="*60)
    print("\nVALUE: Understand Amplifier internals through complete observability")
    print("AUDIENCE: Developers (advanced), power users")
    print("\nWhat this demonstrates:")
    print("  - Hook system for complete observability")
    print("  - Real-time event visualization")
    print("  - Event filtering and analysis")
    print("  - Debugging complex agent behavior")
    print("  - Export capabilities for post-analysis")
    
    # Check prerequisites
    if not os.getenv("ANTHROPIC_API_KEY"):
        print("\n❌ ERROR: Set ANTHROPIC_API_KEY environment variable")
        print("\nExample:")
        print("  export ANTHROPIC_API_KEY='your-key-here'")
        print("  python 13_event_debugging.py")
        return
    
    # Run demo
    print("\n" + "="*60)
    print("🎯 Running Event Debugging Demo")
    print("="*60)
    
    debugger = await demo_event_debugging()
    
    # Final summary
    print("\n" + "="*60)
    print("📚 WHAT YOU LEARNED:")
    print("="*60)
    print("  ✓ How to capture events with hooks")
    print("  ✓ Real-time event visualization with colors")
    print("  ✓ Filtering events by pattern (tool, provider, etc.)")
    print("  ✓ Event flow analysis and debugging")
    print("  ✓ Exporting events to JSON for analysis")
    
    print("\n💡 KEY INSIGHTS:")
    print("  • Register hooks with specific event names")
    print("  • Pass method reference (.__call__) not object")
    print("  • Events show the complete flow through the system")
    print("  • Use filters to focus on specific subsystems")
    print("  • Export to JSON for post-mortem debugging")
    
    print("\n💡 DEBUGGING WORKFLOW:")
    print("  1. Run with EventDebugger to capture events")
    print("  2. Identify unexpected behavior in event sequence")
    print("  3. Use filters to isolate problematic subsystem")
    print("  4. Export and analyze event data")
    print("  5. Fix issue and verify with clean event flow")
    
    print("\n✅ You now understand Amplifier's event system!")
    print("   Use this for debugging, monitoring, and building custom hooks.")


if __name__ == "__main__":
    asyncio.run(main())
