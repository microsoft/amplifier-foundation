"""Dictionary utilities for deep merging and navigation."""

from .merge import deep_merge
from .merge import merge_module_lists
from .navigation import get_nested
from .navigation import set_nested

__all__ = [
    "deep_merge",
    "merge_module_lists",
    "get_nested",
    "set_nested",
]
