"""Dictionary navigation utilities."""

from __future__ import annotations

from typing import Any


def get_nested(
    data: dict[str, Any],
    path: list[str],
    default: Any = None,
) -> Any:
    """Get a value from a nested dictionary by path.

    Args:
        data: Dictionary to navigate.
        path: List of keys to traverse.
        default: Value to return if path not found.

    Returns:
        Value at path, or default if not found.

    Example:
        >>> get_nested({'a': {'b': {'c': 1}}}, ['a', 'b', 'c'])
        1
        >>> get_nested({'a': 1}, ['x', 'y'], default='not found')
        'not found'
    """
    current = data
    for key in path:
        if not isinstance(current, dict):
            return default
        if key not in current:
            return default
        current = current[key]
    return current


def set_nested(
    data: dict[str, Any],
    path: list[str],
    value: Any,
) -> None:
    """Set a value in a nested dictionary by path.

    Creates intermediate dicts as needed.

    Args:
        data: Dictionary to modify (modified in place).
        path: List of keys to traverse.
        value: Value to set at path.

    Example:
        >>> d = {}
        >>> set_nested(d, ['a', 'b', 'c'], 1)
        >>> d
        {'a': {'b': {'c': 1}}}
    """
    if not path:
        return

    current = data
    for key in path[:-1]:
        if key not in current or not isinstance(current[key], dict):
            current[key] = {}
        current = current[key]

    current[path[-1]] = value
