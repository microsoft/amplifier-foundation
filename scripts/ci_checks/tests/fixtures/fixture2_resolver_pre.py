# Provenance: amplifier-bundle-routing-matrix
# modules/hooks-routing/amplifier_module_hooks_routing/resolver.py
# at commit a88c1ff^ (immediately before PR #31 merged).
# https://github.com/microsoft/amplifier-bundle-routing-matrix/pull/31
from typing import Any


def find_provider_by_type(
    providers: dict[str, Any],
    type_name: str,
) -> tuple[str, Any] | None:
    """Find an installed provider by module type name or instance ID.

    Args:
        providers: Dict of mounted providers keyed by module id or instance id.
        type_name: Provider identifier from a matrix candidate's ``provider:``
            field (short type name or multi-instance id).

    Returns:
        ``(module_id, provider_instance)`` or ``None``.
    """
    for name, provider in providers.items():
        if type_name in (
            name,
            name.replace("provider-", ""),
            f"provider-{type_name}",
        ):
            return (name, provider)
    return None
