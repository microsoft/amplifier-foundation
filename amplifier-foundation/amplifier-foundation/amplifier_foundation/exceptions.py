"""Exception hierarchy for amplifier-foundation."""


class BundleError(Exception):
    """Base exception for all bundle-related errors."""


class BundleNotFoundError(BundleError):
    """Bundle could not be located at the specified source."""


class BundleLoadError(BundleError):
    """Bundle exists but could not be loaded (parse error, invalid format)."""


class BundleValidationError(BundleError):
    """Bundle loaded but validation failed (missing required fields, etc)."""


class BundleDependencyError(BundleError):
    """Bundle dependency could not be resolved (circular deps, missing deps)."""
