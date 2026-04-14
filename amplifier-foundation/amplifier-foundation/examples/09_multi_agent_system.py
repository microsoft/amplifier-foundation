#!/usr/bin/env python3
"""
Example 5: Multi-Agent System - Coordinating Specialized Agents
================================================================

VALUE PROPOSITION:
Build sophisticated systems by composing specialized agents.
Each agent has different tools, models, and expertise.
Amplifier handles the orchestration, you focus on the architecture.

WHAT YOU'LL LEARN:
- How to define specialized agents with different capabilities
- Sequential workflows (agent1 → agent2 → agent3)
- Parallel execution patterns
- Real-world multi-agent architectures

REAL-WORLD USE CASES:
- Software development: architect + implementer + reviewer + tester
- Data analysis: collector + analyzer + visualizer
- Content creation: researcher + writer + editor
- Customer support: classifier + resolver + escalator

This is the advanced pattern that makes Amplifier powerful for complex systems.

TIME TO VALUE: 30 minutes
"""

import asyncio
import os
from pathlib import Path
from typing import Any

from amplifier_foundation import Bundle
from amplifier_foundation import load_bundle

# =============================================================================
# SECTION 1: Define Specialized Agents
# =============================================================================


def create_architect_agent(provider: Bundle) -> Bundle:
    """The Architect: Designs system architecture and specifications.

    Expertise: System design, architecture patterns, API design
    Tools: Read files, web search (for documentation)
    """
    return Bundle(
        name="architect",
        version="1.0.0",
        tools=[
            {
                "module": "tool-filesystem",
                "source": "git+https://github.com/microsoft/amplifier-module-tool-filesystem@main",
            },
        ],
        instruction="""You are a Software Architect expert.

Your role:
- Design system architectures and specifications
- Break down complex problems into modules
- Define clear interfaces and contracts
- Consider scalability, maintainability, and best practices

When given a task:
1. Analyze the requirements
2. Design the architecture (components, interfaces, data flow)
3. Create a specification document
4. Return clear instructions for implementation

Focus on design, not implementation. Be thorough but concise.""",
    ).compose(provider)


def create_implementer_agent(provider: Bundle) -> Bundle:
    """The Implementer: Writes code based on specifications.

    Expertise: Code implementation, testing, debugging
    Tools: Full filesystem access, bash execution
    """
    return Bundle(
        name="implementer",
        version="1.0.0",
        tools=[
            {
                "module": "tool-filesystem",
                "source": "git+https://github.com/microsoft/amplifier-module-tool-filesystem@main",
            },
            {"module": "tool-bash", "source": "git+https://github.com/microsoft/amplifier-module-tool-bash@main"},
        ],
        instruction="""You are a Software Implementation expert.

Your role:
- Implement code based on specifications
- Write clean, tested, documented code
- Follow the architecture and contracts provided
- Run tests to verify correctness

When given a specification:
1. Understand the requirements
2. Implement the code
3. Write tests
4. Verify it works
5. Document the implementation

Focus on clean, working code that matches the spec exactly.""",
    ).compose(provider)


def create_reviewer_agent(provider: Bundle) -> Bundle:
    """The Reviewer: Reviews code for quality, security, and best practices.

    Expertise: Code review, security, best practices
    Tools: Read-only filesystem access
    """
    return Bundle(
        name="reviewer",
        version="1.0.0",
        tools=[
            {
                "module": "tool-filesystem",
                "source": "git+https://github.com/microsoft/amplifier-module-tool-filesystem@main",
            },
        ],
        instruction="""You are a Code Review expert.

Your role:
- Review code for correctness, security, and best practices
- Identify bugs, vulnerabilities, and improvement opportunities
- Provide actionable feedback

When reviewing code:
1. Check correctness (does it match the spec?)
2. Check security (any vulnerabilities?)
3. Check quality (readable, maintainable?)
4. Check tests (adequate coverage?)
5. Provide specific, actionable feedback

Be thorough but constructive. Focus on important issues.""",
    ).compose(provider)


# =============================================================================
# SECTION 2: Multi-Agent Orchestrator
# =============================================================================


class MultiAgentSystem:
    """Orchestrates multiple specialized agents to complete complex tasks."""

    def __init__(self, foundation: Bundle, provider: Bundle):
        self.foundation = foundation
        self.provider = provider

    async def execute_workflow(self, task: str, workflow: list[tuple[str, str, Bundle]]) -> dict[str, Any]:
        """Execute a multi-agent workflow.

        Args:
            task: The overall task description
            workflow: List of (agent_name, instruction, agent_bundle) tuples

        Returns:
            Dict with results from each agent
        """
        results = {}
        context = {"task": task}

        for agent_name, instruction, agent_bundle in workflow:
            print(f"\n{'=' * 60}")
            print(f"🤖 Agent: {agent_name.upper()}")
            print(f"{'=' * 60}")
            print(f"Instruction: {instruction[:100]}...")

            # Compose foundation + agent
            composed = self.foundation.compose(agent_bundle)

            print("⏳ Preparing agent...")
            prepared = await composed.prepare()
            session = await prepared.create_session()

            # Add context from previous agents
            context_str = self._format_context(context, results)
            full_instruction = f"{context_str}\n\n{instruction}"

            # Execute
            print("🔄 Executing...")
            async with session:
                response = await session.execute(full_instruction)
                results[agent_name] = response
                print(f"\n✓ {agent_name} completed")
                print(f"Response length: {len(response)} chars")

        return results

    def _format_context(self, context: dict[str, Any], previous_results: dict[str, str]) -> str:
        """Format context from previous agents for the current agent."""
        if not previous_results:
            return f"Task: {context['task']}"

        sections = [f"Task: {context['task']}", "\nPrevious work:"]
        for agent_name, result in previous_results.items():
            sections.append(f"\n{agent_name.upper()} OUTPUT:")
            sections.append(result[:500] + "..." if len(result) > 500 else result)

        return "\n".join(sections)


# =============================================================================
# SECTION 3: Example Workflow
# =============================================================================


async def workflow_build_feature():
    """Workflow: Design → Implement → Review a new feature."""

    print("\n" + "=" * 60)
    print("WORKFLOW: Build a Feature (Design → Implement → Review)")
    print("=" * 60)

    # Load foundation and provider
    foundation_path = Path(__file__).parent.parent  # examples/ -> amplifier-foundation/
    foundation = await load_bundle(str(foundation_path))
    provider = await load_bundle(str(foundation_path / "providers" / "anthropic-sonnet.yaml"))

    # Create multi-agent system
    system = MultiAgentSystem(foundation, provider)

    # Create specialized agents
    architect = create_architect_agent(provider)
    implementer = create_implementer_agent(provider)
    reviewer = create_reviewer_agent(provider)

    # Define workflow
    task = "Create a Python module for rate limiting API requests"

    workflow = [
        (
            "architect",
            """Design a rate limiting module for API requests.

Requirements:
- Support different rate limit strategies (token bucket, sliding window)
- Thread-safe for concurrent requests
- Configurable limits per endpoint
- Include usage tracking

Provide:
1. Module structure
2. Class definitions and interfaces
3. Key algorithms
4. Example usage""",
            architect,
        ),
        (
            "implementer",
            """Implement the rate limiting module based on the architect's design.

Create:
1. rate_limiter.py with all classes
2. A simple test to verify it works
3. Brief usage example

Keep it simple and focused.""",
            implementer,
        ),
        (
            "reviewer",
            """Review the rate limiter implementation.

Check:
1. Does it match the architecture?
2. Are there any bugs or edge cases?
3. Is it thread-safe?
4. Is the code readable and maintainable?

Provide specific feedback for improvement.""",
            reviewer,
        ),
    ]

    # Execute workflow
    results = await system.execute_workflow(task, workflow)

    # Display summary
    print("\n" + "=" * 60)
    print("WORKFLOW COMPLETE - SUMMARY")
    print("=" * 60)
    for agent_name, result in results.items():
        print(f"\n{agent_name.upper()}:")
        print(f"  Output: {len(result)} characters")
        print(f"  Preview: {result[:150]}...")

    return results


# =============================================================================
# Main Demo
# =============================================================================


async def main():
    """Run multi-agent system demonstration."""

    print("🤝 Amplifier Multi-Agent Systems")
    print("=" * 60)
    print("\nKEY CONCEPT: Specialized Agents Working Together")
    print("- Each agent has different expertise, tools, and instructions")
    print("- Agents communicate through structured workflows")
    print("- Complex tasks decomposed into agent responsibilities")
    print("\nREAL-WORLD PATTERN:")
    print("This is how you build sophisticated AI systems in production.")

    # Check API key
    if not os.getenv("ANTHROPIC_API_KEY"):
        print("\n❌ ERROR: Set ANTHROPIC_API_KEY environment variable")
        print("\nExample:")
        print("  export ANTHROPIC_API_KEY='your-key-here'")
        print("  python 05_multi_agent_system.py")
        return

    # Run demonstration
    await workflow_build_feature()

    print("\n" + "=" * 60)
    print("📚 WHAT YOU LEARNED:")
    print("=" * 60)
    print("1. Agent Specialization: Different agents for different expertise")
    print("2. Workflow Orchestration: Sequential handoffs between agents")
    print("3. Context Passing: Agents build on previous work")
    print("4. Composition: Each agent is foundation + provider + tools + instruction")
    print("\n✅ You now understand how to build multi-agent architectures!")
    print("\nNEXT STEPS:")
    print("- Define agents for your domain")
    print("- Create workflows that match your business processes")
    print("- Consider parallel execution for independent tasks")


if __name__ == "__main__":
    asyncio.run(main())
