"""Static module-to-exported-names map for the standard foundation module set.

This is a v1 stopgap hand-maintained map.  It exists because the activator
currently does not introspect registered tool names after module.mount().
The correct long-term fix is for each module to publish its exports at
activation time; this file bridges the gap until that API exists.

Format: module_id -> list[str] of tool/hook names registered by that module.

When a module does not appear here, the inspector falls back to using the
module_id itself as the single name (which is correct for simple 1-tool-per-module
cases like tool-bash -> bash).

HOW TO MAINTAIN:
  Add or update entries when:
    - A new standard module is added to foundation bundles.
    - An existing module registers a tool name that doesn't match its short ID.

DO NOT:
    - List entries where the short name already matches (e.g. tool-bash -> bash);
      the fallback handles those automatically.
    - Use fuzzy/glob patterns here; exact names only.
"""

from __future__ import annotations

# module_id -> list of names as registered in coordinator.get("tools")
KNOWN_MODULE_EXPORTS: dict[str, list[str]] = {
    # tool-filesystem registers multiple filesystem tools
    "tool-filesystem": [
        "read_file",
        "write_file",
        "edit_file",
        "apply_patch",
        "glob",
    ],
    # tool-search registers grep and glob text-search tools
    "tool-search": [
        "grep",
        "glob",
    ],
    # tool-skills registers the load_skill tool
    "tool-skills": [
        "load_skill",
    ],
    # hooks-logging registers the LoggingHandler hook
    "hooks-logging": [
        "LoggingHandler",
    ],
    # hooks-python-check registers the python_check hook
    "hooks-python-check": [
        "python-check",
    ],
    # hooks-cost-bridge registers cost bridging hook
    "hooks-cost-bridge": [
        "cost-bridge",
    ],
}


def build_tool_to_module_map(
    extra_exports: dict[str, list[str]] | None = None,
) -> dict[str, str]:
    """Build a reverse map: tool_name -> module_id.

    Merges KNOWN_MODULE_EXPORTS with any extra_exports provided at
    activation time (from actual module introspection).

    Args:
        extra_exports: Additional or override exports from the activator.

    Returns:
        Dict mapping each registered tool name to its module_id.
    """
    merged = dict(KNOWN_MODULE_EXPORTS)
    if extra_exports:
        merged.update(extra_exports)

    reverse: dict[str, str] = {}
    for module_id, names in merged.items():
        for name in names:
            reverse[name] = module_id
    return reverse
