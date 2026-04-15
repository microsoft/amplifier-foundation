"""Dependency map and validation for bundle parts (provenance-aware).

New API (provenance-aware):
  - PART_DEPENDENCIES, AGENT_REQUIRES_DELEGATE, REQUIRED_PARTS, INFRASTRUCTURE
  - _get_all_part_names(pmap: ProvenanceMap) -> set[str]
  - validate_provenance(pmap: ProvenanceMap) -> tuple[list[str], list[str]]

Legacy API (Bundle-based, kept for backward compatibility):
  - DEPENDENCIES (alias for PART_DEPENDENCIES)
  - REQUIRED (alias for REQUIRED_PARTS)
  - get_all_part_names(bundle: Bundle) -> set[str]
  - check_dependencies(bundle: Bundle) -> list[str]
  - check_required(bundle: Bundle) -> list[str]
  - validate(bundle: Bundle) -> tuple[list[str], list[str]]
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from amplifier_foundation.bundle import Bundle

if TYPE_CHECKING:
    from .models import ProvenanceMap

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Known dependency relationships: part name -> [deps it needs]
PART_DEPENDENCIES: dict[str, list[str]] = {
    # hooks -> tools they need
    "hooks-todo-reminder": ["tool-todo"],
    "hooks-todo-display": ["tool-todo"],
    # tools -> context files they need
    "tool-lsp": ["context:python-lsp", "context:lsp-general"],
    "tool-skills": ["context:skills-instructions"],
    "tool-recipes": ["context:recipe-awareness"],
    "tool-modes": ["context:modes-instructions"],
    # python check -> context
    "python_check": ["context:python-dev-instructions"],
}

# Whether agents always require tool-delegate
AGENT_REQUIRES_DELEGATE: bool = True

# Parts that must always be present in every bundle
REQUIRED_PARTS: set[str] = {
    "tool-bash",
    "tool-filesystem",
    "tool-search",
}

# Infrastructure parts (not subject to standard validation rules)
INFRASTRUCTURE: set[str] = {"session"}

# ---------------------------------------------------------------------------
# Backward-compat aliases (legacy names)
# ---------------------------------------------------------------------------

DEPENDENCIES = PART_DEPENDENCIES
REQUIRED = REQUIRED_PARTS


# ---------------------------------------------------------------------------
# Provenance-aware API (new)
# ---------------------------------------------------------------------------


def _get_all_part_names(pmap: ProvenanceMap) -> set[str]:
    """Return a set of all part identifiers from a ProvenanceMap.

    Context parts use ``'context:{short_name}'`` format where *short_name*
    is the last segment after splitting the TrackedPart name on ``':'``.
    All other part kinds use their name as-is.

    This normalisation lets callers compare against ``PART_DEPENDENCIES``
    values which also use the ``'context:X'`` format.
    """
    from .models import PartKind

    names: set[str] = set()
    for part in pmap.all_parts:
        if part.kind == PartKind.CONTEXT:
            short_name = part.name.split(":")[-1]
            names.add(f"context:{short_name}")
        else:
            names.add(part.name)
    return names


def validate_provenance(pmap: ProvenanceMap) -> tuple[list[str], list[str]]:
    """Full provenance-aware validation.

    Parameters
    ----------
    pmap:
        The :class:`ProvenanceMap` to validate.

    Returns
    -------
    tuple[list[str], list[str]]
        ``(errors, warnings)`` where:

        * **errors** — missing required parts (hard failures).
        * **warnings** — missing declared dependencies or agents without
          ``tool-delegate`` (soft failures).
    """
    from .models import PartKind

    names = _get_all_part_names(pmap)
    errors: list[str] = []
    warnings: list[str] = []

    # --- Required parts (errors) -------------------------------------------
    for req in REQUIRED_PARTS:
        if req not in names:
            errors.append(f"Required part missing: {req}")

    # --- Part dependencies (warnings) --------------------------------------
    for part_name, deps in PART_DEPENDENCIES.items():
        if part_name in names:
            for dep in deps:
                if dep not in names:
                    warnings.append(f"{part_name} requires {dep} but it's missing")

    # --- Agents need tool-delegate (warning) --------------------------------
    if AGENT_REQUIRES_DELEGATE:
        has_agents = any(part.kind == PartKind.AGENT for part in pmap.all_parts)
        if has_agents and "tool-delegate" not in names:
            warnings.append("Agents require tool-delegate but it's not in the bundle")

    return errors, warnings


# ---------------------------------------------------------------------------
# Bundle-based API (legacy — kept for backward compatibility)
# ---------------------------------------------------------------------------


def get_all_part_names(bundle: Bundle) -> set[str]:
    """Return a set of all part identifiers present in a ``Bundle``.

    .. deprecated::
        Use :func:`_get_all_part_names` with a :class:`ProvenanceMap` instead.
    """
    names: set[str] = set()
    for t in bundle.tools:
        names.add(t.get("module", t.get("id", "")))
    for h in bundle.hooks:
        names.add(h.get("module", h.get("id", "")))
    for name in bundle.agents:
        names.add(f"agent:{name}")
    for name in bundle.context:
        names.add(f"context:{name}")
    for p in bundle.providers:
        names.add(p.get("module", p.get("id", "")))
    return names


def check_dependencies(bundle: Bundle) -> list[str]:
    """Check for missing declared dependencies.  Returns warning strings."""
    names = get_all_part_names(bundle)
    warnings: list[str] = []

    for part_name, deps in PART_DEPENDENCIES.items():
        if part_name in names:
            for dep in deps:
                if dep not in names:
                    warnings.append(f"{part_name} requires {dep} but it's missing")

    # Agents need tool-delegate to be spawned
    if bundle.agents and not any(
        t.get("module") == "tool-delegate" for t in bundle.tools
    ):
        warnings.append("Agents require tool-delegate but it's not in the bundle")

    return warnings


def check_required(bundle: Bundle) -> list[str]:
    """Check that required parts are present.  Returns error strings."""
    names = get_all_part_names(bundle)
    errors: list[str] = []
    for req in REQUIRED_PARTS:
        if req not in names:
            errors.append(f"Required part missing: {req}")
    return errors


def validate(bundle: Bundle) -> tuple[list[str], list[str]]:
    """Full Bundle-based validation.  Returns ``(errors, warnings)``."""
    return check_required(bundle), check_dependencies(bundle)
