# Provenance: amplifier-app-cli amplifier_app_cli/commands/provider.py
# _find_provider_entry(), at commit 6986ad8^ (before PR #215).
# https://github.com/microsoft/amplifier-app-cli/pull/215
from typing import Any


def _display_name(module: str) -> str:
    return module.removeprefix("provider-")


def _find_provider_entry(
    providers: list[dict[str, Any]], name: str
) -> dict[str, Any] | None:
    """Find a provider entry by name/id.

    Matches against:
    - 'id' field (for multi-instance providers)
    - 'module' field stripped of 'provider-' prefix
    - full 'module' field
    """
    for p in providers:
        # Match by id field
        if p.get("id") == name:
            return p
        # Match by module name (with or without prefix)
        module = p.get("module", "")
        if (
            module == name
            or module == f"provider-{name}"
            or _display_name(module) == name
        ):
            return p
    return None
