"""Deep merge utilities for dictionaries and module lists."""

from __future__ import annotations

from typing import Any


def deep_merge(parent: dict[str, Any], child: dict[str, Any]) -> dict[str, Any]:
    """Deep merge two dictionaries.

    Child values override parent values. For nested dicts, merge recursively.
    For other types (including lists), child replaces parent.

    Args:
        parent: Base dictionary.
        child: Override dictionary.

    Returns:
        Merged dictionary (new dict, inputs not modified).
    """
    result = parent.copy()

    for key, child_value in child.items():
        if key in result:
            parent_value = result[key]
            # Only deep merge if both are dicts
            if isinstance(parent_value, dict) and isinstance(child_value, dict):
                result[key] = deep_merge(parent_value, child_value)
            else:
                result[key] = child_value
        else:
            result[key] = child_value

    return result


def merge_module_lists(
    parent: list[dict[str, Any]],
    child: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Merge two lists of module configs by module ID.

    Module configs are dicts with a 'module' key as identifier.
    If both lists have config for the same module ID, deep merge them
    (child overrides parent).

    Args:
        parent: Base list of module configs.
        child: Override list of module configs.

    Returns:
        Merged list of module configs (new list).

    Raises:
        TypeError: If any config in parent or child is not a dict.
    """
    # Index parent configs by module ID
    by_id: dict[str, dict[str, Any]] = {}
    for i, config in enumerate(parent):
        if not isinstance(config, dict):
            raise TypeError(
                f"Malformed module config at index {i}: expected dict with 'module' key, "
                f"got {type(config).__name__} {config!r}"
            )
        module_id = config.get("module")
        if module_id:
            by_id[module_id] = config.copy()

    # Process child configs
    for i, config in enumerate(child):
        if not isinstance(config, dict):
            raise TypeError(
                f"Malformed module config at index {i}: expected dict with 'module' key, "
                f"got {type(config).__name__} {config!r}"
            )
        module_id = config.get("module")
        if not module_id:
            continue

        if module_id in by_id:
            # Deep merge with existing
            by_id[module_id] = deep_merge(by_id[module_id], config)
        else:
            # Add new module
            by_id[module_id] = config.copy()

    return list(by_id.values())
