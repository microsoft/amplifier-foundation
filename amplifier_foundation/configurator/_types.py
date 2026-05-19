"""Foundation data types for item provenance and records.

These types form the contract between the foundation layer (provenance tracking)
and the app-cli layer (rendering).  The BundleInspector returns list[ItemRecord]
from all six *_list() methods.  The app-cli ItemRenderer consumes only ItemRecord.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal


@dataclass(frozen=True)
class Origin:
    """One claim on an item — a single edge in the behavior-merge graph.

    Attributes:
        bundle:       Bundle name that owns the claim (e.g. "foundation",
                      "behavior-python-dev", "mode:demo").
        via_behavior: The immediate parent in the merge graph.  None means the
                      item was self-introduced by *bundle* (not inherited from
                      another behavior).  When set, this is the name of the
                      intermediate bundle that carried the item into the
                      composition that ultimately exposed it to *bundle*.

    Examples:
        # Foundation introduced bash directly
        Origin(bundle="foundation", via_behavior=None)

        # behavior-dev inherited bash from foundation; carries it to root
        Origin(bundle="foundation", via_behavior="behavior-dev")

        # Mode contributed an agent at runtime
        Origin(bundle="mode:demo", via_behavior=None)
    """

    bundle: str
    via_behavior: str | None


@dataclass(frozen=True)
class IncludeStep:
    """One link in the bundle-on-disk include graph.

    Populated at query time by walking BundleRegistry._registry; not stored
    on Bundle directly.

    Attributes:
        bundle:  Bundle name at this step in the chain.
        version: Bundle version string, or None if not recorded.
        uri:     Source URI (git+https://..., file://...), or None if not known.
        is_root: True when this bundle is a topological root in the included_by
                 graph — i.e., it has no further ancestors (included_by is
                 empty or None).  These are the user-explicit entry points into
                 the composition (e.g., the active bundle set via ``bundle use``
                 or a behavior added via the ``app:`` list).  Rendered as ``*``
                 prefix in CLI output.  Part of the experimental JSON schema.
    """

    bundle: str
    version: str | None
    uri: str | None
    is_root: bool = False


# Type alias for the runtime_injection field — exported for reuse in inspector helpers.
RuntimeInjection = Literal["static", "mode", "hook", "skills", "mcp", "task"]


@dataclass(frozen=True)
class ItemRecord:
    """Foundation/app contract — the only thing the renderer consumes.

    All six BundleInspector.*_list() methods return list[ItemRecord].
    The JSON shape produced by dataclasses.asdict(record) is the public schema.

    Attributes:
        category:          One of the 12 category literals.
        name:              Display name for the item.
        enabled:           Whether the item is currently active.
        module_id:         Module identifier (e.g. "tool-bash"), or None.
        source_uri:        Source URI for the module, or None.
        config_summary:    Redacted configuration dict.
        origins:           Merge-graph chain (behaviors that contributed).
        include_paths:     Disk-graph chains (bundles on disk), one list per
                           distinct path, each ordered root→leaf.  Empty list
                           when no registry data is available; single-element
                           outer list when only one path exists.
        runtime_injection: How the item arrived at runtime, or None for static.
    """

    category: Literal[
        "provider",
        "tool",
        "hook",
        "agent",
        "context",
        "behavior",
        "session.orchestrator",
        "session.context",
        "spawn",
        "instruction",
        "skill",
        "mode",
    ]
    name: str
    enabled: bool
    module_id: str | None
    source_uri: str | None
    config_summary: dict[str, Any]
    origins: list[Origin]
    include_paths: list[list[IncludeStep]]
    runtime_injection: Literal["static", "mode", "hook", "skills", "mcp", "task"] | None
    explicitly_requested: bool = (
        False  # True when this bundle was the user's explicit entry point
    )


__all__ = ["Origin", "IncludeStep", "ItemRecord"]
