#!/usr/bin/env python3
"""Example 4: Complete workflow - load, compose, prepare, execute.

TEACHABLE MOMENT: The full prepare() → create_session() → execute() flow

This is the SINGLE SOURCE for understanding:
- Bundle.prepare() - resolves all module sources, returns PreparedBundle
- PreparedBundle.create_session() - creates AmplifierSession with mount plan
- session.execute(prompt) - runs the LLM interaction

OPTIONAL ADVANCED features (marked clearly):
- @mention processing - loading files referenced in prompts
- spawn capability - sub-session delegation for agents

Requirements:
- ANTHROPIC_API_KEY or OPENAI_API_KEY environment variable set
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
# SECTION 1: FOUNDATION MECHANISM (The essential pattern - copy this)
# =============================================================================


async def load_and_compose(foundation_path: Path, provider_path: Path) -> Bundle:
    """Load foundation and provider bundles, compose them together.

    Args:
        foundation_path: Path to foundation bundle
        provider_path: Path to provider bundle (e.g., providers/anthropic.yaml)

    Returns:
        Composed bundle ready for prepare()
    """
    foundation = await load_bundle(str(foundation_path))
    provider = await load_bundle(str(provider_path))
    return foundation.compose(provider)


async def prepare_and_execute(composed: Bundle, prompt: str) -> str:
    """The core workflow: prepare → create_session → execute.

    This is the essential pattern for using amplifier-foundation.

    Args:
        composed: A composed bundle with providers configured
        prompt: User prompt to execute

    Returns:
        LLM response text
    """
    # prepare() resolves all module sources (downloads if needed)
    prepared = await composed.prepare()

    # create_session() creates AmplifierSession with the mount plan
    session = await prepared.create_session()

    # execute() runs the prompt through the LLM
    async with session:
        response = await session.execute(prompt)
        return response


# =============================================================================
# SECTION 2: APP-LAYER HELPERS (Customize for your app)
# =============================================================================


def discover_providers(bundle: Bundle) -> list[dict[str, Any]]:
    """Discover available provider bundles from foundation's providers/ directory."""
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
        module = provider_config.get("module", "")

        # Determine required env var
        env_var = "ANTHROPIC_API_KEY" if "anthropic" in module else "OPENAI_API_KEY"

        providers.append(
            {
                "name": bundle_info.get("name", provider_file.stem),
                "model": provider_config.get("config", {}).get("default_model", "unknown"),
                "file": provider_file,
                "env_var": env_var,
                "env_set": bool(os.environ.get(env_var)),
            }
        )

    return providers


def select_provider_interactive(providers: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Interactive provider selection."""
    print("\nAvailable providers:")
    for i, p in enumerate(providers, 1):
        status = "[OK]" if p["env_set"] else "[MISSING KEY]"
        print(f"  [{i}] {p['name']} ({p['model']}) {status}")

    while True:
        choice = input(f"\nSelect [1-{len(providers)}] or 'q' to quit: ")
        if choice.lower() == "q":
            return None
        try:
            idx = int(choice) - 1
            if 0 <= idx < len(providers):
                return providers[idx]
        except ValueError:
            pass
        print("Invalid selection.")


# =============================================================================
# SECTION 3: OPTIONAL ADVANCED - @Mention Processing
# =============================================================================


async def process_mentions(session: Any, prompt: str, foundation: Bundle) -> None:
    """OPTIONAL: Process @mentions in prompt and add file context to session.

    This is APP-LAYER POLICY for handling @file references.
    Skip this section if you don't need @mention support.

    Supported @mention types:
    - @filename.txt - loads file content
    - @path/to/file.md - loads file at path
    - @directory/ - loads directory listing (files/subdirs, NOT contents)
    - @bundle:resource - loads resource from registered bundle

    Directory @mentions provide awareness of available files without
    flooding context with all file contents.
    """
    from amplifier_foundation.mentions import BaseMentionResolver
    from amplifier_foundation.mentions import ContentDeduplicator
    from amplifier_foundation.mentions import format_context_block
    from amplifier_foundation.mentions import load_mentions
    from amplifier_foundation.mentions import parse_mentions

    mentions = parse_mentions(prompt)
    if not mentions:
        return

    print(f"  Processing {len(mentions)} @mention(s)...")

    resolver = BaseMentionResolver(
        bundles={"foundation": foundation},
        base_path=Path.cwd(),
    )
    deduplicator = ContentDeduplicator()

    results = await load_mentions(
        text=prompt,
        resolver=resolver,
        deduplicator=deduplicator,
        relative_to=Path.cwd(),
    )

    mention_to_path = {r.mention: r.resolved_path for r in results if r.resolved_path}
    context_block = format_context_block(deduplicator, mention_to_path)

    if context_block:
        context = session.coordinator.get("context")
        await context.add_message(
            {
                "role": "system",
                "content": f"Referenced files:\n\n{context_block}",
            }
        )
        unique_files = deduplicator.get_unique_files()
        dir_count = sum(1 for f in unique_files if f.content.startswith("Directory:"))
        file_count = len(unique_files) - dir_count
        parts = []
        if file_count:
            parts.append(f"{file_count} file(s)")
        if dir_count:
            parts.append(f"{dir_count} directory listing(s)")
        print(f"  Loaded {', '.join(parts)}")


# =============================================================================
# SECTION 4: OPTIONAL ADVANCED - Sub-Session Spawning
# =============================================================================


def register_spawn_capability(session: Any, prepared: PreparedBundle) -> None:
    """OPTIONAL: Register spawn capability for agent delegation.

    This enables the task tool to spawn sub-sessions for agents.
    Skip this section if you don't need agent delegation.
    """

    async def spawn_capability(
        agent_name: str,
        instruction: str,
        parent_session: Any,
        agent_configs: dict[str, dict[str, Any]],
        sub_session_id: str | None = None,
    ) -> dict[str, Any]:
        """Spawn sub-session for agent."""
        # Resolve agent name to Bundle (APP-LAYER POLICY)
        if agent_name in agent_configs:
            config = agent_configs[agent_name]
        elif agent_name in prepared.bundle.agents:
            config = prepared.bundle.agents[agent_name]
        else:
            available = list(agent_configs.keys()) + list(prepared.bundle.agents.keys())
            raise ValueError(f"Agent '{agent_name}' not found. Available: {available}")

        child_bundle = Bundle(
            name=agent_name,
            version="1.0.0",
            session=config.get("session", {}),
            providers=config.get("providers", []),
            tools=config.get("tools", []),
            hooks=config.get("hooks", []),
            instruction=config.get("instruction") or config.get("system", {}).get("instruction"),
        )

        return await prepared.spawn(
            child_bundle=child_bundle,
            instruction=instruction,
            session_id=sub_session_id,
            parent_session=parent_session,
        )

    session.coordinator.register_capability("session.spawn", spawn_capability)


# =============================================================================
# MAIN: Interactive Demo
# =============================================================================


async def main() -> None:
    """Interactive end-to-end demo."""
    print("=" * 60)
    print("Amplifier Foundation: Full Workflow Demo")
    print("=" * 60)

    # Step 1: Load foundation
    foundation_path = Path(__file__).parent.parent.parent
    print(f"\n[1/4] Loading foundation from: {foundation_path}")
    foundation = await load_bundle(str(foundation_path))
    print(f"      Loaded: {foundation.name} v{foundation.version}")

    # Step 2: Discover and select provider
    print("\n[2/4] Discovering providers...")
    providers = discover_providers(foundation)
    if not providers:
        print("      No providers found!")
        return

    selected = select_provider_interactive(providers)
    if not selected:
        print("\nExiting.")
        return

    if not selected["env_set"]:
        print(f"\n  WARNING: {selected['env_var']} not set. Provider may fail.")

    # Step 3: Compose and prepare
    print(f"\n[3/4] Composing foundation + {selected['name']}...")
    composed = foundation.compose(await load_bundle(str(selected["file"])))

    print("      Preparing (downloading modules if needed)...")
    prepared = await composed.prepare()
    print("      Ready!")

    # Step 4: Execute
    print("\n[4/4] Enter prompt (or 'q' to quit):")
    prompt = input("> ")
    if prompt.lower() == "q":
        print("\nExiting.")
        return

    print("\n      Executing...")
    try:
        session = await prepared.create_session()

        # OPTIONAL: Register advanced features
        register_spawn_capability(session, prepared)

        async with session:
            # OPTIONAL: Process @mentions
            await process_mentions(session, prompt, foundation)

            response = await session.execute(prompt)

        print("\n" + "-" * 60)
        print("Response:")
        print("-" * 60)
        print(response)

    except ImportError:
        print("\n  ERROR: amplifier-core not installed")
        print("  Install with: pip install amplifier-core")
    except Exception as e:
        print(f"\n  ERROR: {e}")

    print("\n" + "=" * 60)
    print("Demo complete")
    print("=" * 60)


if __name__ == "__main__":
    asyncio.run(main())
