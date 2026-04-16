"""Bundle provenance tracking helpers.

Extracted from _dataclass.py to keep Bundle.compose() focused on merge logic.
These functions handle all provenance bookkeeping: initialisation, snapshot, and
tagging of newly-introduced items after each merge step.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from amplifier_foundation.bundle._dataclass import Bundle


def _prov_add(provenance: dict[str, list[str]], key: str, behavior: str) -> None:
    """Append *behavior* to the provenance list for *key*, deduplicating.

    Args:
        provenance: The provenance dict to mutate.
        key: Provenance key (e.g. 'tool:tool-bash').
        behavior: Bundle/behavior name to record as a claimant.
    """
    if not behavior:  # Skip empty string or None claimants (nameless bundles)
        return
    if key not in provenance:
        provenance[key] = []
    if behavior not in provenance[key]:
        provenance[key].append(behavior)


def build_initial_provenance(
    bundle: "Bundle",
    prefixed_context: dict,
    pending_context: dict,
) -> dict[str, list[str]]:
    """Build the initial provenance dict for a compose() operation.

    Preserves all prior attributions from *bundle._provenance*, then tags any
    items in *bundle*'s lists that are not yet tracked (i.e. items that were
    added via the constructor rather than via a prior compose() call).

    Args:
        bundle: The ``self`` bundle at the start of compose().
        prefixed_context: The already-prefixed context dict built by compose()
            before the merge loop (keys have the ``{name}:{key}`` form).
        pending_context: The ``_pending_context`` dict copied from *bundle*.

    Returns:
        A new provenance dict ready to be stored in the result bundle.
    """
    initial_provenance: dict[str, list[str]] = {}

    # Step 1: preserve all prior attributions from self
    for prov_key, claimants in bundle._provenance.items():
        for claimant in claimants:
            _prov_add(initial_provenance, prov_key, claimant)

    # Step 2: tag only UNTRACKED items in self's lists (constructor-built bundles
    # where _provenance starts empty).  We do NOT re-tag items that already appear
    # in self._provenance — those were properly attributed by earlier compose() calls.
    for prefixed_key in prefixed_context:
        prov_key = f"context:{prefixed_key}"
        if prov_key not in initial_provenance:
            _prov_add(initial_provenance, prov_key, bundle.name)

    # Also tag pending context (namespace-prefixed refs deferred for resolution).
    for pending_name in pending_context:
        prov_key = f"context:{pending_name}"
        if prov_key not in initial_provenance:
            _prov_add(initial_provenance, prov_key, bundle.name)

    for mod in bundle.tools:
        module_id = mod.get("id") or mod.get("module")
        if module_id:
            prov_key = f"tool:{module_id}"
            if prov_key not in initial_provenance:
                _prov_add(initial_provenance, prov_key, bundle.name)

    for mod in bundle.providers:
        module_id = mod.get("id") or mod.get("module")
        if module_id:
            prov_key = f"provider:{module_id}"
            if prov_key not in initial_provenance:
                _prov_add(initial_provenance, prov_key, bundle.name)

    for mod in bundle.hooks:
        module_id = mod.get("id") or mod.get("module")
        if module_id:
            prov_key = f"hook:{module_id}"
            if prov_key not in initial_provenance:
                _prov_add(initial_provenance, prov_key, bundle.name)

    for agent_name in bundle.agents:
        prov_key = f"agent:{agent_name}"
        if prov_key not in initial_provenance:
            _prov_add(initial_provenance, prov_key, bundle.name)

    return initial_provenance


def capture_existing_ids(result_bundle: "Bundle") -> dict[str, set]:
    """Snapshot the set of IDs currently present in *result_bundle* before a merge.

    Called once per iteration of the compose loop, before merging *other* into
    *result*.  The returned dict is passed to :func:`track_provenance` so it can
    determine which items were genuinely introduced by *other*.

    Args:
        result_bundle: The accumulator bundle, captured immediately before the
            merge of the next *other* bundle.

    Returns:
        Dict with keys ``"tool_ids"``, ``"hook_ids"``, ``"provider_ids"``,
        ``"agent_names"``, ``"context_keys"``, and ``"pending_keys"`` — each
        mapping to a :class:`set` of existing item identifiers.
    """
    return {
        "tool_ids": {
            m.get("id") or m.get("module")
            for m in result_bundle.tools
            if isinstance(m, dict)
        },
        "hook_ids": {
            m.get("id") or m.get("module")
            for m in result_bundle.hooks
            if isinstance(m, dict)
        },
        "provider_ids": {
            m.get("id") or m.get("module")
            for m in result_bundle.providers
            if isinstance(m, dict)
        },
        "agent_names": set(result_bundle.agents.keys()),
        "context_keys": set(result_bundle.context.keys()),
        "pending_keys": set(result_bundle._pending_context.keys()),
    }


def track_provenance(
    result: "Bundle",
    other: "Bundle",
    existing_ids: dict[str, set],
) -> None:
    """Tag newly-introduced items and overlay provenance from *other* into *result*.

    Called once per iteration of the compose loop, **after** the data merge has
    already taken place.  Uses *existing_ids* (captured before the merge) to
    distinguish items that were already in *result* from items that *other*
    genuinely introduced.

    Two phases:
    1. Tag new items with ``other.name`` as the claimant.
    2. Overlay ``other._provenance`` entries for new items only, preserving the
       original contributor chain (e.g. if tool-x was introduced by "a" inside
       bundle "b", when "b" is composed into "c" we propagate "a" as tool-x's
       original contributor, not "b" or "c").

    Args:
        result: The accumulator bundle whose ``_provenance`` is mutated in-place.
        other: The bundle that was just merged into *result*.
        existing_ids: Snapshot from :func:`capture_existing_ids`, taken before
            the merge of *other*.
    """
    existing_tool_ids = existing_ids["tool_ids"]
    existing_hook_ids = existing_ids["hook_ids"]
    existing_provider_ids = existing_ids["provider_ids"]
    existing_agent_names = existing_ids["agent_names"]
    existing_context_keys = existing_ids["context_keys"]
    existing_pending_keys = existing_ids["pending_keys"]

    # ------------------------------------------------------------------ #
    # Phase 1: tag items directly introduced by other.name                #
    # ------------------------------------------------------------------ #

    # Tag ONLY newly introduced modules from other for tools, providers, and hooks.
    for mod in other.tools:
        module_id = mod.get("id") or mod.get("module")
        if module_id and module_id not in existing_tool_ids:
            _prov_add(result._provenance, f"tool:{module_id}", other.name)

    for mod in other.providers:
        module_id = mod.get("id") or mod.get("module")
        if module_id and module_id not in existing_provider_ids:
            _prov_add(result._provenance, f"provider:{module_id}", other.name)

    for mod in other.hooks:
        module_id = mod.get("id") or mod.get("module")
        if module_id and module_id not in existing_hook_ids:
            _prov_add(result._provenance, f"hook:{module_id}", other.name)

    # Agents: tag only NEW agent names.
    for agent_name in other.agents:
        if agent_name not in existing_agent_names:
            _prov_add(result._provenance, f"agent:{agent_name}", other.name)

    # Context: tag new keys (compare against snapshot taken before the merge).
    for prefixed_key in result.context:
        if prefixed_key not in existing_context_keys:
            _prov_add(result._provenance, f"context:{prefixed_key}", other.name)

    # Pending context: tag new pending keys.
    for pending_name in result._pending_context:
        if pending_name not in existing_pending_keys:
            _prov_add(result._provenance, f"context:{pending_name}", other.name)

    # ------------------------------------------------------------------ #
    # Phase 2: overlay other's provenance for NEW items only              #
    # ------------------------------------------------------------------ #
    # This preserves the original contributor chain (e.g. tool-x was introduced
    # by "a" inside bundle "b"; when "b" is composed into "c", we propagate "a"
    # as tool-x's original contributor).  We do NOT overlay provenance for items
    # already in result before this merge — doing so would propagate over-attributed
    # provenance chains from bundles that inherited those items transitively.
    for prov_key, claimants in other._provenance.items():
        if ":" in prov_key:
            category, item_key = prov_key.split(":", 1)
        else:
            category, item_key = "", prov_key

        is_new = False
        if category == "tool":
            is_new = item_key not in existing_tool_ids
        elif category == "hook":
            is_new = item_key not in existing_hook_ids
        elif category == "provider":
            is_new = item_key not in existing_provider_ids
        elif category == "agent":
            is_new = item_key not in existing_agent_names
        elif category == "context":
            is_new = (
                item_key not in existing_context_keys
                and item_key not in existing_pending_keys
            )
        else:
            is_new = True  # Unknown category: preserve all provenance

        if is_new:
            for claimant in claimants:
                _prov_add(result._provenance, prov_key, claimant)
