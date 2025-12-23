from __future__ import annotations

import getpass
import os
from pathlib import Path

# Repository root
ROOT = Path(__file__).resolve().parents[1]

PROVIDERS = {
    "sonnet": ROOT / "providers" / "anthropic-sonnet.yaml",
    "opus": ROOT / "providers" / "anthropic-opus.yaml",
    "gpt": ROOT / "providers" / "openai-gpt.yaml",
    "gpt-codex": ROOT / "providers" / "openai-gpt-codex.yaml",
}

FOUNDATIONS = {
    "minimal": ROOT / "bundles" / "minimal.yaml",
    "default": ROOT,  # root bundle path
}


def required_env_for(path: Path) -> str:
    return "OPENAI_API_KEY" if "openai" in path.name.lower() else "ANTHROPIC_API_KEY"


def select_provider(
    key: str = "sonnet",
    foundation: str = "minimal",
    prompt_for_env: bool = True,
) -> tuple[Path, Path, str]:
    """Resolve foundation/provider paths and ensure env is set."""
    if key not in PROVIDERS:
        raise ValueError(f"Unknown provider key: {key}")
    if foundation not in FOUNDATIONS:
        raise ValueError(f"Unknown foundation key: {foundation}")

    foundation_path = FOUNDATIONS[foundation]
    provider_path = PROVIDERS[key]

    if not foundation_path.exists():
        raise FileNotFoundError(f"Foundation bundle not found: {foundation_path}")
    if not provider_path.exists():
        raise FileNotFoundError(f"Provider file not found: {provider_path}")
    if foundation_path == provider_path:
        raise ValueError("Provider path cannot be the same as foundation path.")

    required_env = required_env_for(provider_path)
    if prompt_for_env and not os.getenv(required_env):
        os.environ[required_env] = getpass.getpass(f"Enter {required_env}: ")

    return foundation_path, provider_path, required_env


def print_provider_menu(selected_key: str) -> None:
    """Print a small menu to show available providers and selection."""
    print("Providers (set PROVIDER_KEY):")
    for key, path in PROVIDERS.items():
        mark = "->" if key == selected_key else "  "
        print(f"  {mark} {key}: {path.name}")
