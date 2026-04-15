"""amplifier_configurator — provenance-aware bundle editing for Amplifier.

Built on Foundation's real Bundle API. Not a reimplementation.
"""

from __future__ import annotations

from .configurator import BundleConfigurator
from .dependencies import PART_DEPENDENCIES, REQUIRED_PARTS, validate_provenance
from .models import (
    BehaviorInfo,
    BundleDiff,
    ConfiguratorError,
    ConfiguratorWarning,
    DependencyError,
    LoadError,
    PartKind,
    ProvenanceMap,
    TrackedPart,
)
from .serialize import serialize_bundle
from .tokens import estimate_tokens_for_file, estimate_tokens_for_text

__all__ = [
    # Main class
    "BundleConfigurator",
    # Data models
    "BehaviorInfo",
    "BundleDiff",
    "PartKind",
    "ProvenanceMap",
    "TrackedPart",
    # Errors
    "ConfiguratorError",
    "ConfiguratorWarning",
    "DependencyError",
    "LoadError",
    # Functions
    "estimate_tokens_for_file",
    "estimate_tokens_for_text",
    "serialize_bundle",
    "validate_provenance",
    # Constants
    "PART_DEPENDENCIES",
    "REQUIRED_PARTS",
]
