"""Cooperating updates to native session metadata, independent of UI state.

The short metadata lock serializes metadata writers, not session execution.
Hosts still hold execution ownership when changing transcripts or running tools.
All paths are supplied by the host; reads never migrate or repair files.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

NAMING_FIELDS = (
    "name",
    "name_source",
    "name_revision",
    "name_updated_at",
    "name_generated_at",
    "description",
    "description_updated_at",
)


@contextmanager
def metadata_lock(session_dir: Path) -> Iterator[None]:
    # Import only on writes: filelock can probe filesystem capabilities.
    from filelock import FileLock

    # Sibling lock survives deletion of the session directory.
    session_dir.parent.mkdir(parents=True, exist_ok=True)
    with FileLock(
        session_dir.parent / f".{session_dir.name}.metadata.lock", timeout=10
    ):
        yield


def checkpoint_metadata(current: dict, incoming: dict) -> dict:
    """Preserve unknown fields and newer naming while saving runtime state."""
    merged = {**current, **incoming}
    for key in NAMING_FIELDS:
        if key in current:
            merged[key] = current[key]
    return merged


def has_generated_or_manual_name(metadata: dict) -> bool:
    """Legacy names are intentional; a fallback may still be auto-named."""
    return bool(metadata.get("name")) and metadata.get("name_source") != "fallback"


class SessionMetadataStore:
    """A field-oriented writer for the existing metadata.json authority.

    Older writers that bypass this API's lock cannot be made concurrent-safe.
    No second name file, event replay, provider call, or execution lease is used.
    """

    def __init__(self, session_dir: str | Path, *, create: bool = False):
        from .history import SessionHistoryStore

        self.history = SessionHistoryStore(session_dir)
        self.create = create

    def read(self) -> dict[str, Any]:
        return self.history.load_metadata()

    def update(self, fields: dict[str, Any]) -> dict[str, Any]:
        """Reread under the lock and patch only the supplied fields."""
        with metadata_lock(self.history.session_dir):
            if not self.create and not self.history.session_dir.is_dir():
                raise FileNotFoundError("The native session no longer exists")
            current = self.read()
            result = {**current, **fields}
            self.history._save_metadata_unlocked(result)
            return result

    def set_name(
        self,
        name: str,
        *,
        source: str = "manual",
        description: str | None = None,
        expected_revision: int | None = None,
        only_if_missing: bool = False,
    ) -> dict[str, Any]:
        """Set an explicit name, or conditionally accept a generated/fallback name.

        Generated results never replace an explicit or unclassified legacy name.
        Hosts can pass the revision observed before asynchronous generation.
        Migration uses only_if_missing; conflicting legacy evidence stays intact.
        """
        if not isinstance(name, str) or not name.strip() or len(name.strip()) > 200:
            raise ValueError("Session names must contain 1 to 200 characters")
        if source not in {"manual", "generated", "fallback"}:
            raise ValueError("Unknown session name source")
        if description is not None and not isinstance(description, str):
            raise ValueError("Session description must be a string")
        name = name.strip()
        with metadata_lock(self.history.session_dir):
            if not self.create and not self.history.session_dir.is_dir():
                raise FileNotFoundError("The native session no longer exists")
            current = self.read()
            result = dict(current)
            revision = current.get("name_revision", 0)
            revision = revision if type(revision) is int and revision >= 0 else 0
            has_name = bool(current.get("name"))
            allowed = not (only_if_missing and has_name)
            if (
                source == "fallback"
                and has_name
                and current.get("name_source") != "fallback"
            ):
                allowed = False
            if source == "generated":
                allowed = allowed and (
                    not has_name
                    or current.get("name_source") in {"fallback", "generated"}
                )
                if expected_revision is not None and expected_revision != revision:
                    allowed = False
            now = datetime.now(UTC).isoformat()
            if allowed and (current.get("name"), current.get("name_source")) != (
                name,
                source,
            ):
                result.update(
                    name=name,
                    name_source=source,
                    name_revision=revision + 1,
                    name_updated_at=now,
                )
                if source == "generated":
                    result["name_generated_at"] = now
            if (
                description is not None
                and not (only_if_missing and current.get("description"))
                and description != current.get("description")
                and (expected_revision is None or expected_revision == revision)
            ):
                result.update(description=description, description_updated_at=now)
            if result != current:
                self.history._save_metadata_unlocked(result)
            return result
