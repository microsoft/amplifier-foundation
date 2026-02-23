"""Amplifier Foundation - Bundle composition mechanism layer.

Foundation provides an ultra-thin mechanism layer for bundle composition
that sits between amplifier-core (kernel) and applications.

Core concept: Bundle = composable unit that produces mount plans.

One mechanism: `includes:` (declarative) + `compose()` (imperative)

Philosophy: Mechanism not policy, ruthless simplicity.

Note: This library is PURE MECHANISM. It loads bundles from URIs without
knowing about any specific bundle (including "foundation"). The foundation
bundle content co-located in this repo is just content - it's discovered
and loaded the same way any other bundle would be.
"""

from __future__ import annotations

# Core classes
from amplifier_foundation.bundle import Bundle

# Reference implementations
from amplifier_foundation.cache.disk import DiskCache

# Protocols
from amplifier_foundation.cache.protocol import CacheProviderProtocol
from amplifier_foundation.cache.simple import SimpleCache

# Dict utilities
from amplifier_foundation.dicts.merge import deep_merge
from amplifier_foundation.dicts.merge import merge_module_lists
from amplifier_foundation.dicts.navigation import get_nested
from amplifier_foundation.dicts.navigation import set_nested

# Exceptions
from amplifier_foundation.exceptions import BundleDependencyError
from amplifier_foundation.exceptions import BundleError
from amplifier_foundation.exceptions import BundleLoadError
from amplifier_foundation.exceptions import BundleNotFoundError
from amplifier_foundation.exceptions import BundleValidationError

# I/O utilities
from amplifier_foundation.io.files import read_with_retry
from amplifier_foundation.io.files import write_with_backup
from amplifier_foundation.io.files import write_with_retry
from amplifier_foundation.io.frontmatter import parse_frontmatter
from amplifier_foundation.io.yaml import read_yaml
from amplifier_foundation.io.yaml import write_yaml

# Mention utilities
from amplifier_foundation.mentions.deduplicator import ContentDeduplicator
from amplifier_foundation.mentions.loader import load_mentions
from amplifier_foundation.mentions.models import ContextFile
from amplifier_foundation.mentions.models import MentionResult
from amplifier_foundation.mentions.parser import parse_mentions
from amplifier_foundation.mentions.protocol import MentionResolverProtocol
from amplifier_foundation.mentions.resolver import BaseMentionResolver

# Path utilities
from amplifier_foundation.paths.construction import construct_agent_path
from amplifier_foundation.paths.construction import construct_context_path
from amplifier_foundation.paths.discovery import find_bundle_root
from amplifier_foundation.paths.discovery import find_files
from amplifier_foundation.paths.resolution import ParsedURI
from amplifier_foundation.paths.resolution import normalize_path
from amplifier_foundation.paths.resolution import parse_uri
from amplifier_foundation.registry import BundleRegistry
from amplifier_foundation.registry import BundleState
from amplifier_foundation.registry import UpdateInfo
from amplifier_foundation.registry import load_bundle

# Serialization utilities
from amplifier_foundation.serialization import sanitize_for_json
from amplifier_foundation.serialization import sanitize_message

# Spawn utilities
from amplifier_foundation.spawn_utils import ModelResolutionResult
from amplifier_foundation.spawn_utils import ProviderPreference
from amplifier_foundation.spawn_utils import apply_provider_preferences
from amplifier_foundation.spawn_utils import apply_provider_preferences_with_resolution
from amplifier_foundation.spawn_utils import is_glob_pattern
from amplifier_foundation.spawn_utils import resolve_model_pattern
from amplifier_foundation.sources.protocol import SourceHandlerProtocol
from amplifier_foundation.sources.protocol import SourceHandlerWithStatusProtocol
from amplifier_foundation.sources.protocol import SourceResolverProtocol
from amplifier_foundation.sources.protocol import SourceStatus
from amplifier_foundation.sources.resolver import SimpleSourceResolver

# Tracing utilities
from amplifier_foundation.tracing import generate_sub_session_id

# Session capability helpers (for modules to access session context)
from amplifier_foundation.session.capabilities import get_working_dir
from amplifier_foundation.session.capabilities import set_working_dir
from amplifier_foundation.session.capabilities import WORKING_DIR_CAPABILITY

# Updates - bundle update checking and updating
from amplifier_foundation.updates import BundleStatus
from amplifier_foundation.updates import check_bundle_status
from amplifier_foundation.updates import update_bundle
from amplifier_foundation.validator import BundleValidator
from amplifier_foundation.validator import ValidationResult
from amplifier_foundation.validator import validate_bundle
from amplifier_foundation.validator import validate_bundle_or_raise

__all__ = [
    # Core
    "Bundle",
    "BundleRegistry",
    "BundleState",
    "UpdateInfo",
    "BundleValidator",
    "ValidationResult",
    "load_bundle",
    "validate_bundle",
    "validate_bundle_or_raise",
    # Exceptions
    "BundleError",
    "BundleNotFoundError",
    "BundleLoadError",
    "BundleValidationError",
    "BundleDependencyError",
    # Protocols
    "MentionResolverProtocol",
    "SourceResolverProtocol",
    "SourceHandlerProtocol",
    "SourceHandlerWithStatusProtocol",
    "SourceStatus",
    "CacheProviderProtocol",
    # Updates
    "BundleStatus",
    "check_bundle_status",
    "update_bundle",
    # Reference implementations
    "BaseMentionResolver",
    "SimpleSourceResolver",
    "SimpleCache",
    "DiskCache",
    # Mentions
    "parse_mentions",
    "load_mentions",
    "ContentDeduplicator",
    "ContextFile",
    "MentionResult",
    # I/O
    "read_yaml",
    "write_yaml",
    "parse_frontmatter",
    "read_with_retry",
    "write_with_retry",
    "write_with_backup",
    # Serialization
    "sanitize_for_json",
    "sanitize_message",
    # Tracing
    "generate_sub_session_id",
    # Dicts
    "deep_merge",
    "merge_module_lists",
    "get_nested",
    "set_nested",
    # Paths
    "parse_uri",
    "ParsedURI",
    "normalize_path",
    "construct_agent_path",
    "construct_context_path",
    "find_files",
    "find_bundle_root",
    # Session capabilities
    "get_working_dir",
    "set_working_dir",
    "WORKING_DIR_CAPABILITY",
    # Spawn utilities
    "ProviderPreference",
    "ModelResolutionResult",
    "apply_provider_preferences",
    "apply_provider_preferences_with_resolution",
    "is_glob_pattern",
    "resolve_model_pattern",
]
