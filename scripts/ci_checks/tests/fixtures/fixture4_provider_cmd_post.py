# Provenance: amplifier-app-cli amplifier_app_cli/commands/provider.py
# _find_provider_entry(), at commit 6986ad8 (PR #215 merged, fixed).
# https://github.com/microsoft/amplifier-app-cli/pull/215
from typing import Any


def _display_name(module: str) -> str:
    return module.removeprefix("provider-")


def _find_provider_entry(
    providers: list[dict[str, Any]], name: str
) -> dict[str, Any] | None:
    """Find a provider entry by name/id.

    Matches against:
    - 'id' field (for multi-instance providers) -- unambiguous by definition,
      so an id match is returned immediately.
    - 'module' field stripped of 'provider-' prefix, or the full 'module'
      field -- this can match 2+ instances of the same module. In that
      ambiguous case, resolve deterministically to the highest-priority
      (lowest `config.priority` value, defaulting to 100 when absent)
      instance instead of whichever happens to be first in list order.
    """
    for p in providers:
        if p.get("id") == name:
            return p

    matches: list[dict[str, Any]] = [
        p
        for p in providers
        if (module := p.get("module", ""))
        and (
            module == name
            or module == f"provider-{name}"
            or _display_name(module) == name
        )
    ]

    if not matches:
        return None

    def _priority(p: dict[str, Any]) -> int:
        config = p.get("config", {})
        return config.get("priority", 100) if isinstance(config, dict) else 100

    return min(matches, key=_priority)
