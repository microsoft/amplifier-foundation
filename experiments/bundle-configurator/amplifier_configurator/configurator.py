"""Main BundleConfigurator class — wraps a ProvenanceMap for introspection and customization.

Provides an immutable-mutation API: all mutation methods return a NEW
BundleConfigurator instance rather than modifying the existing one.
"""

from __future__ import annotations

import asyncio
import dataclasses
from pathlib import Path

from amplifier_foundation.bundle import Bundle

from .models import (
    BehaviorInfo,
    BundleDiff,
    ConfiguratorError,
    DependencyError,
    PartKind,
    ProvenanceMap,
    TrackedPart,
)


class BundleConfigurator:
    """Wraps a ProvenanceMap with query and immutable-mutation API.

    All mutation methods (``remove_behavior``, ``remove_part``, ``add_behavior``)
    return a **new** ``BundleConfigurator`` instance — the original is never
    modified.
    """

    def __init__(self, provenance: ProvenanceMap) -> None:
        self._provenance = provenance

    # -------------------------------------------------------------------------
    # Properties
    # -------------------------------------------------------------------------

    @property
    def provenance(self) -> ProvenanceMap:
        """The underlying ProvenanceMap."""
        return self._provenance

    @property
    def bundle(self) -> Bundle:
        """The composed Foundation Bundle as loaded by Foundation.

        .. warning::
            This reflects the **original** Foundation-loaded state and is **not**
            updated when parts or behaviors are removed via mutation methods
            (``remove_behavior``, ``remove_part``, ``add_behavior``).

            Use :meth:`list_parts` / :meth:`list_behaviors` to inspect current
            state, and :meth:`save` to serialize the current mutated state.
        """
        return self._provenance.composed_bundle

    # -------------------------------------------------------------------------
    # Factory methods
    # -------------------------------------------------------------------------

    @classmethod
    async def load(cls, source: str) -> "BundleConfigurator":
        """Load a bundle source and build a full ProvenanceMap.

        Parameters
        ----------
        source:
            Any Foundation-supported source: bundle name, git URI, or file path.

        Returns
        -------
        BundleConfigurator
            Wraps the built ProvenanceMap.
        """
        from .provenance import build_provenance_map

        pmap = await build_provenance_map(source)
        return cls(pmap)

    @classmethod
    def load_sync(cls, source: str) -> "BundleConfigurator":
        """Synchronous wrapper around :meth:`load` using ``asyncio.run``."""
        return asyncio.run(cls.load(source))

    # -------------------------------------------------------------------------
    # Query methods
    # -------------------------------------------------------------------------

    def list_behaviors(self) -> list[BehaviorInfo]:
        """Return all behaviors sorted by include_order (leaves first).

        The include_order recorded in the ProvenanceMap is bottom-up (deepest
        leaves first, shallowest last), so this order reflects dependency
        depth.

        Returns
        -------
        list[BehaviorInfo]
            Behaviors in include_order sequence.
        """
        return [
            self._provenance.behaviors[uri]
            for uri in self._provenance.include_order
            if uri in self._provenance.behaviors
        ]

    def list_parts(self, kind: PartKind | None = None) -> list[TrackedPart]:
        """Return all tracked parts, optionally filtered by kind.

        Parameters
        ----------
        kind:
            If given, only parts of this ``PartKind`` are returned.

        Returns
        -------
        list[TrackedPart]
            All (or filtered) parts from ``ProvenanceMap.all_parts``.
        """
        parts: list[TrackedPart] = list(self._provenance.all_parts)
        if kind is not None:
            parts = [p for p in parts if p.kind == kind]
        return parts

    def total_tokens(self) -> int:
        """Total estimated token cost.

        Returns
        -------
        int
            Sum of all part tokens across ``all_parts`` **plus**
            ``root_instruction_tokens``.
        """
        return (
            sum(p.tokens for p in self._provenance.all_parts)
            + self._provenance.root_instruction_tokens
        )

    def tokens_by_behavior(self) -> dict[str, int]:
        """Token cost breakdown sorted descending.

        The returned dict maps behavior **name** → token count.  A special
        ``"<root>"`` key holds the root instruction token count.

        Returns
        -------
        dict[str, int]
            Mapping sorted in descending order of token count.
        """
        costs: dict[str, int] = {}
        for beh in self._provenance.behaviors.values():
            costs[beh.name] = beh.total_tokens
        # Root cost = instruction tokens + root-won parts in all_parts.
        # Root's _pending_context is an aggregate of all sub-behavior context;
        # those files are attributed to the sub-behaviors that declared them
        # (via setdefault in _deduplicate_parts). Only parts that no sub-behavior
        # claimed appear in all_parts with source_behavior=None.
        root_won_tokens = sum(
            p.tokens for p in self._provenance.all_parts if p.source_behavior is None
        )
        costs["<root>"] = self._provenance.root_instruction_tokens + root_won_tokens
        # Return sorted by value descending; Python 3.7+ dicts preserve insertion order.
        return dict(sorted(costs.items(), key=lambda item: item[1], reverse=True))

    def get_behavior(self, name: str) -> BehaviorInfo:
        """Return the BehaviorInfo with the given short name.

        Parameters
        ----------
        name:
            Short behavior name (e.g. ``"bug-hunter"``), **not** its URI.

        Returns
        -------
        BehaviorInfo

        Raises
        ------
        KeyError
            If no behavior with that name exists in this configurator.
        """
        for beh in self._provenance.behaviors.values():
            if beh.name == name:
                return beh
        raise KeyError(name)

    def get_part(self, kind: PartKind, name: str) -> TrackedPart:
        """Return the TrackedPart identified by (kind, name).

        Parameters
        ----------
        kind:
            The :class:`PartKind` of the part.
        name:
            The part name (e.g. ``"tool-bash"`` or
            ``"leaf-behavior:lsp-config"``).

        Returns
        -------
        TrackedPart

        Raises
        ------
        KeyError
            If no matching part exists.
        """
        for part in self._provenance.all_parts:
            if part.kind == kind and part.name == name:
                return part
        raise KeyError((kind, name))

    # -------------------------------------------------------------------------
    # Mutation methods (immutable — return NEW instance)
    # -------------------------------------------------------------------------

    def remove_behavior(self, name: str) -> "BundleConfigurator":
        """Remove a behavior by short name.

        Returns a new ``BundleConfigurator`` with the behavior and all parts
        exclusively contributed by it removed.  Any behaviors that become
        orphaned (their direct parent in the include chain was removed) are
        also removed recursively.

        Parameters
        ----------
        name:
            Short behavior name to remove.

        Raises
        ------
        KeyError
            If no behavior with that name exists.
        """
        # Find the URI of the behavior to remove.
        uri_to_remove: str | None = None
        for uri, beh in self._provenance.behaviors.items():
            if beh.name == name:
                uri_to_remove = uri
                break
        if uri_to_remove is None:
            raise KeyError(name)

        behavior_name = self._provenance.behaviors[uri_to_remove].name

        # Collect all behavior names to remove, including orphaned ones.
        # A behavior is orphaned when its direct parent in include_chain
        # (include_chain[-2]) is among the removed names.
        names_removed: set[str] = {behavior_name}
        changed = True
        while changed:
            changed = False
            for beh in self._provenance.behaviors.values():
                if beh.name in names_removed:
                    continue
                # Direct parent = element immediately before this behavior.
                if (
                    len(beh.include_chain) >= 2
                    and beh.include_chain[-2] in names_removed
                ):
                    names_removed.add(beh.name)
                    changed = True

        # Build updated data structures.
        new_behaviors = {
            k: v
            for k, v in self._provenance.behaviors.items()
            if v.name not in names_removed
        }
        uris_to_remove = {
            uri
            for uri, beh in self._provenance.behaviors.items()
            if beh.name in names_removed
        }
        new_include_order = tuple(
            u for u in self._provenance.include_order if u not in uris_to_remove
        )
        # Remove parts exclusively from removed behaviors.
        new_all_parts = tuple(
            p
            for p in self._provenance.all_parts
            if p.source_behavior not in names_removed
        )
        return self._recompose(
            behaviors=new_behaviors,
            include_order=new_include_order,
            all_parts=new_all_parts,
        )

    def remove_part(self, kind: PartKind, name: str) -> "BundleConfigurator":
        """Remove a part by (kind, name) from all parts and behavior records.

        Returns a new ``BundleConfigurator``.

        Parameters
        ----------
        kind:
            The :class:`PartKind` of the part to remove.
        name:
            The part name (e.g. ``\"tool-lsp\"``).

        Raises
        ------
        KeyError
            If no matching part exists.
        DependencyError
            If the part is in the set of required parts that must always be
            present (e.g. ``tool-bash``, ``tool-filesystem``, ``tool-search``).
        """
        from .dependencies import REQUIRED

        # Guard: part must exist.
        found = any(
            p.kind == kind and p.name == name for p in self._provenance.all_parts
        )
        if not found:
            raise KeyError((kind, name))

        # Guard: required parts cannot be removed.
        if name in REQUIRED:
            raise DependencyError([name])

        new_all_parts = tuple(
            p
            for p in self._provenance.all_parts
            if not (p.kind == kind and p.name == name)
        )
        new_root_parts = tuple(
            p
            for p in self._provenance.root_parts
            if not (p.kind == kind and p.name == name)
        )
        new_behaviors: dict[str, BehaviorInfo] = {}
        for uri, beh in self._provenance.behaviors.items():
            new_parts = tuple(
                p for p in beh.parts if not (p.kind == kind and p.name == name)
            )
            new_raw = tuple(
                p for p in beh.raw_parts if not (p.kind == kind and p.name == name)
            )
            new_behaviors[uri] = dataclasses.replace(
                beh,
                parts=new_parts,
                raw_parts=new_raw,
                total_tokens=sum(p.tokens for p in new_parts),
            )
        return self._recompose(
            behaviors=new_behaviors,
            all_parts=new_all_parts,
            root_parts=new_root_parts,
        )

    async def add_behavior(self, uri: str) -> "BundleConfigurator":
        """Add a new behavior by URI.

        Loads the behavior tree rooted at ``uri``, merges it into the current
        provenance map using the same bottom-up, last-write-wins rules as
        :func:`build_provenance_map`, and returns a new
        ``BundleConfigurator``.

        Parameters
        ----------
        uri:
            Foundation-supported URI for the behavior bundle.
        """
        from .provenance import _load_behavior_tree, _deduplicate_parts
        from amplifier_foundation.registry import BundleRegistry

        registry = BundleRegistry()
        new_behavior_infos, new_raw_bundles = await _load_behavior_tree(
            uri=uri,
            depth=0,
            chain=(self._provenance.root_name,),
            registry=registry,
            seen=set(),
        )

        # Merge new behaviors (de-duplicate URIs; new wins).
        merged_behaviors = dict(self._provenance.behaviors)
        merged_include_order = list(self._provenance.include_order)
        for beh in new_behavior_infos:
            if beh.uri not in merged_behaviors:
                merged_behaviors[beh.uri] = beh
                merged_include_order.append(beh.uri)
            else:
                merged_behaviors[beh.uri] = beh  # new wins

        new_include_order = tuple(merged_include_order)

        # Re-deduplicate all parts with the updated behaviors.
        all_parts, updated_behaviors = _deduplicate_parts(
            merged_behaviors,
            self._provenance.root_parts,
            new_include_order,
        )

        merged_raw_bundles = dict(self._provenance._raw_bundles)
        merged_raw_bundles.update(new_raw_bundles)

        return self._recompose(
            behaviors=updated_behaviors,
            include_order=new_include_order,
            all_parts=all_parts,
            _raw_bundles=merged_raw_bundles,
        )

    # -------------------------------------------------------------------------
    # Diff, validate, compose, save
    # -------------------------------------------------------------------------

    def diff(self, other: "BundleConfigurator") -> BundleDiff:
        """Compute the diff between this configurator (before) and *other* (after).

        Parameters
        ----------
        other:
            The "after" configurator to compare against.

        Returns
        -------
        BundleDiff
        """
        self_keys = {(p.kind, p.name) for p in self._provenance.all_parts}
        other_keys = {(p.kind, p.name) for p in other._provenance.all_parts}

        added_keys = other_keys - self_keys
        removed_keys = self_keys - other_keys

        added_parts = tuple(
            p for p in other._provenance.all_parts if (p.kind, p.name) in added_keys
        )
        removed_parts = tuple(
            p for p in self._provenance.all_parts if (p.kind, p.name) in removed_keys
        )

        self_uris = set(self._provenance.behaviors.keys())
        other_uris = set(other._provenance.behaviors.keys())

        added_behaviors = tuple(other_uris - self_uris)
        removed_behaviors = tuple(self_uris - other_uris)

        before_tokens = self.total_tokens()
        after_tokens = other.total_tokens()

        return BundleDiff(
            added_parts=added_parts,
            removed_parts=removed_parts,
            added_behaviors=added_behaviors,
            removed_behaviors=removed_behaviors,
            token_delta=after_tokens - before_tokens,
            before_tokens=before_tokens,
            after_tokens=after_tokens,
        )

    def validate(self) -> tuple[list[str], list[str]]:
        """Run bundle validation and return ``(errors, warnings)``.

        Uses provenance-aware validation via
        :func:`~amplifier_configurator.dependencies.validate_provenance`.

        Returns
        -------
        tuple[list[str], list[str]]
            ``(errors, warnings)`` where *errors* are hard failures (e.g.
            missing required parts) and *warnings* are soft issues (e.g.
            missing optional dependencies or agents without tool-delegate).
        """
        from .dependencies import validate_provenance

        return validate_provenance(self._provenance)

    def compose(self) -> Bundle:
        """Return the composed Foundation Bundle.

        This is the same object accessible via :attr:`bundle`.

        .. warning::
            Like :attr:`bundle`, this reflects the **original** Foundation-loaded
            state and is **not** updated by mutation methods.  Use
            :meth:`list_parts` / :meth:`list_behaviors` to inspect current state,
            and :meth:`save` to serialize the current mutated state.
        """
        return self.bundle

    def save(self, path: str | Path) -> Path:
        """Save the current composed bundle as a .md file.

        Calls :meth:`validate` first; raises :class:`~amplifier_configurator.models.ConfiguratorError`
        if any validation errors are found.  Uses :func:`~amplifier_configurator.serialize.serialize_bundle`
        to generate the YAML frontmatter + markdown body content.

        Parameters
        ----------
        path:
            Destination file path (parent directories are created if needed).

        Returns
        -------
        Path
            The path to the written file.

        Raises
        ------
        ConfiguratorError
            If validation finds hard errors (e.g. missing required parts).
        """
        from .serialize import serialize_bundle

        errors, warnings = self.validate()
        if errors:
            raise ConfiguratorError(f"Bundle has validation errors: {errors!r}")

        dest = Path(path)
        dest.parent.mkdir(parents=True, exist_ok=True)
        content = serialize_bundle(
            self._provenance, warnings=warnings if warnings else None
        )
        dest.write_text(content)
        return dest

    # -------------------------------------------------------------------------
    # Private helpers
    # -------------------------------------------------------------------------

    def _recompose(
        self,
        *,
        behaviors: dict[str, BehaviorInfo] | None = None,
        include_order: tuple[str, ...] | None = None,
        all_parts: tuple[TrackedPart, ...] | None = None,
        root_parts: tuple[TrackedPart, ...] | None = None,
        _raw_bundles: dict[str, Bundle] | None = None,
    ) -> "BundleConfigurator":
        """Create a new BundleConfigurator with selectively replaced provenance fields.

        Parameters
        ----------
        behaviors:
            Replacement behaviors dict.  Uses existing value if ``None``.
        include_order:
            Replacement include_order tuple.  Uses existing value if ``None``.
        all_parts:
            Replacement all_parts tuple.  Uses existing value if ``None``.
        root_parts:
            Replacement root_parts tuple.  Uses existing value if ``None``.
        _raw_bundles:
            Replacement raw bundles mapping.  Uses existing value if ``None``.

        Returns
        -------
        BundleConfigurator
            New instance wrapping the updated ProvenanceMap.
        """
        p = self._provenance
        new_pmap = dataclasses.replace(
            p,
            behaviors=behaviors if behaviors is not None else p.behaviors,
            include_order=include_order
            if include_order is not None
            else p.include_order,
            all_parts=all_parts if all_parts is not None else p.all_parts,
            root_parts=root_parts if root_parts is not None else p.root_parts,
            _raw_bundles=_raw_bundles if _raw_bundles is not None else p._raw_bundles,
        )
        return BundleConfigurator(new_pmap)
