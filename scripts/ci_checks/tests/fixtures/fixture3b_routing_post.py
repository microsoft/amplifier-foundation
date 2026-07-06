# Provenance: amplifier-app-cli amplifier_app_cli/commands/routing.py
# _get_provider_config(), at commit 3f3f425 (PR #214 merged, fixed).
# https://github.com/microsoft/amplifier-app-cli/pull/214
from typing import Any


def _get_provider_config(provider_name: str, settings: Any) -> dict[str, Any] | None:
    """Look up the stored config dict for a provider by type name, module name,
    or instance id.
    """
    matches: list[dict[str, Any]] = []
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
            matches.append(p)

    if not matches:
        return None

    # Multiple instances of the same module can share a bare type/module
    # name match (matching by 'id' is already unambiguous). Resolve
    # deterministically to the highest-priority (lowest config.priority
    # number) instance instead of whichever is first in list order.
    def _priority(p: dict[str, Any]) -> int:
        config = p.get("config", {})
        return config.get("priority", 100) if isinstance(config, dict) else 100

    best = min(matches, key=_priority)
    return best.get("config", {})
