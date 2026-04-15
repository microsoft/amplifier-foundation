"""Provenance-aware bundle loading for amplifier_configurator.

The core function is ``build_provenance_map(source)`` which loads a bundle and
all its includes, tracking which behavior contributed each part (tool, hook,
agent, context, provider).  The result is a :class:`ProvenanceMap` that lets
callers see exactly where every token comes from.

Algorithm overview (see design doc §4 for full details):

  Step 1 — Load root bundle with ``auto_include=False`` to get raw parts and
            the include list without following them.
  Step 2 — Recursively load each include with ``_load_behavior_tree()``,
            bottom-up (leaves first, parent last).
  Step 5 — Deduplicate parts with last-write-wins (parent overrides child).
  Step 7 — Load the fully-composed bundle with ``auto_include=True`` and
            resolve pending context paths.
  Step 8 — Back-fill context parts that still have 0 tokens from the composed
            bundle's resolved paths.
"""

from __future__ import annotations

import dataclasses
from pathlib import Path
from typing import Any

from amplifier_foundation import load_bundle
from amplifier_foundation.bundle import Bundle
from amplifier_foundation.registry import BundleRegistry

from .models import BehaviorInfo, LoadError, PartKind, ProvenanceMap, TrackedPart
from .tokens import estimate_tokens_for_file, estimate_tokens_for_text

# Maximum recursion depth for include trees (safety limit).
MAX_INCLUDE_DEPTH: int = 10


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------


def _extract_include_uri(entry: Any) -> str | None:
    """Extract a URI string from a bundle include entry.

    Foundation's ``includes`` list accepts two formats:

    * ``{"bundle": "<uri>"}`` — dict with a ``"bundle"`` key
    * ``"<uri>"`` — plain string

    Returns the URI string, or ``None`` for unknown formats.
    """
    if isinstance(entry, str):
        return entry
    if isinstance(entry, dict) and "bundle" in entry:
        return entry["bundle"]
    return None


def _extract_parts_from_bundle(
    bundle: Bundle,
    source_behavior: str | None,
) -> list[TrackedPart]:
    """Extract all parts from a raw (uncomposed) Bundle into TrackedPart objects.

    Parameters
    ----------
    bundle:
        The raw bundle to extract parts from.
    source_behavior:
        The name of the behavior that owns this bundle, or ``None`` for the
        root bundle's own parts.

    Returns
    -------
    list[TrackedPart]
        Parts in declaration order: tools, hooks, providers, agents, context.
    """
    parts: list[TrackedPart] = []

    # --- Tools (tokens = 0; descriptions are generated at runtime) -----------
    for tool in bundle.tools:
        name: str = tool.get("module") or tool.get("id", "?")
        parts.append(
            TrackedPart(
                kind=PartKind.TOOL,
                name=name,
                source_behavior=source_behavior,
                tokens=0,
                config=tool,
                namespace_path=None,
            )
        )

    # --- Hooks (tokens = 0; injections are dynamic and per-turn) -------------
    for hook in bundle.hooks:
        name = hook.get("module") or hook.get("id", "?")
        parts.append(
            TrackedPart(
                kind=PartKind.HOOK,
                name=name,
                source_behavior=source_behavior,
                tokens=0,
                config=hook,
                namespace_path=None,
            )
        )

    # --- Providers (tokens = 0; they contribute no context tokens) -----------
    for provider in bundle.providers:
        name = provider.get("module") or provider.get("id", "?")
        parts.append(
            TrackedPart(
                kind=PartKind.PROVIDER,
                name=name,
                source_behavior=source_behavior,
                tokens=0,
                config=provider,
                namespace_path=None,
            )
        )

    # --- Agents (tokens = description + instruction) -------------------------
    for agent_name, agent_config in bundle.agents.items():
        description: str | None = agent_config.get("description")
        instruction: str | None = agent_config.get("instruction")
        tokens = estimate_tokens_for_text(description) + estimate_tokens_for_text(
            instruction
        )
        parts.append(
            TrackedPart(
                kind=PartKind.AGENT,
                name=agent_name,
                source_behavior=source_behavior,
                tokens=tokens,
                config=agent_config,
                namespace_path=None,
            )
        )

    # --- Context (tokens from file content if path is resolvable) ------------
    for ctx_name, ctx_path in bundle.context.items():
        namespaced_name = f"{bundle.name}:{ctx_name}"
        if isinstance(ctx_path, Path):
            tokens = estimate_tokens_for_file(ctx_path)
        else:
            tokens = 0
        config: dict[str, Any] = {"path": str(ctx_path)} if ctx_path else {}
        parts.append(
            TrackedPart(
                kind=PartKind.CONTEXT,
                name=namespaced_name,
                source_behavior=source_behavior,
                tokens=tokens,
                config=config,
                namespace_path=None,
            )
        )

    # --- Pending context (namespace-URI refs not yet resolved to file paths) --
    # Foundation stores namespace-referenced context entries in _pending_context
    # as {"namespace:path": "namespace:path"} when a bundle is loaded with
    # auto_include=False.  They only migrate to bundle.context after the fully-
    # composed bundle calls resolve_pending_context() (Step 7 of the algorithm).
    # We emit 0-token TrackedParts here so _backfill_context_tokens() can fill
    # them in from the composed bundle once resolve_pending_context() has run.
    # The part name is the full reference string (e.g. "amplifier:context/foo.md")
    # because that is the key used in composed.context after resolution, which
    # ensures the backfill lookup succeeds and deduplication across behaviors
    # with the same context file works correctly.
    for _ctx_key, ctx_ref in bundle._pending_context.items():
        parts.append(
            TrackedPart(
                kind=PartKind.CONTEXT,
                name=ctx_ref,
                source_behavior=source_behavior,
                tokens=0,
                config={"ref": ctx_ref},
                namespace_path=ctx_ref,
            )
        )

    return parts


async def _load_behavior_tree(
    uri: str,
    depth: int,
    chain: tuple[str, ...],
    registry: BundleRegistry,
    seen: set[str],
) -> tuple[list[BehaviorInfo], dict[str, Bundle]]:
    """Recursively load a behavior and all its sub-includes.

    Returns results in **bottom-up** order (deepest leaves first, this
    behavior last) so that the caller can build ``include_order`` by simply
    appending the returned list.

    Parameters
    ----------
    uri:
        The URI of the behavior to load.
    depth:
        Current nesting depth (0 for direct includes of root).
    chain:
        Tuple of ancestor bundle names leading to this behavior.
    registry:
        Shared BundleRegistry for caching across all loads.
    seen:
        URIs already in the current call chain (for circular-include detection).

    Returns
    -------
    tuple[list[BehaviorInfo], dict[str, Bundle]]
        Ordered list of BehaviorInfo (bottom-up) plus a mapping of
        behavior-name → raw Bundle (for ``ProvenanceMap._raw_bundles``).

    Raises
    ------
    LoadError
        If the depth limit is exceeded, a circular include is detected, or
        Foundation raises an exception while loading the bundle.
    """
    if depth > MAX_INCLUDE_DEPTH:
        raise LoadError(
            source=uri,
            cause=ValueError(
                f"Include tree exceeds maximum depth ({MAX_INCLUDE_DEPTH}) at {uri!r}. "
                "Possible circular reference."
            ),
        )

    if uri in seen:
        raise LoadError(
            source=uri,
            cause=ValueError(f"Circular include detected: {(*chain, uri)!r}"),
        )

    # Load the behavior bundle without following its own includes.
    # Resolve namespace URIs (e.g. "lean:behaviors/lean-foundation") to real file
    # or git URIs before calling Foundation's load_bundle, which uses a plain
    # SimpleSourceResolver that cannot handle namespace:path syntax.  The
    # registry already knows about the root bundle at this point (it was
    # registered in build_provenance_map's Step 1), so _resolve_include_source
    # can look up the namespace and return a file:// URI.
    try:
        effective_uri = registry._resolve_include_source(uri) or uri
        behavior_raw = await load_bundle(
            effective_uri, auto_include=False, registry=registry
        )
    except Exception as exc:
        raise LoadError(source=uri, cause=exc) from exc

    raw_parts = tuple(
        _extract_parts_from_bundle(behavior_raw, source_behavior=behavior_raw.name)
    )

    # Recurse into sub-includes before adding this behavior (bottom-up order).
    new_seen = seen | {uri}
    sub_behavior_infos: list[BehaviorInfo] = []
    sub_raw_bundles: dict[str, Bundle] = {}

    for include_entry in behavior_raw.includes:
        sub_uri = _extract_include_uri(include_entry)
        if sub_uri is None:
            continue
        sub_list, sub_bundles = await _load_behavior_tree(
            sub_uri,
            depth + 1,
            chain + (behavior_raw.name,),
            registry,
            new_seen,
        )
        sub_behavior_infos.extend(sub_list)
        sub_raw_bundles.update(sub_bundles)

    # Use effective_uri (the fully-resolved git/file URI) rather than the
    # original namespace URI.  Storing the resolved URI ensures that when
    # serialize_bundle() writes the includes list it emits standalone git URIs
    # (e.g. "git+https://…#subdirectory=behaviors/foo.yaml") rather than
    # namespace-relative URIs (e.g. "foundation:behaviors/foo").
    #
    # Namespace URIs are only resolvable within Foundation's bundle registry; a
    # saved .md file that contains them breaks round-trip loading because
    # Foundation re-registers the bundle's name as a namespace pointing to the
    # saved file's location, then fails to find the sub-behaviors there.
    behavior_info = BehaviorInfo(
        name=behavior_raw.name,
        uri=effective_uri,
        parts=raw_parts,
        raw_parts=raw_parts,
        total_tokens=sum(p.tokens for p in raw_parts),
        depth=depth,
        include_chain=chain + (behavior_raw.name,),
        instruction=behavior_raw.instruction,
    )

    raw_bundles: dict[str, Bundle] = {behavior_raw.name: behavior_raw}
    raw_bundles.update(sub_raw_bundles)

    # Return: sub-behaviors first (bottom-up), then this behavior.
    return sub_behavior_infos + [behavior_info], raw_bundles


def _deduplicate_parts(
    behaviors: dict[str, BehaviorInfo],
    root_parts: tuple[TrackedPart, ...],
    include_order: tuple[str, ...],
) -> tuple[tuple[TrackedPart, ...], dict[str, BehaviorInfo]]:
    """Apply last-write-wins deduplication across all behaviors.

    Walk ``include_order`` (bottom-up, so parent behaviors come later and
    therefore *win* over children with the same part).  Root parts win over
    everything — with one exception: context files.

    Foundation aggregates every sub-behavior's ``_pending_context`` into the
    root bundle's ``_pending_context``.  If the root's context parts
    unconditionally override sub-behavior context, every context file ends up
    attributed to ``<root>`` (``source_behavior=None``) and removing any
    behavior produces zero token savings.

    Correct attribution rule: root context wins *only* for files that no
    sub-behavior claimed.  Sub-behavior attribution wins for all files that
    appear in at least one behavior's ``raw_parts``.

    Returns
    -------
    tuple[tuple[TrackedPart, ...], dict[str, BehaviorInfo]]
        ``(all_parts, updated_behaviors)`` where each behavior's ``parts``
        field contains only the parts it *won* after deduplication.
    """
    # last-write-wins accumulator: (kind, name) → winning TrackedPart
    winner: dict[tuple[PartKind, str], TrackedPart] = {}

    # Walk behaviors in include_order (leaf first → parent last).
    for uri in include_order:
        behavior = behaviors.get(uri)
        if behavior is None:
            continue
        for part in behavior.raw_parts:
            winner[(part.kind, part.name)] = part

    # Root parts override everything (parent composes last in Foundation).
    # Exception: CONTEXT parts — root's _pending_context is an *aggregate* of
    # all sub-behavior context, so sub-behavior attribution should win.  Root
    # context only fills slots that no sub-behavior claimed (e.g. context
    # declared directly on the root bundle, not via an include).
    for part in root_parts:
        if part.kind == PartKind.CONTEXT:
            winner.setdefault((part.kind, part.name), part)
        else:
            winner[(part.kind, part.name)] = part

    # Rebuild each BehaviorInfo with only its winning parts.
    updated_behaviors: dict[str, BehaviorInfo] = {}
    for uri, behavior in behaviors.items():
        winning_parts = tuple(
            part
            for part in behavior.raw_parts
            if winner.get((part.kind, part.name)) is part
        )
        updated_behaviors[uri] = dataclasses.replace(
            behavior,
            parts=winning_parts,
            total_tokens=sum(p.tokens for p in winning_parts),
        )

    all_parts = tuple(winner.values())
    return all_parts, updated_behaviors


def _backfill_context_tokens(
    behaviors: dict[str, BehaviorInfo],
    root_parts: tuple[TrackedPart, ...],
    include_order: tuple[str, ...],
    composed: Bundle,
) -> tuple[dict[str, BehaviorInfo], tuple[TrackedPart, ...], tuple[TrackedPart, ...]]:
    """Back-fill 0-token context parts using the fully-composed bundle's paths.

    After loading individual behaviors with ``auto_include=False``, context
    paths may still be unresolved (pending).  The fully-composed bundle has
    resolved all paths via ``resolve_pending_context()``.  This function
    patches any context part whose ``tokens == 0`` with the real file token
    count derived from the composed bundle.

    Context files extracted from the root bundle's ``_pending_context`` land in
    ``root_parts`` (not in any behavior's parts), because Foundation's
    last-write-wins deduplication in ``_deduplicate_parts`` lets root_parts
    override every behavior.  ``root_parts`` must therefore be patched here
    alongside ``behaviors``; otherwise context token counts remain zero.

    Returns
    -------
    tuple[dict[str, BehaviorInfo], tuple[TrackedPart, ...], tuple[TrackedPart, ...]]
        ``(updated_behaviors, updated_root_parts, all_parts)`` with refreshed
        token counts in both the behavior map and the root parts tuple.
    """
    # Build a {context_name: tokens} lookup from the composed bundle.
    resolved_tokens: dict[str, int] = {}
    for ctx_name, ctx_path in composed.context.items():
        if isinstance(ctx_path, Path) and ctx_path.exists():
            resolved_tokens[ctx_name] = estimate_tokens_for_file(ctx_path)

    if not resolved_tokens:
        # Nothing to back-fill — skip the rebuild.
        all_parts = _recompute_all_parts(behaviors, root_parts, include_order)
        return behaviors, root_parts, all_parts

    # Patch root_parts that have 0-token context entries.
    #
    # When the root bundle is loaded with auto_include=False, Foundation still
    # propagates the full _pending_context from all sub-behaviors up to the
    # root level.  _extract_parts_from_bundle() then places every context
    # reference into root_parts (source_behavior=None).  After
    # _deduplicate_parts() the root wins every (CONTEXT, name) slot, so
    # behaviors end up with zero context parts.  Without patching root_parts,
    # all context files would remain at 0 tokens even though the composed
    # bundle has resolved their paths.
    new_root = list(root_parts)
    for i, part in enumerate(new_root):
        if part.kind == PartKind.CONTEXT and part.tokens == 0:
            tokens = resolved_tokens.get(part.name, 0)
            if tokens > 0:
                new_root[i] = dataclasses.replace(part, tokens=tokens)
    updated_root_parts = tuple(new_root)

    # Patch behaviors that have 0-token context parts (handles bundles where
    # context is NOT fully propagated to the root, e.g. deeply nested includes).
    updated: dict[str, BehaviorInfo] = {}
    for uri, behavior in behaviors.items():
        new_parts = list(behavior.parts)
        new_raw = list(behavior.raw_parts)
        changed = False

        for i, part in enumerate(new_parts):
            if part.kind == PartKind.CONTEXT and part.tokens == 0:
                tokens = resolved_tokens.get(part.name, 0)
                if tokens > 0:
                    new_parts[i] = dataclasses.replace(part, tokens=tokens)
                    changed = True

        for i, part in enumerate(new_raw):
            if part.kind == PartKind.CONTEXT and part.tokens == 0:
                tokens = resolved_tokens.get(part.name, 0)
                if tokens > 0:
                    new_raw[i] = dataclasses.replace(part, tokens=tokens)

        if changed:
            updated[uri] = dataclasses.replace(
                behavior,
                parts=tuple(new_parts),
                raw_parts=tuple(new_raw),
                total_tokens=sum(p.tokens for p in new_parts),
            )
        else:
            updated[uri] = behavior

    all_parts = _recompute_all_parts(updated, updated_root_parts, include_order)
    return updated, updated_root_parts, all_parts


def _recompute_all_parts(
    behaviors: dict[str, BehaviorInfo],
    root_parts: tuple[TrackedPart, ...],
    include_order: tuple[str, ...],
) -> tuple[TrackedPart, ...]:
    """Recompute ``all_parts`` after patching behavior parts.

    Applies the same last-write-wins rule as ``_deduplicate_parts``, including
    the CONTEXT exception: root context only fills unclaimed slots.
    """
    winner: dict[tuple[PartKind, str], TrackedPart] = {}
    for uri in include_order:
        behavior = behaviors.get(uri)
        if behavior is None:
            continue
        for part in behavior.raw_parts:
            winner[(part.kind, part.name)] = part
    for part in root_parts:
        if part.kind == PartKind.CONTEXT:
            winner.setdefault((part.kind, part.name), part)
        else:
            winner[(part.kind, part.name)] = part
    return tuple(winner.values())


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


async def build_provenance_map(source: str) -> ProvenanceMap:
    """Build a full provenance map for a bundle and all its includes.

    This is the primary entry point for this module.  It:

    1. Loads the root bundle without following includes (Step 1).
    2. Recursively loads all includes, bottom-up (Steps 2–3).
    3. Deduplicates parts across behaviors (Step 5).
    4. Loads the fully-composed bundle (Step 7).
    5. Back-fills 0-token context parts (Step 8).

    Parameters
    ----------
    source:
        Any Foundation-supported source: bundle name, git URI, or file path.

    Returns
    -------
    ProvenanceMap
        Complete provenance record with per-behavior token attribution.

    Raises
    ------
    LoadError
        Wraps any Foundation exception that occurs during loading.
    """
    registry = BundleRegistry()

    # -------------------------------------------------------------------------
    # Step 1: Load the root bundle WITHOUT following includes.
    # -------------------------------------------------------------------------
    try:
        root_raw = await load_bundle(source, auto_include=False, registry=registry)
    except Exception as exc:
        raise LoadError(source=source, cause=exc) from exc

    root_name = root_raw.name
    root_instruction = root_raw.instruction
    root_instruction_tokens = estimate_tokens_for_text(root_instruction)
    session_config: dict[str, Any] = dict(root_raw.session) if root_raw.session else {}
    spawn_config: dict[str, Any] | None = (
        dict(root_raw.spawn) if root_raw.spawn else None
    )

    # Collect root-level parts — source_behavior=None.
    root_parts = tuple(_extract_parts_from_bundle(root_raw, source_behavior=None))

    # -------------------------------------------------------------------------
    # Steps 2–3: Recursively load all includes (depth-first, bottom-up).
    # -------------------------------------------------------------------------
    all_behavior_infos: list[BehaviorInfo] = []
    accumulated_raw_bundles: dict[str, Bundle] = {root_name: root_raw}

    for include_entry in root_raw.includes:
        uri = _extract_include_uri(include_entry)
        if uri is None:
            continue
        sub_list, sub_raw = await _load_behavior_tree(
            uri=uri,
            depth=0,
            chain=(root_name,),
            registry=registry,
            seen=set(),
        )
        all_behavior_infos.extend(sub_list)
        accumulated_raw_bundles.update(sub_raw)

    # Build behaviors dict (keyed by URI) and include_order (insertion order).
    behaviors: dict[str, BehaviorInfo] = {}
    include_order_list: list[str] = []
    for beh_info in all_behavior_infos:
        if beh_info.uri not in behaviors:
            behaviors[beh_info.uri] = beh_info
            include_order_list.append(beh_info.uri)

    include_order = tuple(include_order_list)

    # -------------------------------------------------------------------------
    # Step 5: Deduplicate parts (last-write-wins; root overrides all).
    # -------------------------------------------------------------------------
    all_parts, behaviors = _deduplicate_parts(behaviors, root_parts, include_order)

    # -------------------------------------------------------------------------
    # Step 7: Load the fully-composed bundle.
    #
    # IMPORTANT: use a fresh registry (no registry= parameter) rather than the
    # shared one from Steps 1-2.  The shared registry caches per-behavior bundles
    # from their auto_include=False loads; passing it here causes Foundation to
    # return those cached raw bundles instead of performing a full composition.
    # A fresh load lets Foundation accumulate source_base_paths and _pending_context
    # from all included bundles, which _backfill_context_tokens() needs.
    # -------------------------------------------------------------------------
    try:
        composed = await load_bundle(source, auto_include=True)
        composed.resolve_pending_context()
    except Exception as exc:
        raise LoadError(source=source, cause=exc) from exc

    # -------------------------------------------------------------------------
    # Step 8: Back-fill context parts that still have 0 tokens.
    #
    # _backfill_context_tokens returns 3 values: (updated_behaviors,
    # updated_root_parts, all_parts).  root_parts must be updated because
    # Foundation propagates every _pending_context entry from sub-behaviors up
    # to the root bundle when loaded with auto_include=False, so the context
    # TrackedParts land in root_parts rather than in any behavior's parts.
    # The old 2-value signature silently left root_parts unpatched; now we
    # assign the updated root_parts so total_tokens() reflects context files.
    # -------------------------------------------------------------------------
    behaviors, root_parts, all_parts = _backfill_context_tokens(
        behaviors, root_parts, include_order, composed
    )

    return ProvenanceMap(
        root_name=root_name,
        root_uri=source,
        root_instruction=root_instruction,
        root_instruction_tokens=root_instruction_tokens,
        behaviors=behaviors,
        root_parts=root_parts,
        all_parts=all_parts,
        composed_bundle=composed,
        include_order=include_order,
        session_config=session_config,
        spawn_config=spawn_config,
        _raw_bundles=accumulated_raw_bundles,
    )
