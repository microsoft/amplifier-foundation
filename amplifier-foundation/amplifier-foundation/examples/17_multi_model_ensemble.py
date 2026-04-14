#!/usr/bin/env python3
"""
Example 17: Multi-Model Ensemble
=================================

AUDIENCE: Advanced developers, researchers, teams optimizing quality/cost
VALUE: Shows advanced pattern of combining multiple models for better results
PATTERN: Ensemble methods, model routing, consensus building

What this demonstrates:
  - Using multiple LLM providers/models in a single workflow
  - Model routing based on task type
  - Consensus and voting across models
  - Quality vs. cost optimization
  - Fallback strategies

When you'd use this:
  - Critical decisions requiring multiple perspectives
  - Quality optimization (use multiple models, pick best)
  - Cost optimization (try cheap model first, fallback to expensive)
  - Research and model comparison
  - Avoiding vendor lock-in
"""

import asyncio
from pathlib import Path
from typing import Any

from amplifier_core import AmplifierSession
from amplifier_foundation import load_bundle

# ============================================================================
# Ensemble Strategies
# ============================================================================


async def ensemble_consensus(
    prompts: list[tuple[str, str]],  # (provider_path, prompt)
    foundation_path: Path,
) -> list[str]:
    """
    Run the same prompt across multiple models and collect responses.

    Use case: Get multiple perspectives on the same problem.
    """
    results = []

    for provider_path, prompt in prompts:
        print(f"\n{'=' * 80}")
        print(f"🤖 Testing: {provider_path}")
        print(f"{'=' * 80}")

        # Load foundation with specific provider
        foundation = await load_bundle(str(foundation_path))
        provider = await load_bundle(provider_path)

        # Compose bundles (provider overrides foundation defaults)
        composed = foundation.compose(provider)
        mount_plan = composed.to_mount_plan()

        # Create and run session
        session = AmplifierSession(config=mount_plan)
        await session.initialize()

        try:
            result = await session.execute(prompt)
            results.append((provider_path, result))

            # Show preview
            preview = result[:200] + "..." if len(result) > 200 else result
            print(f"\n📝 Response preview:\n{preview}")

        finally:
            await session.cleanup()

    return results


async def ensemble_cascade(
    provider_configs: list[tuple[str, str]],  # (provider_path, label)
    prompt: str,
    foundation_path: Path,
    quality_threshold: str = "acceptable",
) -> tuple[str, str]:
    """
    Try models in order (cheap to expensive) until quality threshold met.

    Use case: Optimize cost by using cheaper models when they're good enough.
    """
    print(f"\n{'=' * 80}")
    print("🔄 Cascade Strategy: Trying models from cheap to expensive")
    print(f"{'=' * 80}")
    print(f"\nPrompt: {prompt}")
    print(f"Quality threshold: {quality_threshold}")

    foundation = await load_bundle(str(foundation_path))

    for i, (provider_path, label) in enumerate(provider_configs, 1):
        print(f"\n{'─' * 80}")
        print(f"Attempt {i}/{len(provider_configs)}: {label}")
        print(f"{'─' * 80}")

        # Load and compose with provider
        provider = await load_bundle(provider_path)
        composed = foundation.compose(provider)
        mount_plan = composed.to_mount_plan()

        # Run session
        session = AmplifierSession(config=mount_plan)
        await session.initialize()

        try:
            result = await session.execute(prompt)

            # Simple quality check (in practice, use more sophisticated validation)
            word_count = len(result.split())
            has_code = "```" in result or "def " in result or "class " in result

            quality_met = word_count > 50  # Simple heuristic
            if quality_threshold == "high" and has_code:
                quality_met = word_count > 100

            status = "✅ Quality met" if quality_met else "⚠️  Quality below threshold"
            print(f"\n{status} (words: {word_count}, has code: {has_code})")

            if quality_met:
                print(f"\n🎯 Accepted result from {label}")
                return (label, result)
            print("\n⏭️  Trying next model...")

        finally:
            await session.cleanup()

    raise RuntimeError("No model met quality threshold")


async def ensemble_routing(
    task: dict[str, Any],
    foundation_path: Path,
) -> str:
    """
    Route tasks to appropriate models based on task characteristics.

    Use case: Use the right tool for the job - fast models for simple tasks,
    powerful models for complex reasoning.
    """
    print(f"\n{'=' * 80}")
    print("🎯 Routing Strategy: Selecting model based on task type")
    print(f"{'=' * 80}")

    task_type = task.get("type", "general")
    prompt = task.get("prompt", "")

    # Define routing rules
    routing_map = {
        "simple": ("providers/anthropic-haiku.yaml", "Fast/Cheap (Haiku)"),
        "balanced": ("providers/anthropic-sonnet.yaml", "Balanced (Sonnet)"),
        "complex": ("providers/anthropic-opus.yaml", "Premium (Opus)"),
    }

    provider_path, label = routing_map.get(task_type, routing_map["balanced"])

    print(f"\nTask type: {task_type}")
    print(f"Selected model: {label}")
    print(f"Prompt: {prompt[:100]}...")

    # Load and run
    foundation = await load_bundle(str(foundation_path))
    provider = await load_bundle(provider_path)
    composed = foundation.compose(provider)
    mount_plan = composed.to_mount_plan()

    session = AmplifierSession(config=mount_plan)
    await session.initialize()

    try:
        result = await session.execute(prompt)
        return result
    finally:
        await session.cleanup()


# ============================================================================
# Demo Scenarios
# ============================================================================


async def scenario_consensus_voting():
    """
    Scenario: Get consensus across multiple models.

    Run the same prompt on multiple models and compare results.
    """
    print("\n" + "=" * 80)
    print("SCENARIO 1: Consensus Voting")
    print("=" * 80)
    print("\nRun the same prompt across multiple models to get diverse perspectives.")
    print("Useful for critical decisions or quality optimization.")
    print("-" * 80)

    foundation_path = Path(__file__).parent.parent

    # Same prompt to multiple providers
    prompt = "List 3 creative uses for AI agents in software development. Be concise."

    prompts = [
        ("providers/anthropic-haiku.yaml", prompt),
        ("providers/anthropic-sonnet.yaml", prompt),
    ]

    print(f"\n📝 Prompt: {prompt}")

    results = await ensemble_consensus(prompts, foundation_path)

    # Show comparison
    print("\n" + "=" * 80)
    print("📊 CONSENSUS COMPARISON")
    print("=" * 80)

    for provider_path, result in results:
        model_name = provider_path.split("/")[-1].replace(".yaml", "")
        print(f"\n{model_name}:")
        print(f"{'─' * 80}")
        preview = result[:300] + "..." if len(result) > 300 else result
        print(preview)

    print("\n" + "=" * 80)
    print("💡 Analysis")
    print("=" * 80)
    print("""
With consensus voting, you can:
- Compare model outputs for quality assessment
- Pick the best response (manual or automated)
- Combine insights from multiple models
- Reduce bias from a single model
    """)


async def scenario_cost_optimization():
    """
    Scenario: Cascade from cheap to expensive models.

    Try cheaper models first, escalate to expensive only if needed.
    """
    print("\n" + "=" * 80)
    print("SCENARIO 2: Cost Optimization with Cascade")
    print("=" * 80)
    print("\nTry models in order of cost, stopping when quality threshold met.")
    print("Maximizes cost efficiency while maintaining quality.")
    print("-" * 80)

    foundation_path = Path(__file__).parent.parent

    # Define cascade order (cheap to expensive)
    providers = [
        ("providers/anthropic-haiku.yaml", "Haiku (cheapest)"),
        ("providers/anthropic-sonnet.yaml", "Sonnet (balanced)"),
        ("providers/anthropic-opus.yaml", "Opus (premium)"),
    ]

    prompt = "Write a Python function to calculate fibonacci numbers with memoization."

    try:
        selected_model, result = await ensemble_cascade(
            providers, prompt, foundation_path, quality_threshold="acceptable"
        )

        print("\n" + "=" * 80)
        print("✅ FINAL RESULT")
        print("=" * 80)
        print(f"\nSelected model: {selected_model}")
        print("\nResponse:")
        print("─" * 80)
        print(result)

    except RuntimeError as e:
        print(f"\n❌ Error: {e}")

    print("\n" + "=" * 80)
    print("💡 Cost Savings")
    print("=" * 80)
    print("""
Cascade strategy benefits:
- Pay for expensive models only when necessary
- Fast responses from cheaper models when they work
- Quality guarantee through fallback chain
- Typical cost savings: 40-60% compared to always using premium models
    """)


async def scenario_task_routing():
    """
    Scenario: Route different tasks to appropriate models.

    Use the right model for each task type.
    """
    print("\n" + "=" * 80)
    print("SCENARIO 3: Intelligent Task Routing")
    print("=" * 80)
    print("\nRoute tasks to the most appropriate model based on complexity.")
    print("Simple tasks → fast models, complex tasks → powerful models.")
    print("-" * 80)

    foundation_path = Path(__file__).parent.parent

    # Define various tasks
    tasks = [
        {
            "type": "simple",
            "prompt": "What is 2 + 2?",
            "description": "Simple arithmetic",
        },
        {
            "type": "balanced",
            "prompt": "Explain the Observer pattern in software design.",
            "description": "Moderate explanation",
        },
        {
            "type": "complex",
            "prompt": "Design a distributed consensus algorithm for a blockchain network.",
            "description": "Complex system design",
        },
    ]

    for i, task in enumerate(tasks, 1):
        print(f"\n{'=' * 80}")
        print(f"Task {i}: {task['description']}")
        print(f"{'=' * 80}")

        result = await ensemble_routing(task, foundation_path)

        preview = result[:200] + "..." if len(result) > 200 else result
        print(f"\n📝 Response:\n{preview}")

    print("\n" + "=" * 80)
    print("💡 Routing Benefits")
    print("=" * 80)
    print("""
Task routing advantages:
- Match model capability to task complexity
- Optimize for cost AND latency
- Simple tasks get fast responses
- Complex tasks get quality responses
- Easy to adjust routing rules based on experience
    """)


# ============================================================================
# Interactive Menu
# ============================================================================


async def main():
    """Run interactive demo menu."""
    print("\n" + "=" * 80)
    print("🎭 Multi-Model Ensemble Patterns")
    print("=" * 80)
    print("\nVALUE: Advanced patterns for combining multiple models")
    print("AUDIENCE: Teams optimizing quality, cost, and reliability")
    print("\nWhat this demonstrates:")
    print("  - Consensus voting across models")
    print("  - Cost optimization with cascading")
    print("  - Intelligent task routing")
    print("  - Model comparison and selection")

    scenarios = [
        ("Consensus Voting (multiple perspectives)", scenario_consensus_voting),
        ("Cost Optimization (cascade strategy)", scenario_cost_optimization),
        ("Task Routing (right model for the job)", scenario_task_routing),
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
1. **Consensus Voting**: Run same prompt on multiple models, compare results
2. **Cost Cascade**: Try cheap models first, escalate only if needed
3. **Task Routing**: Match model capability to task complexity
4. **Flexibility**: Easy to swap models, test strategies, optimize

**When to use ensemble patterns:**
- Critical decisions requiring multiple perspectives
- Cost optimization while maintaining quality
- Avoiding vendor lock-in
- Research and model comparison
- Building resilient systems with fallbacks

**Implementation patterns:**
- Bundle composition makes it easy to swap providers
- Same code works across all models (provider abstraction)
- Add routing logic in your application layer
- Use hooks to collect metrics for routing decisions
""")


if __name__ == "__main__":
    asyncio.run(main())
