# Provenance: amplifier-app-cli amplifier_app_cli/commands/routing.py
# _get_provider_config(), at commit 3f3f425^ (before PR #214).
# https://github.com/microsoft/amplifier-app-cli/pull/214
from typing import Any


def _get_provider_config(provider_name: str, settings: Any) -> dict[str, Any] | None:
    """Look up the stored config dict for a provider by type name, module name,
    or instance id.
    """
    for p in settings.get_provider_overrides():
        p_module = p.get("module", "")
        p_type = (
            p_module.removeprefix("provider-")
            if p_module.startswith("provider-")
            else p_module
        )
        if (
            p.get("id") == provider_name
            or p_type == provider_name
            or p_module == provider_name
        ):
            return p.get("config", {})
    return None
