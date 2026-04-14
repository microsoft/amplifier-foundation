"""Deep merge utilities for dictionaries and module lists."""

from __future__ import annotations

import json
from typing import Any


def deep_merge(parent: dict[str, Any], child: dict[str, Any]) -> dict[str, Any]:
    """Deep merge two dictionaries.

    Child values override parent values. For nested dicts, merge recursively.
    For lists, concatenate with deduplication (parent items first, child items appended).
    For other scalar types, child replaces parent.

    List deduplication uses type-namespaced keys to prevent cross-type collisions
    (e.g. the string "{'a': 1}" and the dict {'a': 1} will NOT be treated as
    duplicates). For dicts and other non-primitives, json.dumps(sort_keys=True)
    is used so that key-order variations in otherwise-identical dicts are
    correctly identified as duplicates.

    This ensures that composing two behaviors that declare the same module
    (e.g. tool-skills) with list-typed config values (e.g. config.skills,
    config.search_paths) accumulates all entries rather than silently dropping
    the parent's list.

    Note: There is no mechanism for a child to replace a parent's list entirely.
    If replacement semantics are needed, structure the data differently (e.g.
    use a separate key for the override, or avoid sharing the same module
    declaration across behaviors that need disjoint configs).

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
            # Deep merge dicts, concatenate lists, replace everything else
            if isinstance(parent_value, dict) and isinstance(child_value, dict):
                result[key] = deep_merge(parent_value, child_value)
            elif isinstance(parent_value, list) and isinstance(child_value, list):
                # Concatenate with deduplication — parent items first, then child items.
                # Type-namespace the keys to prevent cross-type collisions
                # (e.g. string "{'a': 1}" vs dict {'a': 1}).
                # Use json.dumps with sort_keys for dicts to handle key-order variation.
                seen: set[Any] = set()
                merged: list[Any] = []
                for item in parent_value + child_value:
                    if isinstance(item, (str, int, float, bool, type(None))):
                        dedup_key: Any = (type(item), item)
                    else:
                        try:
                            dedup_key = (
                                type(item),
                                json.dumps(item, sort_keys=True, default=str),
                            )
                        except (TypeError, ValueError):
                            # Can't serialize — include without deduplication
                            merged.append(item)
                            continue
                    if dedup_key not in seen:
                        seen.add(dedup_key)
                        merged.append(item)
                result[key] = merged
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
    # Index parent configs by module ID (or instance id if present)
    by_id: dict[str, dict[str, Any]] = {}
    for i, config in enumerate(parent):
        if not isinstance(config, dict):
            raise TypeError(
                f"Malformed module config at index {i}: expected dict with 'module' key, "
                f"got {type(config).__name__} {config!r}"
            )
        module_id = config.get("id") or config.get("module")
        if module_id:
            by_id[module_id] = config.copy()

    # Process child configs
    for i, config in enumerate(child):
        if not isinstance(config, dict):
            raise TypeError(
                f"Malformed module config at index {i}: expected dict with 'module' key, "
                f"got {type(config).__name__} {config!r}"
            )
        module_id = config.get("id") or config.get("module")
        if not module_id:
            continue

        if module_id in by_id:
            # Deep merge with existing
            by_id[module_id] = deep_merge(by_id[module_id], config)
        else:
            # Add new module
            by_id[module_id] = config.copy()

    return list(by_id.values())
