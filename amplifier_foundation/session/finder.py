"""Layer Level 3 (Session Finder) — session discovery by path, ID, or partial ID.

This module builds on store.py (Level 2) and diagnosis.py (Level 1) to provide
session discovery and resolution. It knows the `~/.amplifier/projects/` path
convention.

Directory structure:
    sessions_root/<project>/sessions/<session_id>/
        transcript.jsonl
        metadata.json
        events.jsonl  (optional)
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

from .diagnosis import diagnose_transcript
from .messages import count_turns
from .store import (
    METADATA_FILENAME,
    TRANSCRIPT_FILENAME,
    load_metadata,
    load_transcript_with_lines,
)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

DEFAULT_SESSIONS_ROOT: Path = Path.home() / ".amplifier" / "projects"


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------


def _iter_project_dirs(root: Path, project: str | None) -> Iterator[tuple[str, Path]]:
    """Yield (project_name, sessions_subdir) tuples.

    Searches root for project directories. If ``project`` is provided, only
    that single project directory is yielded (if it exists and contains a
    ``sessions/`` subdirectory). If ``project`` is None, all project
    directories in root are yielded.

    Args:
        root: The sessions root directory (DEFAULT_SESSIONS_ROOT).
        project: Optional project name to narrow scope.

    Yields:
        Tuples of (project_name, sessions_dir) where sessions_dir is the
        ``sessions/`` subdirectory within the project directory.
    """
    if not root.is_dir():
        return

    if project is not None:
        proj_dir = root / project
        sessions_dir = proj_dir / "sessions"
        if sessions_dir.is_dir():
            yield project, sessions_dir
        return

    for entry in sorted(root.iterdir()):
        if not entry.is_dir():
            continue
        sessions_dir = entry / "sessions"
        if sessions_dir.is_dir():
            yield entry.name, sessions_dir


def _transcript_contains_keyword(session_dir: Path, keyword: str) -> bool:
    """Return True if transcript.jsonl contains keyword (case-insensitive).

    Searches only transcript.jsonl — never events.jsonl.

    Args:
        session_dir: Path to the session directory.
        keyword: Search term (case-insensitive).

    Returns:
        True if keyword found anywhere in the transcript text.
    """
    transcript_path = session_dir / TRANSCRIPT_FILENAME
    if not transcript_path.exists():
        return False
    keyword_lower = keyword.lower()
    with transcript_path.open(encoding="utf-8") as fh:
        for line in fh:
            if keyword_lower in line.lower():
                return True
    return False


def _is_valid_session_dir(session_dir: Path) -> bool:
    """Return True if session_dir has a metadata.json."""
    return (session_dir / METADATA_FILENAME).exists()


def _parse_created(created_str: str | None) -> datetime | None:
    """Parse an ISO-format created string into a timezone-aware datetime."""
    if not created_str:
        return None
    try:
        dt = datetime.fromisoformat(created_str)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except (ValueError, TypeError):
        return None


def _parse_date_filter(date_str: str) -> datetime:
    """Parse a date filter string (ISO format) into a timezone-aware datetime.

    If the string has no time component, midnight UTC is assumed.
    """
    dt = datetime.fromisoformat(date_str)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def resolve_session(
    session_ref: str,
    *,
    sessions_root: Path | None = None,
    project: str | None = None,
) -> Path:
    """Resolve a session reference to an absolute Path.

    Accepts three forms of reference:
    - A full absolute path to a session directory.
    - A full session ID (exact directory name match).
    - A partial session ID (prefix match).

    Args:
        session_ref: Full path, full session ID, or partial ID.
        sessions_root: Root directory containing project subdirectories.
            Defaults to DEFAULT_SESSIONS_ROOT.
        project: Optional project name to narrow the search scope.

    Returns:
        Absolute Path to the resolved session directory.

    Raises:
        FileNotFoundError: If no session matches the reference.
        ValueError: If a partial ID matches more than one session.
    """
    root = sessions_root if sessions_root is not None else DEFAULT_SESSIONS_ROOT

    # Case 1: absolute path reference
    ref_path = Path(session_ref)
    if ref_path.is_absolute():
        if _is_valid_session_dir(ref_path):
            return ref_path
        raise FileNotFoundError(f"No valid session at path: {session_ref}")

    # Case 2 & 3: search by session ID (full or partial)
    exact_matches: list[Path] = []
    prefix_matches: list[Path] = []

    for _proj_name, sessions_dir in _iter_project_dirs(root, project):
        for session_dir in sessions_dir.iterdir():
            if not session_dir.is_dir():
                continue
            if not _is_valid_session_dir(session_dir):
                continue

            name = session_dir.name
            if name == session_ref:
                exact_matches.append(session_dir)
            elif name.startswith(session_ref):
                prefix_matches.append(session_dir)

    # Exact match takes priority over prefix match
    if exact_matches:
        if len(exact_matches) == 1:
            return exact_matches[0]
        raise ValueError(
            f"Ambiguous session ID '{session_ref}': "
            f"matched {[p.name for p in exact_matches]}"
        )

    if len(prefix_matches) == 1:
        return prefix_matches[0]
    if len(prefix_matches) > 1:
        raise ValueError(
            f"Ambiguous partial session ID '{session_ref}': "
            f"matched {[p.name for p in prefix_matches]}"
        )

    raise FileNotFoundError(f"No session found matching '{session_ref}'")


def find_sessions(
    *,
    sessions_root: Path | None = None,
    project: str | None = None,
    after: str | None = None,
    before: str | None = None,
    keyword: str | None = None,
    status: str | None = None,
    limit: int = 50,
) -> list[dict[str, Any]]:
    """List sessions with optional filtering.

    Args:
        sessions_root: Root directory containing project subdirectories.
            Defaults to DEFAULT_SESSIONS_ROOT.
        project: Filter by project name.
        after: ISO date string — only include sessions created after this date.
        before: ISO date string — only include sessions created before this date.
        keyword: Search term — only include sessions whose transcript.jsonl
            contains this keyword (case-insensitive). Never searches events.jsonl.
        status: Health status filter — "healthy" or "broken". Runs
            diagnose_transcript (expensive; opt-in only).
        limit: Maximum number of results to return (default 50).

    Returns:
        List of dicts with keys: session_id, path, project, created, bundle,
        model. Sorted by created (most recent first).
    """
    root = sessions_root if sessions_root is not None else DEFAULT_SESSIONS_ROOT

    after_dt = _parse_date_filter(after) if after else None
    before_dt = _parse_date_filter(before) if before else None

    results: list[dict[str, Any]] = []

    for proj_name, sessions_dir in _iter_project_dirs(root, project):
        for session_dir in sessions_dir.iterdir():
            if not session_dir.is_dir():
                continue
            if not _is_valid_session_dir(session_dir):
                continue

            try:
                meta = load_metadata(session_dir)
            except Exception:
                continue

            created_str = meta.get("created")
            created_dt = _parse_created(created_str)

            # Date range filters
            if after_dt is not None and created_dt is not None:
                if created_dt <= after_dt:
                    continue
            if before_dt is not None and created_dt is not None:
                if created_dt >= before_dt:
                    continue

            # Keyword filter (transcript only, never events.jsonl)
            if keyword is not None:
                if not _transcript_contains_keyword(session_dir, keyword):
                    continue

            # Status filter (expensive — runs diagnosis)
            if status is not None:
                try:
                    entries = load_transcript_with_lines(session_dir)
                    diagnosis = diagnose_transcript(entries)
                    if diagnosis["status"] != status:
                        continue
                except Exception:
                    continue

            results.append(
                {
                    "session_id": meta.get("session_id", session_dir.name),
                    "path": session_dir,
                    "project": proj_name,
                    "created": created_str,
                    "bundle": meta.get("bundle"),
                    "model": meta.get("model"),
                }
            )

    # Sort by created (most recent first) — None sorts last
    results.sort(
        key=lambda r: r["created"] or "",
        reverse=True,
    )

    return results[:limit]


def session_info(session_dir: Path) -> dict[str, Any]:
    """Return metadata, turn count, and health status for a single session.

    Args:
        session_dir: Path to the session directory.

    Returns:
        Dict with keys: session_id, path, project, created, bundle, model,
        turn_count, status, failure_modes.
    """
    session_dir = Path(session_dir).resolve()

    meta = load_metadata(session_dir)

    # Load transcript with line numbers for diagnosis
    entries = load_transcript_with_lines(session_dir)
    diagnosis = diagnose_transcript(entries)
    turn_count = count_turns(entries)

    # Derive project from path: sessions_root/<project>/sessions/<session_id>
    # parent = sessions/, parent.parent = <project>/
    project = session_dir.parent.parent.name

    return {
        "session_id": meta.get("session_id", session_dir.name),
        "path": session_dir,
        "project": project,
        "created": meta.get("created"),
        "bundle": meta.get("bundle"),
        "model": meta.get("model"),
        "turn_count": turn_count,
        "status": diagnosis["status"],
        "failure_modes": diagnosis["failure_modes"],
    }
