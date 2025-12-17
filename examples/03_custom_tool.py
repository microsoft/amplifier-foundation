#!/usr/bin/env python3
"""
Example 3: Building a Custom Tool - Extending Your Agent
=========================================================

VALUE PROPOSITION:
Need a capability that doesn't exist? Build it in 20 lines.
Amplifier's protocol-based design means no inheritance, no framework lock-in.
Just implement the contract and plug it in.

WHAT YOU'LL LEARN:
- How to create a custom tool from scratch
- The Tool contract (what methods you need)
- How to register and use your tool
- How tools integrate seamlessly with any orchestrator/provider

REAL-WORLD USE CASE:
Build a "database query" tool, "API client" tool, or "email sender" tool
for your specific domain.

TIME TO VALUE: 10 minutes
"""

import asyncio
import os
from pathlib import Path
from typing import Any

from amplifier_core import ToolResult
from amplifier_foundation import Bundle
from amplifier_foundation import load_bundle

# =============================================================================
# STEP 1: Define Your Custom Tool
# =============================================================================


class WeatherTool:
    """A custom tool that provides weather information.

    This demonstrates the minimal Tool contract:
    - name property
    - description property
    - input_schema property (JSON schema for parameters)
    - execute() method

    No inheritance required! Just implement the protocol.
    """

    @property
    def name(self) -> str:
        """Unique identifier for this tool."""
        return "weather"

    @property
    def description(self) -> str:
        """Description the LLM will see to decide when to use this tool."""
        return """Get current weather for a location.

Input: {"location": "city name or zip code"}
Returns: Weather information including temperature, conditions, and forecast."""

    @property
    def input_schema(self) -> dict:
        """JSON schema defining the tool's parameters."""
        return {
            "type": "object",
            "properties": {"location": {"type": "string", "description": "City name or zip code"}},
            "required": ["location"],
        }

    async def execute(self, input: dict[str, Any]) -> ToolResult:
        """Execute the tool with the given input.

        Args:
            input: Dict with 'location' key

        Returns:
            ToolResult with weather information
        """
        location = input.get("location", "")

        if not location:
            return ToolResult(success=False, error={"message": "No location provided"})

        # In a real tool, you'd call a weather API here
        # For demo, we'll return mock data
        mock_weather = {
            "location": location,
            "temperature": "72°F (22°C)",
            "conditions": "Partly cloudy",
            "humidity": "65%",
            "wind": "10 mph NW",
            "forecast": "Clear skies expected through the evening",
        }

        result_text = f"""Weather for {location}:
Temperature: {mock_weather["temperature"]}
Conditions: {mock_weather["conditions"]}
Humidity: {mock_weather["humidity"]}
Wind: {mock_weather["wind"]}
Forecast: {mock_weather["forecast"]}"""

        return ToolResult(success=True, output=result_text)


class DatabaseTool:
    """Example of a more complex custom tool - database queries.

    This shows how you might build domain-specific tools for your application.
    """

    @property
    def name(self) -> str:
        return "database"

    @property
    def description(self) -> str:
        return """Query the application database.

Input: {"query": "SQL query", "params": [optional list of params]}
Returns: Query results as JSON."""

    @property
    def input_schema(self) -> dict:
        """JSON schema defining the tool's parameters."""
        return {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "SQL query to execute"},
                "params": {"type": "array", "description": "Optional query parameters", "items": {"type": "string"}},
            },
            "required": ["query"],
        }

    async def execute(self, input: dict[str, Any]) -> ToolResult:
        """Execute a database query."""
        query = input.get("query", "")

        if not query:
            return ToolResult(success=False, error={"message": "No query provided"})

        # Mock results for demo
        # In real tool, you'd use asyncpg, SQLAlchemy, etc.
        if "users" in query.lower():
            result = [
                {"id": 1, "name": "Alice", "email": "alice@example.com"},
                {"id": 2, "name": "Bob", "email": "bob@example.com"},
            ]
            return ToolResult(success=True, output=result)

        return ToolResult(success=True, output=f"Query executed: {query}")


# =============================================================================
# STEP 2: Mount Function (Required for Module Loading)
# =============================================================================


async def mount_custom_tools(coordinator, config: dict):
    """Mount function that registers your custom tools.

    This is the bridge between your tool and Amplifier's module system.
    The coordinator provides the registration API.
    """
    # Create instances of your tools
    weather = WeatherTool()
    database = DatabaseTool()

    # Register them with the coordinator
    await coordinator.mount("tools", weather, name=weather.name)
    await coordinator.mount("tools", database, name=database.name)

    print(f"✓ Registered custom tools: {weather.name}, {database.name}")

    # Optional: Return cleanup function
    async def cleanup():
        # Close connections, release resources, etc.
        # In a real app: close DB connections, release file handles, etc.
        print("Cleanup: releasing resources (no-op in this example)")

    return cleanup


# =============================================================================
# STEP 3: Use Your Custom Tool
# =============================================================================


async def demo_custom_tool():
    """Demonstrate using custom tools in an agent."""

    print("\n" + "=" * 60)
    print("Custom Tool Demo: Weather + Database Tools")
    print("=" * 60)

    # Load foundation and provider
    foundation_path = Path(__file__).parent.parent  # examples/ -> amplifier-foundation/
    foundation = await load_bundle(str(foundation_path))
    provider = await load_bundle(str(foundation_path / "providers" / "anthropic-sonnet.yaml"))

    # Add filesystem tool for fun
    tools_config = Bundle(
        name="tools-config",
        version="1.0.0",
        tools=[
            {
                "module": "tool-filesystem",
                "source": "git+https://github.com/microsoft/amplifier-module-tool-filesystem@main",
            },
        ],
    )

    composed = foundation.compose(provider).compose(tools_config)

    print("⏳ Preparing...")
    prepared = await composed.prepare()
    session = await prepared.create_session()

    # Register custom tools AFTER session is created
    await mount_custom_tools(session.coordinator, {})

    # Now use the agent with your custom tools!
    async with session:
        print("\n[Test 1: Weather Tool]")
        print("📝 Asking about weather...")
        response1 = await session.execute("What's the weather like in San Francisco?")
        print(f"✓ Response: {response1[:300]}...")

        print("\n[Test 2: Database Tool]")
        print("📝 Asking about database...")
        response2 = await session.execute("Query the users table and show me the results.")
        print(f"✓ Response: {response2[:300]}...")

        print("\n[Test 3: Multi-tool Usage]")
        print("📝 Using multiple tools together...")
        response3 = await session.execute(
            "Check the weather in New York, then query the users table "
            "and save both results to a file called 'results.txt'."
        )
        print(f"✓ Response: {response3[:300]}...")


async def main():
    """Run the custom tool demo."""

    print("🔧 Building Custom Tools with Amplifier")
    print("=" * 60)
    print("\nWHAT YOU'LL BUILD:")
    print("- WeatherTool: Get weather for any location")
    print("- DatabaseTool: Query application database")
    print("- Pattern: Domain-specific tools for your use case")
    print("\nKEY INSIGHT:")
    print("Tools are just classes with name, description, and execute().")
    print("No inheritance, no framework magic - just implement the protocol!")

    await demo_custom_tool()

    print("\n" + "=" * 60)
    print("📚 WHAT YOU LEARNED:")
    print("=" * 60)
    print("1. Tool Contract: name, description, input_schema, execute()")
    print("2. input_schema: JSON schema defining parameters (helps LLM use the tool)")
    print("3. Registration: Use coordinator.mount() to register tools")
    print("4. Integration: Custom tools work with any orchestrator/provider")
    print("5. No framework lock-in: Just implement the protocol")
    print("\n✅ You can now extend Amplifier with domain-specific capabilities!")
    print("\n💡 Next: Try 08_cli_application.py for application patterns")


if __name__ == "__main__":
    if not os.getenv("ANTHROPIC_API_KEY"):
        print("❌ ERROR: Set ANTHROPIC_API_KEY environment variable")
        print("\nExample:")
        print("  export ANTHROPIC_API_KEY='your-key-here'")
        print("  python 03_custom_tool.py")
        exit(1)

    asyncio.run(main())
