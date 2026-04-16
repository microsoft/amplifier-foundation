"""Provenance lookup utilities for SessionConfigurator.

Module-level helpers extracted from SessionConfigurator.__init__ so they can
be shared between BundleStateManager and BundleInspector without coupling
either class to the full configurator surface.
"""
from __future__ import annotations

# Maps singular provenance category keys (as stored in Bundle._provenance) to the
# plural contribution dict keys used in behaviors_list() output.
_PROV_CATEGORY_MAP: dict[str, str] = {
    "tool": "tools",
    "hook": "hooks",
    "provider": "providers",
    "agent": "agents",
    "context": "context",  # "context" is already the plural form used in contributions
}


def _normalize_module_name(name: str) -> str:
    """Normalize a name for fuzzy matching: lowercase and hyphens to underscores.

    Examples::

        >>> _normalize_module_name("python-check")
        'python_check'
        >>> _normalize_module_name("LSP")
        'lsp'
    """
    return name.lower().replace("-", "_")


def _build_normalized_prov_lookup(
    category: str, provenance: dict[str, list[str]]
) -> dict[str, list[str]]:
    """Build a map from normalized short module names to behavior value lists.

    For each provenance key of the form ``"{category}:{module_id}"``
    (e.g. ``"tool:tool-python-check"`` or ``"hook:hooks-logging"``), this
    extracts the short suffix by stripping the leading category prefix from the
    module ID, normalizes the result (lowercase, hyphens→underscores), and
    stores the mapping ``"python_check" → [behavior_name, ...]``.

    Two prefix forms are tried in order:

    1. **Singular prefix** — ``"{category}-"`` (e.g. ``"tool-"`` for
       ``"tool:tool-bash"``).
    2. **Plural prefix** — ``"{category}s-"`` (e.g. ``"hooks-"`` for
       ``"hook:hooks-logging"``).  Hook modules follow the plural convention
       (``hooks-logging``, ``hooks-python-check``) while tool/provider/agent
       modules use singular.

    This allows ``tools_list()`` and ``hooks_list()`` to match mounted names
    against provenance entries even when they differ in case or separators
    (e.g. ``LSP`` from ``tool-lsp``, ``apply_patch`` from
    ``tool-apply-patch``, ``python-check`` from ``hooks-python-check``).

    Args:
        category: The provenance category prefix, e.g. ``"tool"`` or ``"hook"``.
        provenance: The full bundle provenance dict (dict[str, list[str]]).

    Returns:
        Dict mapping normalized short name → list of behavior names.
    """
    result: dict[str, list[str]] = {}
    cat_key_prefix = f"{category}:"  # "tool:" or "hook:"
    singular_prefix = f"{category}-"  # "tool-"
    plural_prefix = f"{category}s-"  # "hooks-" (hook modules use plural)

    for key, behavior_names in provenance.items():
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
        result[norm_short] = behavior_names

    return result


def _lookup_prov_behavior(
    name: str,
    category: str,
    provenance: dict[str, list[str]],
    norm_prov_map: dict[str, list[str]],
) -> list[str] | None:
    """Resolve the behavior provenance for a mounted item using progressive matching.

    Tries four strategies in order, returning the first match:

    1. **Exact key** — ``"{category}:{name}"`` (e.g. ``"tool:bash"``)
    2. **Module-prefixed key** — ``"{category}:{category}-{name}"``
       (e.g. ``"tool:tool-bash"``)
    3. **Normalized exact** — lowercase + hyphens→underscores on both sides
       (e.g. ``"LSP"`` → ``"lsp"`` matching ``"tool:tool-lsp"``)
    4. **Normalized prefix containment** — the module's normalized short name
       is a word-boundary prefix of the normalized mounted name
       (e.g. ``"web"`` from ``"tool:tool-web"`` matches ``"web_search"`` /
       ``"web_fetch"``)

    Semantically unrelated names (e.g. ``"load_skill"`` from ``tool-skills``,
    ``"grep"`` / ``"glob"`` from ``tool-search``, or ``"read_file"`` from
    ``tool-filesystem``) will not match any strategy and return ``None``.
    This is correct and expected — those mappings require out-of-band metadata
    that is not available at runtime.

    Args:
        name: The mounted item name (e.g. ``"python_check"``, ``"LSP"``).
        category: The provenance category (e.g. ``"tool"``, ``"hook"``).
        provenance: The full bundle ``_provenance`` dict (dict[str, list[str]]).
        norm_prov_map: Pre-built normalized lookup from
            :func:`_build_normalized_prov_lookup`.

    Returns:
        List of behavior name strings, or ``None`` if no match is found.
    """
    # Strategy 1: exact raw key match
    behavior = provenance.get(f"{category}:{name}")
    if behavior:
        return behavior

    # Strategy 2: exact key with category-prefix on module ID
    behavior = provenance.get(f"{category}:{category}-{name}")
    if behavior:
        return behavior

    # Strategy 3: normalized exact match (case + hyphen/underscore insensitive)
    norm_name = _normalize_module_name(name)
    behavior = norm_prov_map.get(norm_name)
    if behavior:
        return behavior

    # Strategy 4: normalized prefix containment — module name is a word-boundary
    # prefix of the mounted name.  E.g. "web" is a prefix of "web_search".
    # Require an underscore boundary so "bash" doesn't match "bash_extras".
    for module_norm, beh in norm_prov_map.items():
        if norm_name.startswith(module_norm + "_"):
            return beh

    return None
