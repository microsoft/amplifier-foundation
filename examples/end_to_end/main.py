#!/usr/bin/env python3
"""End-to-end example: Load foundation bundle, select provider, execute prompt.

This interactive example demonstrates the complete workflow:
1. Load the foundation bundle (local or from GitHub)
2. Discover and display available provider bundles
3. Let user select a provider
4. Compose foundation + provider into complete mount plan
5. Let user enter a prompt
6. Execute via AmplifierSession and display response

Features demonstrated:
- Bundle loading and composition
- Provider selection
- Session execution
- Sub-session spawning (via session.spawn capability)

Sub-session spawning architecture:
- Foundation provides MECHANISM: PreparedBundle.spawn(child_bundle, instruction)
- App provides POLICY: spawn_capability that adapts task tool's contract
- Task tool calls: spawn_fn(agent_name, instruction, parent_session, agent_configs, sub_session_id)
- App resolves agent_name -> Bundle, then calls foundation's spawn

Requirements:
- API key environment variable set for chosen provider:
  - Anthropic: ANTHROPIC_API_KEY
  - OpenAI: OPENAI_API_KEY
"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path
from typing import Any

from amplifier_foundation import Bundle
from amplifier_foundation import load_bundle
from amplifier_foundation.bundle import PreparedBundle

# =============================================================================
# CLI Output Helpers
# =============================================================================

STEP_WIDTH = 60


def print_header(title: str) -> None:
    """Print a prominent header for the demo."""
    print()
    print("╔" + "═" * (STEP_WIDTH - 2) + "╗")
    print(f"║  {title.center(STEP_WIDTH - 6)}  ║")
    print("╚" + "═" * (STEP_WIDTH - 2) + "╝")
    print()


def print_step(step: int, total: int, title: str) -> None:
    """Print a step header with visual separation."""
    print()
    print("─" * STEP_WIDTH)
    print(f"  [{step}/{total}] {title}")
    print("─" * STEP_WIDTH)


def print_detail(label: str, value: str) -> None:
    """Print an indented detail line."""
    print(f"       {label}: {value}")


def print_success(message: str) -> None:
    """Print a success message with checkmark."""
    print(f"    ✓  {message}")


# =============================================================================
# Provider Discovery and Selection
# =============================================================================


def discover_providers(bundle: Bundle) -> list[dict]:
    """Discover available provider bundles from the foundation's providers directory.

    Works for both local and remote (git-cached) bundles using bundle.base_path.
    """
    if not bundle.base_path:
        return []

    providers_dir = bundle.base_path / "providers"
    if not providers_dir.exists():
        return []

    providers = []
    for provider_file in sorted(providers_dir.glob("*.yaml")):
        import yaml

        with open(provider_file) as f:
            data = yaml.safe_load(f)

        bundle_info = data.get("bundle", {})
        provider_config = data.get("providers", [{}])[0]

        # Determine required env var
        module = provider_config.get("module", "")
        if "anthropic" in module:
            env_var = "ANTHROPIC_API_KEY"
        elif "openai" in module:
            env_var = "OPENAI_API_KEY"
        else:
            env_var = "API_KEY"

        providers.append(
            {
                "name": bundle_info.get("name", provider_file.stem),
                "description": bundle_info.get("description", ""),
                "model": provider_config.get("config", {}).get("default_model", "unknown"),
                "file": provider_file,
                "env_var": env_var,
                "env_set": bool(os.environ.get(env_var)),
            }
        )

    return providers


def display_providers(providers: list[dict]) -> None:
    """Display available providers with status."""
    print()
    for i, p in enumerate(providers, 1):
        status = "✓" if p["env_set"] else "✗"
        env_status = f"{p['env_var']} {'set' if p['env_set'] else 'NOT set'}"
        print(f"    [{i}] {p['name']}")
        print(f"        Model:  {p['model']}")
        print(f"        Status: {status} {env_status}")
        print()


def select_provider(providers: list[dict]) -> dict | None:
    """Let user select a provider."""
    while True:
        try:
            choice = input(f"Select provider [1-{len(providers)}] (or 'q' to quit): ")
            if choice.lower() == "q":
                return None
            idx = int(choice) - 1
            if 0 <= idx < len(providers):
                selected = providers[idx]
                if not selected["env_set"]:
                    print(f"\n⚠ {selected['env_var']} is not set.")
                    print("You can either:")
                    print("  1. Enter your API key now (will be used for this session only)")
                    print("  2. Skip and set the environment variable later")
                    api_key = input(f"\nEnter {selected['env_var']} (or press Enter to skip): ").strip()
                    if api_key:
                        selected["api_key"] = api_key
                        print("      ✓ API key provided")
                    else:
                        print("\n      Skipping - provider may fail without API key.")
                        proceed = input("      Continue anyway? [y/N]: ")
                        if proceed.lower() != "y":
                            continue
                return selected
            print("Invalid selection. Try again.")
        except ValueError:
            print("Please enter a number.")


def get_user_prompt() -> str | None:
    """Get prompt from user."""
    print("\nEnter your prompt (or 'q' to quit):")
    print("-" * 40)
    prompt = input("> ")
    if prompt.lower() == "q":
        return None
    return prompt


# =============================================================================
# App-Layer Spawn Policy (adapts task tool contract to foundation mechanism)
# =============================================================================


def register_spawn_capability(
    session: Any,
    prepared: PreparedBundle,
) -> None:
    """Register spawn capability with task tool's expected contract.

    This is APP-LAYER POLICY that wraps foundation's mechanism.
    Different apps can implement different agent resolution strategies.

    The task tool expects this contract:
        spawn_fn(agent_name, instruction, parent_session, agent_configs, sub_session_id)

    Foundation provides this mechanism:
        prepared.spawn(child_bundle, instruction, session_id, parent_session)

    This function bridges the gap by resolving agent_name -> Bundle.
    """

    async def spawn_capability(
        agent_name: str,
        instruction: str,
        parent_session: Any,
        agent_configs: dict[str, dict[str, Any]],
        sub_session_id: str | None = None,
    ) -> dict[str, Any]:
        """Spawn a sub-session for agent delegation.

        Args:
            agent_name: Name of agent (e.g., "zen-architect")
            instruction: Task for the agent to execute
            parent_session: Parent session for inheritance
            agent_configs: Registry of available agent configurations
            sub_session_id: Optional session ID for resumption

        Returns:
            {"output": str, "session_id": str}
        """
        # POLICY: Resolve agent name to Bundle
        child_bundle = resolve_agent_bundle(
            agent_name,
            agent_configs,
            prepared.bundle,
        )

        # MECHANISM: Call foundation's spawn
        return await prepared.spawn(
            child_bundle=child_bundle,
            instruction=instruction,
            session_id=sub_session_id,
            parent_session=parent_session,
        )

    session.coordinator.register_capability("session.spawn", spawn_capability)


def resolve_agent_bundle(
    agent_name: str,
    agent_configs: dict[str, dict[str, Any]],
    parent_bundle: Bundle,
) -> Bundle:
    """Resolve agent name to a Bundle. APP-LAYER POLICY.

    Resolution order:
    1. agent_configs registry (passed by task tool from mount plan)
    2. Parent bundle's inline agents

    Apps can customize this resolution strategy.

    Args:
        agent_name: Name of the agent to resolve
        agent_configs: Registry from session config (mount plan "agents" section)
        parent_bundle: The parent bundle for fallback resolution

    Returns:
        Resolved Bundle

    Raises:
        ValueError: If agent not found
    """
    # 1. Check agent_configs registry first (task tool passes this from mount plan)
    if agent_name in agent_configs:
        config = agent_configs[agent_name]
        return Bundle(
            name=agent_name,
            version="1.0.0",
            session=config.get("session", {}),
            providers=config.get("providers", []),
            tools=config.get("tools", []),
            hooks=config.get("hooks", []),
            instruction=config.get("system", {}).get("instruction"),
        )

    # 2. Check parent bundle's inline agents
    if agent_name in parent_bundle.agents:
        config = parent_bundle.agents[agent_name]
        return Bundle(
            name=agent_name,
            version="1.0.0",
            session=config.get("session", {}),
            providers=config.get("providers", []),
            tools=config.get("tools", []),
            hooks=config.get("hooks", []),
            instruction=config.get("instruction"),
        )

    # Not found
    available = list(agent_configs.keys()) + list(parent_bundle.agents.keys())
    raise ValueError(f"Agent '{agent_name}' not found. Available: {available or 'none'}")


async def main() -> None:
    """Interactive end-to-end demo."""
    print_header("Amplifier Foundation: End-to-End Demo")

    # -------------------------------------------------------------------------
    # Step 1: Load Foundation
    # -------------------------------------------------------------------------
    local_foundation = Path(__file__).parent.parent.parent
    if (local_foundation / "bundle.md").exists():
        foundation_source = str(local_foundation)
        print_step(1, 5, "Load Foundation Bundle")
        print_success(f"Using local: {local_foundation}")
    else:
        foundation_source = "git+https://github.com/microsoft/amplifier-foundation@main"
        print_step(1, 5, "Load Foundation Bundle")
        print("       Fetching from GitHub...")

    foundation = await load_bundle(foundation_source)
    print_detail("Name", f"{foundation.name} v{foundation.version}")
    print_detail("Tools", str(len(foundation.tools)))

    # -------------------------------------------------------------------------
    # Step 2: Discover Providers
    # -------------------------------------------------------------------------
    print_step(2, 5, "Discover Available Providers")

    # Works for both local and remote (git-cached) bundles via foundation.base_path
    providers = discover_providers(foundation)

    if not providers:
        print("       No providers found!")
        return

    print_success(f"Found {len(providers)} provider(s)")
    display_providers(providers)

    # -------------------------------------------------------------------------
    # Step 3: Select Provider
    # -------------------------------------------------------------------------
    print_step(3, 5, "Select a Provider")
    print()
    selected = select_provider(providers)
    if not selected:
        print("\n    Exiting.")
        return

    print()
    print_success(f"Selected: {selected['name']} ({selected['model']})")

    # -------------------------------------------------------------------------
    # Step 4: Compose Bundles
    # -------------------------------------------------------------------------
    print_step(4, 5, "Compose Bundles")

    provider_bundle = await load_bundle(str(selected["file"]))
    # If user provided API key, inject it into the provider config
    if "api_key" in selected and provider_bundle.providers:
        provider_bundle.providers[0].setdefault("config", {})["api_key"] = selected["api_key"]

    composed = foundation.compose(provider_bundle)
    print_success("Foundation + Provider composed")

    print("\n       Preparing modules (downloading if needed)...")
    prepared = await composed.prepare()

    mount_plan = prepared.mount_plan
    orchestrator = mount_plan.get("session", {}).get("orchestrator", {}).get("module", "default")
    print_detail("Orchestrator", orchestrator)
    print_detail("Providers", str(len(mount_plan.get("providers", []))))
    print_detail("Tools", str(len(mount_plan.get("tools", []))))

    # -------------------------------------------------------------------------
    # Step 5: Execute
    # -------------------------------------------------------------------------
    prompt = get_user_prompt()
    if not prompt:
        print("\n    Exiting.")
        return

    print_step(5, 5, "Execute via AmplifierSession")

    try:
        # PreparedBundle.create_session() handles:
        # - Creates session with mount plan
        # - Mounts module resolver
        # - Initializes session
        #
        # App layer registers spawn capability (adapts task tool contract):
        # - Task tool calls: spawn_fn(agent_name, instruction, parent_session, agent_configs, sub_session_id)
        # - App resolves agent_name -> Bundle
        # - App calls: prepared.spawn(child_bundle, instruction, session_id, parent_session)
        session = await prepared.create_session()

        # Register app-layer spawn capability (adapts task tool's contract)
        register_spawn_capability(session, prepared)

        # Note: hooks-streaming-ui is included via foundation:behaviors/streaming-ui
        # which handles thinking blocks, tool calls, and token usage display

        print_success("Session created with sub-agent spawning enabled")
        print_detail("Session ID", session.session_id)

        async with session:
            print("\n       Executing...\n")
            response = await session.execute(prompt)
            print()
            print("─" * STEP_WIDTH)
            print("  Response")
            print("─" * STEP_WIDTH)
            print(f"\n{response}")

    except ImportError:
        print("\n    ✗  ERROR: amplifier-core not installed")
        print("       Install with: pip install amplifier-core")
        print("\n       Mount plan was successfully created:")
        _print_mount_plan_summary(mount_plan)
    except Exception as e:
        print(f"\n    ✗  Execution error: {e}")
        print("\n       Mount plan for debugging:")
        _print_mount_plan_summary(mount_plan)

    # -------------------------------------------------------------------------
    # Footer
    # -------------------------------------------------------------------------
    print()
    print("═" * STEP_WIDTH)
    print("  Demo complete")
    print("═" * STEP_WIDTH)


def _print_mount_plan_summary(mount_plan: dict) -> None:
    """Print a summary of the mount plan."""
    import json

    summary = {
        "session": mount_plan.get("session", {}),
        "providers": [
            {"module": p.get("module"), "model": p.get("config", {}).get("default_model")}
            for p in mount_plan.get("providers", [])
        ],
        "tools": [t.get("module") for t in mount_plan.get("tools", [])],
    }
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
