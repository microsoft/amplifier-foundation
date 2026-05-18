"""Provenance lookup utilities for SessionConfigurator.

Module-level helpers extracted from SessionConfigurator.__init__ so they can
be shared between BundleStateManager and BundleInspector without coupling
either class to the full configurator surface.
"""

from __future__ import annotations

from amplifier_foundation.configurator._types import Origin


def _as_origin_list(raw: list | None) -> list[Origin]:
    """Convert a raw list (list[str] or list[Origin]) to list[Origin].

    Handles legacy test fixtures that store plain strings instead of Origin objects.
    """
    if not raw:
        return []
    result = []
    for item in raw:
        if isinstance(item, Origin):
            result.append(item)
        elif isinstance(item, str):
            result.append(Origin(bundle=item, via_behavior=None))
    return result


# Maps singular provenance category keys (as stored in Bundle.origins) to the
# plural contribution dict keys used in behaviors_list() output.
_PROV_CATEGORY_MAP: dict[str, str] = {
    "tool": "tools",
    "hook": "hooks",
    "provider": "providers",
    "agent": "agents",
    "context": "context",  # "context" is already the plural form used in contributions
    "session.orchestrator": "session",
    "session.context": "session",
    "spawn": "spawn",
    "instruction": "instruction",
}


def _normalize_module_name(name: str) -> str:
    """Normalize a name for matching: lowercase and hyphens to underscores.

    Examples::

        >>> _normalize_module_name("python-check")
        'python_check'
        >>> _normalize_module_name("LSP")
        'lsp'
    """
    return name.lower().replace("-", "_")


def _build_normalized_prov_lookup(
    category: str, origins: dict[str, list[Origin]]
) -> dict[str, list[Origin]]:
    """Build a map from normalized short module names to Origin lists.

    For each origins key of the form ``"{category}:{module_id}"``
    (e.g. ``"tool:tool-python-check"`` or ``"hook:hooks-logging"``), this
    extracts the short suffix by stripping the leading category prefix from the
    module ID, normalizes the result (lowercase, hyphens→underscores), and
    stores the mapping ``"python_check" → [Origin(...), ...]``.

    Two prefix forms are tried in order:

    1. **Singular prefix** — ``"{category}-"`` (e.g. ``"tool-"`` for
       ``"tool:tool-bash"``).
    2. **Plural prefix** — ``"{category}s-"`` (e.g. ``"hooks-"`` for
       ``"hook:hooks-logging"``).  Hook modules follow the plural convention
       (``hooks-logging``, ``hooks-python-check``) while tool/provider/agent
       modules use singular.

    Args:
        category: The origins category prefix, e.g. ``"tool"`` or ``"hook"``.
        origins:  The full bundle origins dict (dict[str, list[Origin]]).

    Returns:
        Dict mapping normalized short name → list of Origin objects.
    """
    result: dict[str, list[Origin]] = {}
    cat_key_prefix = f"{category}:"  # "tool:" or "hook:"
    singular_prefix = f"{category}-"  # "tool-"
    plural_prefix = f"{category}s-"  # "hooks-" (hook modules use plural)

    for key, origin_list in origins.items():
        if not key.startswith(cat_key_prefix):
            continue
        module_id = key[
            len(cat_key_prefix) :
        ]  # e.g. "tool-python-check", "hooks-logging"
        # Strip the redundant category prefix from the module ID.
        # Try singular first (e.g. "tool-bash" → "bash"), then plural
        # (e.g. "hooks-logging" → "logging").
        if module_id.startswith(singular_prefix):
            short = module_id[len(singular_prefix) :]  # e.g. "python-check"
        elif module_id.startswith(plural_prefix):
            short = module_id[len(plural_prefix) :]  # e.g. "logging"
        else:
            short = module_id
        norm_short = _normalize_module_name(short)  # e.g. "python_check"
        result[norm_short] = _as_origin_list(origin_list)

    return result


def _lookup_prov_origins(
    name: str,
    category: str,
    origins: dict[str, list[Origin]],
    norm_prov_map: dict[str, list[Origin]],
    module_exports: dict[str, list[str]] | None = None,
) -> list[Origin] | None:
    """Resolve the Origin provenance list for a mounted item.

    For tool items, attempts deterministic lookup via module_exports first:
    tool_name → module_id → origins["tool:module_id"].

    Falls back to normalized key matching for items where module_exports
    does not provide a direct mapping (e.g. legacy modules or non-tool
    categories).

    Args:
        name:           The mounted item name (e.g. ``"bash"``, ``"LSP"``).
        category:       The origins category (e.g. ``"tool"``, ``"hook"``).
        origins:        The full bundle ``origins`` dict.
        norm_prov_map:  Pre-built normalized lookup from
                        :func:`_build_normalized_prov_lookup`.
        module_exports: Map of module_id → list[tool_names] for deterministic
                        lookup of tool-name → module_id.

    Returns:
        List of Origin objects, or ``None`` if no match is found.
    """
    # --- Deterministic path for tools (uses module_exports reverse map) ---
    if category == "tool" and module_exports:
        # Build reverse: tool_name -> module_id
        for module_id, exported_names in module_exports.items():
            if name in exported_names:
                key = f"tool:{module_id}"
                raw = origins.get(key)
                if raw:
                    return _as_origin_list(raw)

    # --- Direct exact key matches ---
    # Strategy 1: exact raw key match
    raw = origins.get(f"{category}:{name}")
    if raw:
        return _as_origin_list(raw)

    # Strategy 2: exact key with category-prefix on module ID
    raw = origins.get(f"{category}:{category}-{name}")
    if raw:
        return _as_origin_list(raw)

    # --- Normalized fallback ---
    # Strategy 3: normalized exact match (case + hyphen/underscore insensitive)
    norm_name = _normalize_module_name(name)
    raw = norm_prov_map.get(norm_name)
    if raw:
        return _as_origin_list(raw)

    # Strategy 4: normalized prefix containment — module name is a word-boundary
    # prefix of the mounted name.  E.g. "web" is a prefix of "web_search".
    # Require an underscore boundary so "bash" doesn't match "bash_extras".
    for module_norm, raw_val in norm_prov_map.items():
        if norm_name.startswith(module_norm + "_"):
            return _as_origin_list(raw_val)

    return None


# ---------------------------------------------------------------------------
# Backward-compat alias — old callers used list[str]; new callers use list[Origin].
# This shim converts the new Origin list to the old string list for any call
# site that hasn't been updated yet.  Remove once all callers are migrated.
# ---------------------------------------------------------------------------


def _lookup_prov_behavior(
    name: str,
    category: str,
    provenance: dict[str, list[Origin]],
    norm_prov_map: dict[str, list[Origin]],
    module_exports: dict[str, list[str]] | None = None,
) -> list[Origin] | None:
    """Compatibility wrapper — delegates to :func:`_lookup_prov_origins`.

    Returns ``list[Origin]`` (not ``list[str]`` as in the old API).
    All callers have been updated to handle the new type.
    """
    return _lookup_prov_origins(
        name, category, provenance, norm_prov_map, module_exports
    )
