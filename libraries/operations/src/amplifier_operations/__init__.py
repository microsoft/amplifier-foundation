"""Portable durable operation observations; no application or execution imports.

An operation journal records evidence. It never starts, restores, retries, or
cancels the underlying side effect. Applications bind identity and controls.
"""

from .journal import OperationJournal

__all__ = ["OperationJournal"]
