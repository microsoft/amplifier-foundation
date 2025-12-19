#!/usr/bin/env python3
"""
Example 21: Custom Orchestrator (Model Routing)
===============================================

AUDIENCE: Teams wanting cost/latency control without changing core modules
VALUE: Shows how to build and use a custom orchestrator module that routes
       between GPT-5 Mini and GPT-5 Codex based on the prompt.

What this demonstrates:
  - Packaging an orchestrator module (mount() + Orchestrator protocol)
  - Swapping the session orchestrator via bundle composition
  - Capturing routing decisions (model + latency) via hooks

Run me:
  export OPENAI_API_KEY="sk-..."
  uv run python examples/21_custom_orchestrator_routing.py [--verbose]
"""

from __future__ import annotations

import asyncio
import os
import argparse
from dataclasses import dataclass
import logging
from pathlib import Path
from typing import Literal

from amplifier_core import HookResult
from amplifier_foundation import Bundle, load_bundle


@dataclass
class Task:
    """Simple task envelope for demonstration."""

    id: str
    prompt: str
    kind: Literal["code", "analysis", "general"] = "general"
    importance: Literal["low", "medium", "high"] = "medium"
    prefer_mini_first: bool = True


class RoutingObserver:
    """Hook to capture routing decisions emitted by the custom orchestrator."""

    def __init__(self):
        self.decisions: list[dict[str, object]] = []

    async def on_orchestrator_turn_complete(self, event: str, data: dict) -> HookResult:
        # Keep a simple history of turn decisions
        self.decisions.append(data or {})
        return HookResult(action="continue")


async def main():
    """Demonstrate routing via the custom orchestrator module."""
    parser = argparse.ArgumentParser(description="Custom orchestrator routing demo")
    parser.add_argument("--verbose", action="store_true", help="Show all router logs and enable raw_debug")
    args = parser.parse_args()

    if not os.getenv("OPENAI_API_KEY"):
        print("❌ Set OPENAI_API_KEY before running this example.")
        print("   export OPENAI_API_KEY='your-key'")
        return

    # Logging: by default only show the router selection line.
    # With --verbose, show all logs (including escalation) and enable raw_debug.
    if not logging.getLogger().handlers:
        if args.verbose:
            logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
        else:
            logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")
            router_logger = logging.getLogger("amplifier_module_router_orchestrator")
            router_logger.setLevel(logging.INFO)
            router_logger.propagate = False
            handler = logging.StreamHandler()
            handler.setLevel(logging.INFO)
            handler.setFormatter(logging.Formatter("%(levelname)s %(name)s: %(message)s"))
            router_logger.addHandler(handler)

    repo_root = Path(__file__).parent.parent

    # Base + provider bundles (all standard amplifier-foundation)
    foundation = await load_bundle(str(repo_root / "bundles" / "minimal.yaml"))
    openai_provider = await load_bundle(str(repo_root / "providers" / "openai-gpt.yaml"))

    # Overlay: use our local orchestrator module for model routing
    router_overlay = Bundle(
        name="router-overlay",
        description="Example-only custom orchestrator module (routing mini vs codex)",
        session={
            "orchestrator": {
                "module": "router-orchestrator",
                "source": str(repo_root / "examples" / "modules" / "router-orchestrator"),
                "config": {
                    "mini_model": "gpt-5-mini",
                    "codex_model": "gpt-5.1-codex",
                    "prefer_mini_first": True,
                    "raw_debug": args.verbose,  # set via --verbose to see all router logs
                },
            }
        },
    )

    # Compose and prepare
    composed = foundation.compose(openai_provider, router_overlay)
    prepared = await composed.prepare()
    print("\n🔧 Mounted orchestrator:", prepared.mount_plan["session"]["orchestrator"])

    # Demo tasks
    tasks = [
        Task(
            id="summary",
            prompt="Give me a 3-bullet summary of the latest Python logging best practices.",
            kind="analysis",
            importance="low",
            prefer_mini_first=True,
        ),
        Task(
            id="codegen",
            prompt="Write a Python function that parses a CSV of users into Pydantic models with validation.",
            kind="code",
            importance="high",
            prefer_mini_first=False,
        ),
        Task(
            id="refactor",
            prompt="Refactor this snippet for clarity and add type hints:\n```python\n"
            "def load_cfg(p):\n    import json\n    return json.loads(open(p).read())\n```",
            kind="code",
            importance="medium",
            prefer_mini_first=True,
        ),
    ]

    # Single session to accumulate context across tasks
    session = await prepared.create_session()
    observer = RoutingObserver()
    session.coordinator.hooks.register(
        "orchestrator:turn_complete", observer.on_orchestrator_turn_complete, name="routing-observer"
    )

    async with session:
        for idx, task in enumerate(tasks, 1):
            print(f"\n🧭 Task {idx} '{task.id}'")
            response = await session.execute(task.prompt)

            decision = observer.decisions[-1] if observer.decisions else {}
            model = decision.get("model", "unknown")
            latency = decision.get("latency_s", 0.0)

            preview = response[:400].replace("\n\n", "\n")
            print(f"   Model: {model}")
            print(f"   Latency: {latency:.2f}s")
            print(f"   Preview:\n{preview}\n{'-'*60}")

        # Ask the session to summarize what it just did
        print("\n🧭 Asking the session to summarize its work so far...")
        summary_prompt = "Summarize the tasks you just completed in this session."
        summary = await session.execute(summary_prompt)
        print(f"\n📝 Session summary:\n{summary}\n{'='*60}")


if __name__ == "__main__":
    asyncio.run(main())
