"""Bundle provenance tracking helpers.

Extracted from _dataclass.py to keep Bundle.compose() focused on merge logic.
These functions handle all provenance bookkeeping: initialisation, snapshot, and
tagging of newly-introduced items after each merge step.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from amplifier_foundation.configurator._types import Origin

if TYPE_CHECKING:
    from amplifier_foundation.bundle._dataclass import Bundle


def _prov_add(
    origins: dict[str, list[Origin]],
    key: str,
    bundle: str,
    via_behavior: str | None = None,
) -> None:
    """Append an Origin entry to the origins list for *key*, deduplicating.

    Deduplication is by (bundle, via_behavior) tuple — the same bundle cannot
    appear twice with the same via_behavior for the same key.

    Args:
        origins:      The origins dict to mutate (Bundle.origins).
        key:          Provenance key (e.g. 'tool:tool-bash').
        bundle:       Bundle name that owns the claim.
        via_behavior: Intermediate bundle that carried this item, or None if
                      the item was self-introduced by *bundle*.
    """
    if not bundle:  # Skip empty/None claimants (nameless bundles)
        return
    if key not in origins:
        origins[key] = []
    entry = Origin(bundle=bundle, via_behavior=via_behavior)
    if entry not in origins[key]:
        origins[key].append(entry)


def build_initial_provenance(
    bundle: "Bundle",
    prefixed_context: dict,
    pending_context: dict,
) -> dict[str, list[Origin]]:
    """Build the initial origins dict for a compose() operation.

    Preserves all prior attributions from *bundle.origins*, then tags any
    items in *bundle*'s lists that are not yet tracked (i.e. items that were
    added via the constructor rather than via a prior compose() call).

    Args:
        bundle: The ``self`` bundle at the start of compose().
        prefixed_context: The already-prefixed context dict built by compose()
            before the merge loop (keys have the ``{name}:{key}`` form).
        pending_context: The ``_pending_context`` dict copied from *bundle*.

    Returns:
        A new origins dict ready to be stored in the result bundle.
    """
    initial_origins: dict[str, list[Origin]] = {}

    # Step 1: preserve all prior attributions from self
    for prov_key, origin_list in bundle.origins.items():
        for origin in origin_list:
            _prov_add(initial_origins, prov_key, origin.bundle, origin.via_behavior)

    # Step 2: tag only UNTRACKED items in self's lists (constructor-built bundles
    # where origins starts empty).  We do NOT re-tag items that already appear
    # in self.origins — those were properly attributed by earlier compose() calls.
    for prefixed_key in prefixed_context:
        prov_key = f"context:{prefixed_key}"
        if prov_key not in initial_origins:
            _prov_add(initial_origins, prov_key, bundle.name)

    # Also tag pending context (namespace-prefixed refs deferred for resolution).
    for pending_name in pending_context:
        prov_key = f"context:{pending_name}"
        if prov_key not in initial_origins:
            _prov_add(initial_origins, prov_key, bundle.name)

    for mod in bundle.tools:
        module_id = mod.get("id") or mod.get("module")
        if module_id:
            prov_key = f"tool:{module_id}"
            if prov_key not in initial_origins:
                _prov_add(initial_origins, prov_key, bundle.name)

    for mod in bundle.providers:
        module_id = mod.get("id") or mod.get("module")
        if module_id:
            prov_key = f"provider:{module_id}"
            if prov_key not in initial_origins:
                _prov_add(initial_origins, prov_key, bundle.name)

    for mod in bundle.hooks:
        module_id = mod.get("id") or mod.get("module")
        if module_id:
            prov_key = f"hook:{module_id}"
            if prov_key not in initial_origins:
                _prov_add(initial_origins, prov_key, bundle.name)

    for agent_name in bundle.agents:
        prov_key = f"agent:{agent_name}"
        if prov_key not in initial_origins:
            _prov_add(initial_origins, prov_key, bundle.name)

    # Tag session module IDs
    session = bundle.session or {}
    orch = session.get("orchestrator") if isinstance(session, dict) else None
    if isinstance(orch, dict):
        orch_id = orch.get("id") or orch.get("module")
        if orch_id:
            prov_key = f"session.orchestrator:{orch_id}"
            if prov_key not in initial_origins:
                _prov_add(initial_origins, prov_key, bundle.name)

    ctx = session.get("context") if isinstance(session, dict) else None
    if isinstance(ctx, dict):
        ctx_id = ctx.get("id") or ctx.get("module")
        if ctx_id:
            prov_key = f"session.context:{ctx_id}"
            if prov_key not in initial_origins:
                _prov_add(initial_origins, prov_key, bundle.name)

    # Tag spawn keys
    for spawn_key in bundle.spawn or {}:
        prov_key = f"spawn:{spawn_key}"
        if prov_key not in initial_origins:
            _prov_add(initial_origins, prov_key, bundle.name)

    # Tag instruction presence
    if bundle.instruction is not None:
        prov_key = "instruction:"
        if prov_key not in initial_origins:
            _prov_add(initial_origins, prov_key, bundle.name)

    return initial_origins


def capture_existing_ids(result_bundle: "Bundle") -> dict[str, Any]:
    """Snapshot the set of IDs currently present in *result_bundle* before a merge.

    Called once per iteration of the compose loop, before merging *other* into
    *result*.  The returned dict is passed to :func:`track_provenance` so it can
    determine which items were genuinely introduced by *other*.

    Args:
        result_bundle: The accumulator bundle, captured immediately before the
            merge of the next *other* bundle.

    Returns:
        Dict with keys ``"tool_ids"``, ``"hook_ids"``, ``"provider_ids"``,
        ``"agent_names"``, ``"context_keys"``, ``"pending_keys"``,
        ``"session_keys"``, ``"spawn_keys"``, and ``"instruction_present"`` —
        each mapping to its respective snapshot type.
    """
    # Session module IDs
    session = result_bundle.session or {}
    session_keys: set[str] = set()
    if isinstance(session, dict):
        orch = session.get("orchestrator")
        if isinstance(orch, dict):
            orch_id = orch.get("id") or orch.get("module")
            if orch_id:
                session_keys.add(f"orchestrator:{orch_id}")
        ctx = session.get("context")
        if isinstance(ctx, dict):
            ctx_id = ctx.get("id") or ctx.get("module")
            if ctx_id:
                session_keys.add(f"context:{ctx_id}")

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
        "session_keys": session_keys,
        "spawn_keys": set((result_bundle.spawn or {}).keys()),
        "instruction_present": result_bundle.instruction is not None,
    }


def track_provenance(
    result: "Bundle",
    other: "Bundle",
    existing_ids: dict[str, Any],
) -> None:
    """Tag newly-introduced items and overlay provenance from *other* into *result*.

    Called once per iteration of the compose loop, **after** the data merge has
    already taken place.  Uses *existing_ids* (captured before the merge) to
    distinguish items that were already in *result* from items that *other*
    genuinely introduced.

    Two phases:
    1. Tag new items with ``other.name`` as the claimant (via_behavior=None).
    2. Overlay ``other.origins`` entries for new items only, preserving the
       original contributor chain: each propagated entry gets
       ``via_behavior = other.name`` so we can see A→B→X.

    Args:
        result: The accumulator bundle whose ``origins`` is mutated in-place.
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
    existing_session_keys = existing_ids.get("session_keys", set())
    existing_spawn_keys = existing_ids.get("spawn_keys", set())
    existing_instruction_present = existing_ids.get("instruction_present", False)

    # ------------------------------------------------------------------ #
    # Phase 1: tag items directly introduced by other.name                #
    # ------------------------------------------------------------------ #

    # Tag ONLY newly introduced modules from other for tools, providers, and hooks.
    for mod in other.tools:
        module_id = mod.get("id") or mod.get("module")
        if module_id and module_id not in existing_tool_ids:
            _prov_add(result.origins, f"tool:{module_id}", other.name)

    for mod in other.providers:
        module_id = mod.get("id") or mod.get("module")
        if module_id and module_id not in existing_provider_ids:
            _prov_add(result.origins, f"provider:{module_id}", other.name)

    for mod in other.hooks:
        module_id = mod.get("id") or mod.get("module")
        if module_id and module_id not in existing_hook_ids:
            _prov_add(result.origins, f"hook:{module_id}", other.name)

    # Agents: tag only NEW agent names.
    for agent_name in other.agents:
        if agent_name not in existing_agent_names:
            _prov_add(result.origins, f"agent:{agent_name}", other.name)

    # Context: tag new keys (compare against snapshot taken before the merge).
    for prefixed_key in result.context:
        if prefixed_key not in existing_context_keys:
            _prov_add(result.origins, f"context:{prefixed_key}", other.name)

    # Pending context: tag new pending keys.
    for pending_name in result._pending_context:
        if pending_name not in existing_pending_keys:
            _prov_add(result.origins, f"context:{pending_name}", other.name)

    # Session: tag new orchestrator/context module IDs.
    session = result.session or {}
    if isinstance(session, dict):
        orch = session.get("orchestrator")
        if isinstance(orch, dict):
            orch_id = orch.get("id") or orch.get("module")
            if orch_id and f"orchestrator:{orch_id}" not in existing_session_keys:
                _prov_add(result.origins, f"session.orchestrator:{orch_id}", other.name)
        ctx = session.get("context")
        if isinstance(ctx, dict):
            ctx_id = ctx.get("id") or ctx.get("module")
            if ctx_id and f"context:{ctx_id}" not in existing_session_keys:
                _prov_add(result.origins, f"session.context:{ctx_id}", other.name)

    # Spawn: tag new top-level keys.
    for spawn_key in result.spawn or {}:
        if spawn_key not in existing_spawn_keys:
            _prov_add(result.origins, f"spawn:{spawn_key}", other.name)

    # Instruction: tag if newly set.
    if result.instruction is not None and not existing_instruction_present:
        _prov_add(result.origins, "instruction:", other.name)

    # ------------------------------------------------------------------ #
    # Phase 2: overlay other's provenance for NEW items only              #
    # ------------------------------------------------------------------ #
    # This preserves the original contributor chain (e.g. tool-x was introduced
    # by "a" inside bundle "b"; when "b" is composed into "c", we propagate "a"
    # as tool-x's original contributor, with via_behavior="b" so we capture the
    # A→B→X chain).  We do NOT overlay provenance for items already in result
    # before this merge.
    for prov_key, origin_list in other.origins.items():
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
        elif category in ("session.orchestrator", "session.context"):
            # session.orchestrator:id or session.context:id
            # item_key is the module ID; check against existing_session_keys
            orch_or_ctx = prov_key.split(":", 1)[0]
            subkey = f"{orch_or_ctx.split('.', 1)[1]}:{item_key}"
            is_new = subkey not in existing_session_keys
        elif category == "spawn":
            is_new = item_key not in existing_spawn_keys
        elif prov_key == "instruction:":
            is_new = not existing_instruction_present
        else:
            is_new = True  # Unknown category: preserve all provenance

        if is_new:
            for origin in origin_list:
                # Preserve original bundle but set via_behavior = other.name
                # so we capture the A→B→X chain.
                # Guard: skip self-referential entries where origin.bundle == other.name
                # (e.g. when a bundle that directly introduced an item is also the propagator
                # — this happens when a bundle composes a peer bundle whose name happens to
                # equal the original introducer's name).
                if origin.bundle == other.name:
                    continue
                _prov_add(
                    result.origins,
                    prov_key,
                    origin.bundle,
                    via_behavior=other.name,
                )


def tag_container_provenance(bundle: "Bundle") -> None:
    """Tag a composed bundle as a container carrying items from its sub-bundles.

    Called by :func:`BundleRegistry._load_single` immediately after
    :func:`BundleRegistry._compose_includes` returns.  For each item in
    *bundle.origins* that was originally introduced by a sub-bundle (not directly
    by the container bundle itself), this function inserts an additional
    :class:`~amplifier_foundation.configurator._types.Origin` entry that records
    the container bundle as a claimant:

    ::

        # Before tag_container_provenance("foundation"):
        origins["tool:tool-apply-patch"] = [Origin("behavior-apply-patch", None)]

        # After:
        origins["tool:tool-apply-patch"] = [
            Origin("behavior-apply-patch", None),            # direct claimant
            Origin("foundation", "behavior-apply-patch"),    # foundation carries it
        ]

    For deeper nesting (X→Y→Z, Z provides T), the chain after each successive
    load is::

        # After _load_single("Y"):
        [Origin(Z, None), Origin(Y, Z)]

        # After _load_single("X"):
        [Origin(Z, None), Origin(Y, Z), Origin(X, Y)]

    Idempotent: items where *bundle.name* already appears as an owner are skipped
    (preserving the "first claimant wins" invariant for bundles that directly
    introduce an item).

    Args:
        bundle: The fully composed bundle returned by *_compose_includes*.  Its
            ``origins`` dict is mutated in-place.  If ``bundle.name`` is empty,
            this function is a no-op.
    """
    container_name = bundle.name
    if not container_name:
        return

    # Collect entries to add first to avoid mutating the dict while iterating it.
    entries_to_add: list[tuple[str, str, str]] = []

    for prov_key, origin_list in bundle.origins.items():
        if not origin_list:
            continue

        # If the container is already present as an owner for this item (either
        # because it introduced the item directly via Phase 1, or because a
        # previous tag_container_provenance call already ran), skip it.
        if any(o.bundle == container_name for o in origin_list):
            continue

        # Require at least one "direct claimant" (via_behavior=None) from a
        # sub-bundle.  If all origins are already chained (all have via_behavior
        # set), skip — the item is deeply inherited and the chain is already rich.
        has_direct_sub_claimant = any(
            o.via_behavior is None and o.bundle != container_name for o in origin_list
        )
        if not has_direct_sub_claimant:
            continue

        # via_behavior points to the most-recently-tagged chain entry — the bundle
        # that *directly* includes the sub-bundle containing this item.
        # For a fresh chain [Origin(Z, None)], last_bundle = Z.
        # For [Origin(Z, None), Origin(Y, Z)], last_bundle = Y.
        last_bundle = origin_list[-1].bundle
        if last_bundle == container_name:
            continue  # would create a self-referential entry

        entries_to_add.append((prov_key, container_name, last_bundle))

    for prov_key, bname, via in entries_to_add:
        _prov_add(bundle.origins, prov_key, bname, via_behavior=via)
