# Provenance: amplifier-foundation amplifier_foundation/spawn_utils.py
# at commit dec9828^ (immediately before PR #267 merged).
# https://github.com/microsoft/amplifier-foundation/pull/267
from typing import Any


def _find_provider_instance(
    providers: dict[str, Any],
    provider_name: str,
) -> Any | None:
    """Find a provider instance by name with flexible matching.

    Args:
        providers: Dict of mounted providers by name.
        provider_name: Provider to find (e.g., "anthropic").

    Returns:
        Provider instance or None if not found.
    """
    for name, provider in providers.items():
        if provider_name in (
            name,
            name.replace("provider-", ""),
            f"provider-{provider_name}",
        ):
            return provider
    return None
