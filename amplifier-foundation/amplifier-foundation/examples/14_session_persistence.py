#!/usr/bin/env python3
"""
Example 14: Session Persistence & Resume
========================================

VALUE PROPOSITION:
Demonstrates how to persist conversation history across sessions. Long-running
tasks or conversations can be saved and resumed later, maintaining full context.

WHAT YOU'LL LEARN:
- Context persistence patterns
- Session state management
- Resume capability
- Long-running workflow patterns
- Manual persistence implementation

AUDIENCE:
Developers and PMs working with stateful workflows
"""

import asyncio
import json
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

from amplifier_foundation import load_bundle

# =============================================================================
# SECTION 1: Simple Persistence Implementation
# =============================================================================


class SimpleSessionPersistence:
    """Simple persistence manager for session state.

    Demonstrates the persistence pattern even if context-persistent
    module is not available.
    """

    def __init__(self, storage_path: Path):
        self.storage_path = storage_path
        self.messages: list[dict[str, Any]] = []
        self.metadata: dict[str, Any] = {}

    def save(self):
        """Save session state to disk."""
        state = {"metadata": self.metadata, "messages": self.messages, "saved_at": datetime.now().isoformat()}

        with open(self.storage_path, "w") as f:
            json.dump(state, f, indent=2)

    def load(self) -> bool:
        """Load session state from disk.

        Returns:
            True if state was loaded, False if no state exists
        """
        if not self.storage_path.exists():
            return False

        with open(self.storage_path) as f:
            state = json.load(f)

        self.metadata = state.get("metadata", {})
        self.messages = state.get("messages", [])

        return True

    def add_message(self, role: str, content: str):
        """Add a message to the conversation history."""
        self.messages.append({"role": role, "content": content, "timestamp": datetime.now().isoformat()})

    def get_context_summary(self) -> str:
        """Get a summary of the conversation for resuming."""
        if not self.messages:
            return ""

        summary_lines = ["Previous conversation:"]
        for msg in self.messages[-5:]:  # Last 5 messages
            role = msg["role"]
            content = msg["content"][:100]
            summary_lines.append(f"[{role}]: {content}...")

        return "\n".join(summary_lines)


# =============================================================================
# SECTION 2: Persistent Workflow
# =============================================================================


async def run_persistent_workflow(session_id: str = "demo-workflow"):
    """Run a multi-step workflow with persistence.

    Args:
        session_id: Unique session identifier
    """
    storage_path = Path.home() / ".amplifier" / "demo_sessions"
    storage_path.mkdir(parents=True, exist_ok=True)

    state_file = storage_path / f"{session_id}.json"

    # Initialize persistence
    persistence = SimpleSessionPersistence(state_file)
    is_resuming = persistence.load()

    if is_resuming:
        print("\n" + "=" * 60)
        print("🔄 RESUMING Previous Session")
        print("=" * 60)
        print(f"Loaded state from: {state_file}")
        print(f"Messages in history: {len(persistence.messages)}")
        persistence.metadata["resume_count"] = persistence.metadata.get("resume_count", 0) + 1
        print(f"Times resumed: {persistence.metadata['resume_count']}")
    else:
        print("\n" + "=" * 60)
        print("🆕 NEW Session")
        print("=" * 60)
        print(f"State will be saved to: {state_file}")
        persistence.metadata = {"session_id": session_id, "created_at": datetime.now().isoformat(), "resume_count": 0}

    # Create Amplifier session
    foundation_path = Path(__file__).parent.parent
    foundation = await load_bundle(str(foundation_path))
    provider = await load_bundle(str(foundation_path / "providers" / "anthropic-sonnet.yaml"))

    composed = foundation.compose(provider)

    print("⏳ Preparing session...")
    prepared = await composed.prepare()
    session = await prepared.create_session()

    async with session:
        if is_resuming:
            # Resume: Show context and continue
            print("\n📜 Previous conversation:")
            print("-" * 60)
            for _i, msg in enumerate(persistence.messages[-3:], 1):
                role_emoji = "👤" if msg["role"] == "user" else "🤖"
                content = msg["content"][:150]
                print(f"{role_emoji} [{msg['role']}]: {content}...")
            print("-" * 60)

            # Get continuation
            context_summary = persistence.get_context_summary()
            prompt = input("\n💬 Continue conversation (or type 'quit' to exit): ")

            if prompt.lower() == "quit":
                print("👋 Saving and exiting...")
                persistence.save()
                return

            full_prompt = f"{context_summary}\n\nUser continues: {prompt}"
            persistence.add_message("user", prompt)

            print("\n🤖 Agent:")
            response = await session.execute(full_prompt)
            print(response)

            persistence.add_message("assistant", response)

        else:
            # New session: Start multi-step task
            print("\n📝 Step 1: Starting research task")
            print("   This simulates a long-running workflow")

            prompt = "I'm researching Python async patterns. First, explain asyncio.gather() in 2-3 sentences."
            persistence.add_message("user", prompt)

            response = await session.execute(prompt)

            print("\n✓ Step 1 Complete")
            print(f"Response: {response[:200]}...")

            persistence.add_message("assistant", response)

            # Simulate we want to continue later
            print("\n💾 Saving session state...")

    # Save state after session closes
    persistence.save()
    print(f"✓ State saved to: {state_file}")
    print(f"   Size: {state_file.stat().st_size} bytes")
    print(f"   Messages: {len(persistence.messages)}")

    if not is_resuming:
        print("\n" + "=" * 60)
        print("💡 TO RESUME THIS SESSION:")
        print("=" * 60)
        print(f"Run: python {Path(__file__).name}")
        print("\nThe session will load the saved state and you can continue!")


# =============================================================================
# SECTION 3: State Management Tools
# =============================================================================


async def inspect_state(session_id: str = "demo-workflow"):
    """Inspect saved session state."""
    storage_path = Path.home() / ".amplifier" / "demo_sessions"
    state_file = storage_path / f"{session_id}.json"

    if not state_file.exists():
        print(f"❌ No saved state found: {state_file}")
        return

    with open(state_file) as f:
        state = json.load(f)

    print("\n" + "=" * 60)
    print("🔍 Session State Inspection")
    print("=" * 60)
    print(f"File: {state_file}")
    print(f"Size: {state_file.stat().st_size} bytes")

    metadata = state.get("metadata", {})
    messages = state.get("messages", [])

    print("\nMetadata:")
    print(f"  Session ID: {metadata.get('session_id')}")
    print(f"  Created: {metadata.get('created_at')}")
    print(f"  Resumed: {metadata.get('resume_count', 0)} times")
    print(f"  Last saved: {state.get('saved_at')}")

    print(f"\nMessages: {len(messages)}")

    # Show message breakdown
    roles = {}
    for msg in messages:
        role = msg.get("role", "unknown")
        roles[role] = roles.get(role, 0) + 1

    for role, count in roles.items():
        print(f"  {role}: {count}")

    # Show recent messages
    print("\n📜 Recent conversation:")
    for msg in messages[-3:]:
        role_emoji = "👤" if msg["role"] == "user" else "🤖"
        content = msg["content"][:100]
        print(f"  {role_emoji} [{msg['role']}]: {content}...")


async def clear_state(session_id: str = "demo-workflow"):
    """Clear saved session state."""
    storage_path = Path.home() / ".amplifier" / "demo_sessions"
    state_file = storage_path / f"{session_id}.json"

    if state_file.exists():
        state_file.unlink()
        print(f"✓ Cleared state: {state_file}")
    else:
        print("ℹ️  No state to clear")


# =============================================================================
# SECTION 4: Main Entry Point
# =============================================================================


async def main():
    """Main entry point."""

    print("🚀 Session Persistence & Resume Demo")
    print("=" * 60)

    # Check prerequisites
    if not os.getenv("ANTHROPIC_API_KEY"):
        print("\n❌ ERROR: Set ANTHROPIC_API_KEY environment variable")
        return

    # Parse mode
    mode = sys.argv[1] if len(sys.argv) > 1 else "run"

    if mode == "inspect":
        await inspect_state()
    elif mode == "clear":
        await clear_state()
    else:
        await run_persistent_workflow()

    # Summary
    print("\n" + "=" * 60)
    print("📚 WHAT YOU LEARNED:")
    print("=" * 60)
    print("  ✓ Persist conversation history to disk")
    print("  ✓ Resume sessions by loading saved state")
    print("  ✓ Maintain context across interruptions")
    print("  ✓ Build reliable, stateful workflows")

    print("\n💡 PATTERN:")
    print("  1. Save messages to JSON after each turn")
    print("  2. Load messages on resume")
    print("  3. Provide context summary to LLM")
    print("  4. Continue conversation seamlessly")


if __name__ == "__main__":
    asyncio.run(main())
