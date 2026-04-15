"""Domain models for amplifier_configurator — error hierarchy and part kind enum."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from amplifier_foundation.bundle import Bundle


class PartKind(str, Enum):
    """Kind of bundle part.

    Values are plain strings so they serialize cleanly to JSON without a
    custom encoder.  ``isinstance(PartKind.TOOL, str)`` is True.
    """

    TOOL = "tool"
    HOOK = "hook"
    PROVIDER = "provider"
    AGENT = "agent"
    CONTEXT = "context"


# ---------------------------------------------------------------------------
# Error hierarchy
# ---------------------------------------------------------------------------


class ConfiguratorError(Exception):
    """Base exception for all amplifier_configurator errors."""


class DependencyError(ConfiguratorError):
    """Raised when a bundle has unresolvable dependency relationships.

    Parameters
    ----------
    parts:
        List of part identifiers involved in the dependency problem.
    """

    def __init__(self, parts: list[str]) -> None:
        self.parts = parts
        super().__init__(f"Dependency error involving parts: {parts!r}")


class LoadError(ConfiguratorError):
    """Raised when a bundle cannot be loaded from a source.

    Parameters
    ----------
    source:
        Human-readable description of where the load was attempted (e.g. a
        file path or URL).
    cause:
        The underlying exception that triggered the failure, if any.
    """

    def __init__(self, source: str, cause: Exception | None = None) -> None:
        self.source = source
        self.cause = cause
        msg = f"Failed to load bundle from {source!r}"
        if cause is not None:
            msg += f": {cause}"
        super().__init__(msg)


class ConfiguratorWarning(UserWarning):
    """Warning category for non-fatal configurator issues."""


# ---------------------------------------------------------------------------
# Tracked-part and behavior dataclasses
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class TrackedPart:
    """A single part tracked within a behavior's composition.

    Attributes
    ----------
    kind:
        The type of part (tool, hook, provider, agent, or context).
    name:
        Module name of the part (e.g. ``"tool-bash"``).
    source_behavior:
        The behavior (agent URI) that directly contributed this part, or
        ``None`` for parts present at the root bundle level.
    tokens:
        Estimated token cost for this part.
    config:
        Raw configuration dict from the bundle definition.
    namespace_path:
        For ``CONTEXT`` parts loaded via a namespace (e.g.
        ``"foundation:instructions"``); ``None`` for non-namespaced parts.
    """

    kind: PartKind
    name: str
    source_behavior: str | None
    tokens: int
    config: dict[str, Any]
    namespace_path: str | None

    def __hash__(self) -> int:
        # ``config`` is a plain dict and therefore not hashable.  We hash only
        # the remaining scalar fields.  Two instances are equal (via the
        # dataclass-generated __eq__ which includes *all* fields) only when
        # every field matches, so excluding ``config`` from the hash is safe —
        # it may increase collisions but never produces incorrect equality.
        return hash(
            (
                self.kind,
                self.name,
                self.source_behavior,
                self.tokens,
                self.namespace_path,
            )
        )


@dataclass(frozen=True)
class BehaviorInfo:
    """Aggregated information about a resolved agent behavior.

    Attributes
    ----------
    name:
        Short name of the agent (e.g. ``"bug-hunter"``).
    uri:
        Fully-qualified agent URI (e.g. ``"foundation:bug-hunter"``).
    parts:
        De-duplicated tuple of ``TrackedPart`` instances contributing to this
        behavior (i.e. parts visible after de-duplication across the include
        chain).
    raw_parts:
        All ``TrackedPart`` instances before de-duplication, preserving the
        order and multiplicity in which they appear in the include chain.
    total_tokens:
        Sum of ``tokens`` across ``parts`` (the de-duplicated set).
    depth:
        Nesting depth in the include chain (0 for root-level agents).
    include_chain:
        Ordered sequence of behavior URIs from the root to this behavior,
        capturing how it was reached.
    instruction:
        Agent instruction string, or ``None`` if not specified.
    """

    name: str
    uri: str
    parts: tuple[TrackedPart, ...]
    raw_parts: tuple[TrackedPart, ...]
    total_tokens: int
    depth: int
    include_chain: tuple[str, ...]
    instruction: str | None = None


# ---------------------------------------------------------------------------
# ProvenanceMap and BundleDiff dataclasses
# ---------------------------------------------------------------------------


@dataclass
class ProvenanceMap:
    """Full provenance record for a composed bundle.

    This dataclass is intentionally *mutable* — mutation methods rebuild it
    internally.  Do **not** freeze it.

    Attributes
    ----------
    root_name:
        Short name of the root bundle.
    root_uri:
        Fully-qualified URI of the root bundle.
    root_instruction:
        Root-level agent instruction string, or ``None`` if absent.
    root_instruction_tokens:
        Estimated token cost of ``root_instruction``.
    behaviors:
        Mapping from behavior URI to ``BehaviorInfo`` for each agent included
        in the composition.
    root_parts:
        Parts present at the root bundle level (before any behavior includes).
    all_parts:
        Union of ``root_parts`` and all parts contributed by every behavior in
        the composition (de-duplicated, ordered).
    composed_bundle:
        The final ``Bundle`` produced by the composition.
    include_order:
        Ordered tuple of behavior URIs reflecting the inclusion sequence.
    session_config:
        Session-level configuration dict.
    spawn_config:
        Spawn-level configuration dict, or ``None`` when not present.
    _raw_bundles:
        Internal mapping from bundle name to the raw ``Bundle`` object used
        during composition.
    """

    root_name: str
    root_uri: str
    root_instruction: str | None
    root_instruction_tokens: int
    behaviors: dict[str, BehaviorInfo]
    root_parts: tuple[TrackedPart, ...]
    all_parts: tuple[TrackedPart, ...]
    composed_bundle: Bundle
    include_order: tuple[str, ...]
    session_config: dict[str, Any]
    spawn_config: dict[str, Any] | None
    _raw_bundles: dict[str, Bundle]


@dataclass(frozen=True)
class BundleDiff:
    """Immutable record of the delta between two bundle compositions.

    Attributes
    ----------
    added_parts:
        Parts present in the *after* bundle that are absent from *before*.
    removed_parts:
        Parts present in the *before* bundle that are absent from *after*.
    added_behaviors:
        Behavior URIs introduced in the *after* bundle.
    removed_behaviors:
        Behavior URIs removed in the *after* bundle.
    token_delta:
        Net change in token count (positive = more tokens, negative = fewer).
    before_tokens:
        Total token count of the *before* bundle composition.
    after_tokens:
        Total token count of the *after* bundle composition.
    """

    added_parts: tuple[TrackedPart, ...]
    removed_parts: tuple[TrackedPart, ...]
    added_behaviors: tuple[str, ...]
    removed_behaviors: tuple[str, ...]
    token_delta: int
    before_tokens: int
    after_tokens: int
