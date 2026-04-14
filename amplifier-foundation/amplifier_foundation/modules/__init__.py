"""Module resolution and activation for amplifier-foundation.

This module provides basic module resolution - downloading modules from URIs
and making them importable. This enables foundation to provide a turn-key
experience where bundles can be loaded and executed without additional libraries.

For advanced resolution strategies (layered resolution, settings-based overrides,
workspace conventions), see amplifier-module-resolution.
"""

from amplifier_foundation.modules.activator import ModuleActivationError
from amplifier_foundation.modules.activator import ModuleActivator

__all__ = ["ModuleActivator", "ModuleActivationError"]
