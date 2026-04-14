#!/usr/bin/env python3
"""
Example 11: Provider Comparison Demo
====================================

VALUE PROPOSITION:
See Amplifier's key strength - trivially easy provider swapping. This example shows
how to compare different providers and demonstrates that swapping is just one line of code.

WHAT YOU'LL LEARN:
- How to swap providers by changing one bundle path
- Pattern for comparing provider performance
- Cost/speed tradeoff considerations
- How Amplifier's abstraction makes providers interchangeable

AUDIENCE:
Developers and PMs making cost/performance decisions
"""

import asyncio
import os
import time
from dataclasses import dataclass
from pathlib import Path

from amplifier_foundation import load_bundle

# =============================================================================
# SECTION 1: Provider Execution Pattern
# =============================================================================


@dataclass
class ProviderResult:
    """Results from a provider execution."""

    provider_name: str
    model_name: str
    response: str
    duration: float
    estimated_cost: str


async def test_provider(provider_bundle_path: Path, prompt: str) -> ProviderResult:
    """Test a single provider and measure performance.

    Args:
        provider_bundle_path: Path to provider bundle (e.g., providers/anthropic-sonnet.yaml)
        prompt: Prompt to execute

    Returns:
        ProviderResult with timing and response
    """
    # Load foundation
    foundation_path = Path(__file__).parent.parent
    foundation = await load_bundle(str(foundation_path))

    # Load provider - THIS IS THE LINE THAT CHANGES TO SWAP PROVIDERS
    provider = await load_bundle(str(provider_bundle_path))

    # Get provider info
    provider_name = provider.name
    model_name = "unknown"
    if provider.providers and len(provider.providers) > 0:
        model_name = provider.providers[0].get("config", {}).get("default_model", "unknown")

    print(f"\n  ⏳ Testing {provider_name} ({model_name})...")

    # Compose foundation + provider
    composed = foundation.compose(provider)

    # Prepare (downloads modules if needed)
    print("     Preparing modules...")
    prepared = await composed.prepare()

    # Create session
    session = await prepared.create_session()

    # Execute with timing
    print("     Executing prompt...")
    start_time = time.time()

    async with session:
        response = await session.execute(prompt)

    duration = time.time() - start_time

    # Estimate cost (rough approximation)
    input_tokens = len(prompt) // 4  # ~4 chars per token
    output_tokens = len(response) // 4

    if "haiku" in model_name.lower():
        cost = f"~${(input_tokens * 0.8 + output_tokens * 4.0) / 1_000_000:.4f} (cheap)"
    elif "sonnet" in model_name.lower():
        cost = f"~${(input_tokens * 3.0 + output_tokens * 15.0) / 1_000_000:.4f} (balanced)"
    elif "opus" in model_name.lower():
        cost = f"~${(input_tokens * 15.0 + output_tokens * 75.0) / 1_000_000:.4f} (premium)"
    else:
        cost = "unknown"

    print(f"     ✓ Completed in {duration:.2f}s")

    return ProviderResult(
        provider_name=provider_name, model_name=model_name, response=response, duration=duration, estimated_cost=cost
    )


# =============================================================================
# SECTION 2: Single Provider Demo (Fast)
# =============================================================================


async def single_provider_demo():
    """Demonstrate single provider usage - fast demo."""

    print("\n" + "=" * 80)
    print("DEMO: Single Provider Test")
    print("=" * 80)
    print("\nThis shows the basic pattern. To compare providers, run this")
    print("same code with different provider bundle paths.")

    # Test prompt
    prompt = "Explain recursion in programming with a simple Python example."

    print(f"\n📝 Prompt: {prompt}")

    # Test with Anthropic Sonnet (balanced)
    provider_path = Path(__file__).parent.parent / "providers" / "anthropic-sonnet.yaml"

    result = await test_provider(provider_path, prompt)

    # Display result
    print("\n" + "=" * 80)
    print("📊 Result")
    print("=" * 80)
    print(f"Provider: {result.provider_name}")
    print(f"Model: {result.model_name}")
    print(f"Time: {result.duration:.2f}s")
    print(f"Cost: {result.estimated_cost}")
    print("\nResponse preview:")
    print("-" * 80)
    print(result.response[:400] + "..." if len(result.response) > 400 else result.response)


# =============================================================================
# SECTION 3: How to Compare Providers
# =============================================================================


def show_comparison_pattern():
    """Show the code pattern for comparing providers."""

    print("\n" + "=" * 80)
    print("💡 HOW TO COMPARE PROVIDERS")
    print("=" * 80)

    print("""
To compare providers, just change ONE line - the provider bundle path:

```python
# Test with Claude Sonnet (balanced)
provider = await load_bundle("providers/anthropic-sonnet.yaml")

# Change to Claude Haiku (fast/cheap)
provider = await load_bundle("providers/anthropic-haiku.yaml")

# Change to Claude Opus (premium quality)
provider = await load_bundle("providers/anthropic-opus.yaml")

# Change to OpenAI GPT-4 (alternative)
provider = await load_bundle("providers/openai-gpt.yaml")
```

That's it! The rest of your code stays exactly the same. This is the power
of Amplifier's provider abstraction - providers are truly interchangeable.

To run a comparison:
1. Run this script with each provider
2. Compare timing, cost, and response quality
3. Choose the right model for your use case:
   - Haiku: Fast, cheap, good for simple tasks
   - Sonnet: Balanced quality and cost (default choice)
   - Opus: Premium quality for complex reasoning
   - GPT-4: Alternative perspective, different strengths
""")


# =============================================================================
# SECTION 4: Cost/Speed Tradeoff Guide
# =============================================================================


def show_tradeoff_guide():
    """Show when to use which provider."""

    print("\n" + "=" * 80)
    print("🎯 WHEN TO USE WHICH MODEL")
    print("=" * 80)

    print("""
MODEL SELECTION GUIDE:

🟢 Claude Haiku (Fast & Cheap)
  Speed: ~0.5-1s
  Cost: ~$0.0002 per request
  Use for:
    - Simple text transformations
    - Data extraction from structured text
    - Quick translations
    - High-volume batch processing

🟡 Claude Sonnet (Balanced) ⭐ RECOMMENDED DEFAULT
  Speed: ~1-2s
  Cost: ~$0.003 per request
  Use for:
    - Code generation and review
    - Content writing
    - Most general-purpose tasks
    - When you need good quality without premium cost

🔴 Claude Opus (Premium Quality)
  Speed: ~2-3s
  Cost: ~$0.015 per request
  Use for:
    - Complex reasoning tasks
    - Critical code reviews
    - Research and analysis
    - When quality matters most

🔵 GPT-4 (Alternative)
  Speed: ~1.5-2s
  Cost: ~$0.006 per request
  Use for:
    - Getting a different perspective
    - Tasks where GPT-4 excels (varies by domain)
    - When you want model diversity

COST OPTIMIZATION STRATEGY:
1. Start with Sonnet (balanced)
2. Use Haiku for simple, high-volume tasks
3. Reserve Opus for critical tasks
4. A/B test to find the right model for each use case
""")


# =============================================================================
# SECTION 5: Main Entry Point
# =============================================================================


async def main():
    """Main entry point."""

    print("🚀 Provider Comparison Demo")
    print("=" * 80)
    print("\nVALUE: Understand provider swapping and make informed model choices")
    print("AUDIENCE: Developers and PMs making cost/performance decisions")

    # Check prerequisites
    if not os.getenv("ANTHROPIC_API_KEY"):
        print("\n❌ ERROR: Set ANTHROPIC_API_KEY environment variable")
        print("\nExample:")
        print("  export ANTHROPIC_API_KEY='your-key-here'")
        print("  python 11_provider_comparison.py")
        return

    # Run single provider demo
    await single_provider_demo()

    # Show comparison pattern
    show_comparison_pattern()

    # Show tradeoff guide
    show_tradeoff_guide()

    # Summary
    print("\n" + "=" * 80)
    print("📚 WHAT YOU LEARNED:")
    print("=" * 80)
    print("  ✓ Provider swapping is ONE line of code in Amplifier")
    print("  ✓ Providers are truly interchangeable - same code works with any")
    print("  ✓ Cost/speed/quality tradeoffs guide model selection")
    print("  ✓ Pattern for running your own comparisons")

    print("\n" + "=" * 80)
    print("🎓 KEY TAKEAWAY")
    print("=" * 80)
    print("""
Amplifier's provider abstraction means you can:
  1. Start with one model
  2. Swap to another anytime (one line change)
  3. Test different models on real tasks
  4. Optimize costs without rewriting code

This flexibility is unique to Amplifier's architecture!
""")


if __name__ == "__main__":
    asyncio.run(main())
