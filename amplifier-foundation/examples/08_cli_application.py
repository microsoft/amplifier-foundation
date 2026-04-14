#!/usr/bin/env python3
"""
Example 8: CLI Application - Building a Real Tool
==================================================

VALUE PROPOSITION:
Go beyond scripts - see how to build a proper CLI application with Amplifier.
Learn application architecture patterns, error handling, and lifecycle management.

WHAT YOU'LL LEARN:
- Application architecture patterns with Amplifier
- Proper session lifecycle management
- Error handling and recovery
- Logging and observability
- Configuration management
- Building reusable application classes

REAL-WORLD USE CASE:
This is the blueprint for building CLI tools:
- Internal developer tools
- Data analysis assistants
- Code review helpers
- Automation scripts

TIME TO VALUE: 15 minutes
"""

import asyncio
import logging
import os
import sys
from dataclasses import dataclass
from pathlib import Path

from amplifier_foundation import Bundle
from amplifier_foundation import load_bundle

# =============================================================================
# SECTION 1: Configuration Management
# =============================================================================


@dataclass
class AppConfig:
    """Application configuration.

    In production, you'd load this from:
    - Environment variables
    - Config files (.amplifier/settings.yaml)
    - Command-line arguments
    - Secrets management (AWS Secrets Manager, etc.)
    """

    # LLM Provider
    provider_bundle: str = "anthropic-sonnet.yaml"
    api_key: str | None = None

    # Application Settings
    log_level: str = "INFO"
    storage_path: Path = Path.home() / ".amplifier" / "app_sessions"

    @classmethod
    def from_env(cls) -> "AppConfig":
        """Load configuration from environment."""
        return cls(
            provider_bundle=os.getenv("PROVIDER", "anthropic-sonnet.yaml"),
            api_key=os.getenv("ANTHROPIC_API_KEY"),
            log_level=os.getenv("LOG_LEVEL", "INFO"),
        )

    def validate(self) -> None:
        """Validate configuration."""
        if not self.api_key:
            raise ValueError("ANTHROPIC_API_KEY not set")

        if not self.storage_path.exists():
            self.storage_path.mkdir(parents=True, exist_ok=True)


# =============================================================================
# SECTION 2: Application Class (Your Product)
# =============================================================================


class AmplifierApp:
    """Amplifier application class showing best practices.

    This class encapsulates:
    - Bundle management
    - Session lifecycle
    - Error handling
    - Logging and observability
    - Graceful shutdown

    Adapt this pattern for your CLI tools or applications.
    """

    def __init__(self, config: AppConfig):
        self.config = config
        self.session = None
        self.logger = self._setup_logging()

    def _setup_logging(self) -> logging.Logger:
        """Configure logging for the application."""
        logging.basicConfig(
            level=getattr(logging, self.config.log_level),
            format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
            handlers=[
                logging.StreamHandler(sys.stdout),
            ],
        )
        return logging.getLogger("amplifier_app")

    async def initialize(self) -> None:
        """Initialize the application and create session."""
        self.logger.info("Initializing Amplifier application...")

        try:
            # Load foundation
            foundation_path = Path(__file__).parent.parent  # examples/ -> amplifier-foundation/
            foundation = await load_bundle(str(foundation_path))
            self.logger.info(f"Loaded foundation: {foundation.name} v{foundation.version}")

            # Load provider
            provider_path = foundation_path / "providers" / self.config.provider_bundle
            provider = await load_bundle(str(provider_path))
            self.logger.info(f"Loaded provider: {provider.name}")

            # Add tools
            tools_config = Bundle(
                name="app-tools",
                version="1.0.0",
                tools=[
                    {
                        "module": "tool-filesystem",
                        "source": "git+https://github.com/microsoft/amplifier-module-tool-filesystem@main",
                    },
                    {
                        "module": "tool-bash",
                        "source": "git+https://github.com/microsoft/amplifier-module-tool-bash@main",
                    },
                ],
            )

            # Compose all bundles
            composed = foundation.compose(provider).compose(tools_config)

            # Prepare (download modules)
            self.logger.info("Preparing bundles (downloading modules if needed)...")
            prepared = await composed.prepare()

            # Create session
            self.logger.info("Creating session...")
            self.session = await prepared.create_session()
            self.logger.info("✓ Application initialized successfully")

        except Exception as e:
            self.logger.error(f"Initialization failed: {e}", exc_info=True)
            raise

    async def execute(self, prompt: str) -> str:
        """Execute a prompt through the agent.

        Args:
            prompt: User input

        Returns:
            Agent response

        Raises:
            RuntimeError: If session not initialized
        """
        if not self.session:
            raise RuntimeError("Session not initialized. Call initialize() first.")

        try:
            self.logger.info(f"Executing prompt: {prompt[:100]}...")
            response = await self.session.execute(prompt)
            self.logger.info("Execution completed successfully")
            return response

        except Exception as e:
            self.logger.error(f"Execution failed: {e}", exc_info=True)
            # In production, you might want to:
            # - Retry with exponential backoff
            # - Fallback to a simpler model
            # - Return a user-friendly error message
            raise

    async def shutdown(self) -> None:
        """Gracefully shutdown the application."""
        self.logger.info("Shutting down application...")

        if self.session:
            try:
                await self.session.cleanup()
                self.logger.info("Session cleaned up")
            except Exception as e:
                self.logger.error(f"Error during cleanup: {e}", exc_info=True)

        self.logger.info("✓ Application shutdown complete")

    async def __aenter__(self):
        """Context manager support."""
        await self.initialize()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """Context manager cleanup."""
        await self.shutdown()


# =============================================================================
# SECTION 3: CLI Interface
# =============================================================================


async def run_interactive_cli(app: AmplifierApp):
    """Interactive CLI mode."""
    print("\n" + "=" * 60)
    print("🤖 Amplifier CLI App - Interactive Mode")
    print("=" * 60)
    print("Type your prompts, or 'quit' to exit.\n")

    while True:
        try:
            # Get user input
            prompt = input("\n💬 You: ")

            if prompt.lower() in ("quit", "exit", "q"):
                print("\n👋 Goodbye!")
                break

            if not prompt.strip():
                continue

            # Execute
            print("\n🤔 Agent: ", end="", flush=True)
            response = await app.execute(prompt)
            print(response)

        except KeyboardInterrupt:
            print("\n\n👋 Interrupted. Goodbye!")
            break
        except Exception as e:
            print(f"\n❌ Error: {e}")
            print("The session is still active. You can continue.")


async def run_single_prompt(app: AmplifierApp, prompt: str):
    """Single prompt mode."""
    print("\n" + "=" * 60)
    print("Executing single prompt...")
    print("=" * 60)

    response = await app.execute(prompt)
    print("\nResponse:")
    print("-" * 60)
    print(response)


# =============================================================================
# SECTION 4: Main Entry Point
# =============================================================================


async def main():
    """Main application entry point."""

    print("🚀 Amplifier CLI Application Example")
    print("=" * 60)

    # Load and validate configuration
    try:
        config = AppConfig.from_env()
        config.validate()
        print("✓ Configuration loaded")
        print(f"  Provider: {config.provider_bundle}")
        print(f"  Log level: {config.log_level}")
    except Exception as e:
        print(f"❌ Configuration error: {e}")
        return 1

    # Initialize application
    try:
        async with AmplifierApp(config) as app:
            # Choose mode
            if len(sys.argv) > 1:
                # Single prompt mode
                prompt = " ".join(sys.argv[1:])
                await run_single_prompt(app, prompt)
            else:
                # Interactive mode
                await run_interactive_cli(app)

    except Exception as e:
        print(f"❌ Application error: {e}")
        return 1

    return 0


if __name__ == "__main__":
    print("\n💡 Usage:")
    print("  Interactive: python 08_cli_application.py")
    print("  Single prompt: python 08_cli_application.py 'your prompt here'")
    print()

    exit_code = asyncio.run(main())
    sys.exit(exit_code)
