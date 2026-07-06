# Provenance: amplifier-bundle-routing-matrix
# modules/hooks-routing/amplifier_module_hooks_routing/resolver.py
# at commit a88c1ff (PR #31 merged, fixed).
# https://github.com/microsoft/amplifier-bundle-routing-matrix/pull/31
from typing import Any


def _get_provider_specs(coordinator: Any) -> list[dict[str, Any]]:
    """Best-effort fetch of the mount plan's provider config list."""
    config = getattr(coordinator, "config", None)
    if not isinstance(config, dict):
        return []
    specs = config.get("providers", [])
    return specs if isinstance(specs, list) else []


def _spec_for_instance(
    provider_specs: list[dict[str, Any]], instance_id: str
) -> dict[str, Any] | None:
    """Find the mount plan config spec matching a runtime provider instance name."""
    for spec in provider_specs:
        if not isinstance(spec, dict):
            continue
        spec_id = spec.get("id") or spec.get("module", "")
        if spec_id == instance_id:
            return spec
    return None


def _module_type_of(spec: dict[str, Any] | None) -> str | None:
    """Extract the bare module type (e.g. "anthropic") from a provider spec."""
    if spec is None:
        return None
    module = spec.get("module", "")
    if not module:
        return None
    return module.replace("provider-", "")


def find_provider_by_type(
    providers: dict[str, Any],
    type_name: str,
    coordinator: Any = None,
) -> tuple[str, Any] | None:
    """Find an installed provider by module type name or instance ID.

    Matching strategy:
        1. Exact key, "provider-" prefix stripped, or "provider-" prefix
           added -- covers the single-instance case and any instance
           explicitly keyed by the bare type.
        2. Fallback: search the mount plan's provider config list for every
           instance whose underlying module type matches, and return the
           one configured with the highest priority (lowest priority number).
    """
    for name, provider in providers.items():
        if type_name in (
            name,
            name.replace("provider-", ""),
            f"provider-{type_name}",
        ):
            return (name, provider)

    provider_specs = _get_provider_specs(coordinator)
    if not provider_specs:
        return None

    candidates: list[tuple[int, str]] = []
    for name in providers:
        spec = _spec_for_instance(provider_specs, name)
        if _module_type_of(spec) == type_name:
            assert spec is not None  # narrowed by _module_type_of returning non-None
            priority = spec.get("config", {}).get("priority", 0)
            candidates.append((priority, name))

    if not candidates:
        return None

    candidates.sort(key=lambda c: c[0])
    best_name = candidates[0][1]
    return (best_name, providers[best_name])
