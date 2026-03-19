"""Unified CLI for Amplifier session management.

Subcommands:
    diagnose <session>  -- Diagnose a session transcript
    repair <session>    -- Repair a broken transcript
    rewind <session>    -- Rewind to before the issue
    info <session>      -- Show session metadata and status
    find                -- Find sessions with filtering

All session-accepting subcommands support:
    --sessions-root     Root directory for sessions
    --project           Filter by project name

Human-readable messages go to stderr; structured JSON goes to stdout.

Exit codes:
    0 -- success / healthy
    1 -- broken / error
    2 -- invalid arguments
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# ---------------------------------------------------------------------------
# sys.path manipulation to ensure amplifier_foundation is importable
# ---------------------------------------------------------------------------

_here = Path(__file__).resolve().parent
_root = _here.parent
if str(_root) not in sys.path:
    sys.path.insert(0, str(_root))

from amplifier_foundation.session import (  # noqa: E402
    EVENTS_FILENAME,  # pyright: ignore[reportAttributeAccessIssue]
    TRANSCRIPT_FILENAME,  # pyright: ignore[reportAttributeAccessIssue]
    backup,  # pyright: ignore[reportAttributeAccessIssue]
    diagnose_transcript,  # pyright: ignore[reportAttributeAccessIssue]
    find_sessions,  # pyright: ignore[reportAttributeAccessIssue]
    load_transcript_with_lines,  # pyright: ignore[reportAttributeAccessIssue]
    repair_transcript,  # pyright: ignore[reportAttributeAccessIssue]
    resolve_session,  # pyright: ignore[reportAttributeAccessIssue]
    rewind_transcript,  # pyright: ignore[reportAttributeAccessIssue]
    session_info,  # pyright: ignore[reportAttributeAccessIssue]
    write_transcript,  # pyright: ignore[reportAttributeAccessIssue]
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _to_json_serialisable(obj):
    """Recursively convert non-serialisable values (e.g. Path) to strings."""
    if isinstance(obj, dict):
        return {k: _to_json_serialisable(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_to_json_serialisable(v) for v in obj]
    if isinstance(obj, Path):
        return str(obj)
    return obj


def _resolve(args) -> Path:
    """Resolve session reference from CLI args using resolve_session()."""
    kwargs: dict = {}
    if getattr(args, "sessions_root", None):
        kwargs["sessions_root"] = Path(args.sessions_root)
    if getattr(args, "project", None):
        kwargs["project"] = args.project
    return resolve_session(args.session, **kwargs)


def _output_error(exc: Exception) -> None:
    """Output error as JSON on stdout and message on stderr."""
    print(json.dumps({"error": str(exc)}))
    print(str(exc), file=sys.stderr)


# ---------------------------------------------------------------------------
# Subcommand handlers
# ---------------------------------------------------------------------------


def cmd_diagnose(args) -> int:
    """diagnose subcommand — analyse a session transcript."""
    try:
        session_dir = _resolve(args)
        entries = load_transcript_with_lines(session_dir)
        diagnosis = diagnose_transcript(entries)
        print(json.dumps(_to_json_serialisable(diagnosis), indent=2))
        return 0 if diagnosis["status"] == "healthy" else 1
    except (FileNotFoundError, ValueError) as exc:
        _output_error(exc)
        return 1
    except Exception as exc:  # noqa: BLE001
        _output_error(exc)
        return 1


def cmd_repair(args) -> int:
    """repair subcommand — repair a broken transcript."""
    try:
        session_dir = _resolve(args)
        entries = load_transcript_with_lines(session_dir)
        diagnosis = diagnose_transcript(entries)

        if diagnosis["status"] == "healthy":
            print(json.dumps({"status": "no-repair-needed"}))
            return 0

        # Back up the transcript before modifying it
        transcript_path = session_dir / TRANSCRIPT_FILENAME
        backup_path = backup(transcript_path, "pre-repair")

        # Repair
        repaired = repair_transcript(entries, diagnosis)
        write_transcript(session_dir, repaired)

        # Verify by re-diagnosing
        entries_after = load_transcript_with_lines(session_dir)
        verification = diagnose_transcript(entries_after)

        report = {
            "status": "repaired" if verification["status"] == "healthy" else "failed",
            "original_diagnosis": diagnosis,
            "verification": verification,
            "backup_path": str(backup_path) if backup_path else None,
            "counts": {
                "original_entries": len(entries),
                "repaired_entries": len(repaired),
            },
        }
        print(json.dumps(_to_json_serialisable(report), indent=2))
        return 0 if verification["status"] == "healthy" else 1
    except (FileNotFoundError, ValueError) as exc:
        _output_error(exc)
        return 1
    except Exception as exc:  # noqa: BLE001
        _output_error(exc)
        return 1


def cmd_rewind(args) -> int:
    """rewind subcommand — rewind transcript to before the issue."""
    try:
        session_dir = _resolve(args)
        entries = load_transcript_with_lines(session_dir)
        diagnosis = diagnose_transcript(entries)

        if diagnosis["status"] == "healthy":
            print(json.dumps({"status": "no-rewind-needed"}))
            return 0

        # Back up transcript (and events.jsonl if present)
        transcript_path = session_dir / TRANSCRIPT_FILENAME
        backup_path = backup(transcript_path, "pre-rewind")

        events_path = session_dir / EVENTS_FILENAME
        if events_path.exists():
            backup(events_path, "pre-rewind")

        # Rewind
        rewound = rewind_transcript(entries, diagnosis)
        write_transcript(session_dir, rewound)

        # Truncate events.jsonl proportionally if it exists
        if events_path.exists():
            original_count = len(entries)
            rewound_count = len(rewound)
            with open(events_path, encoding="utf-8") as fh:
                event_lines = fh.readlines()
            if original_count > 0 and event_lines:
                ratio = rewound_count / original_count
                keep = max(0, int(len(event_lines) * ratio))
                with open(events_path, "w", encoding="utf-8") as fh:
                    fh.writelines(event_lines[:keep])

        report = {
            "status": "rewound",
            "original_entries": len(entries),
            "rewound_entries": len(rewound),
            "backup_path": str(backup_path) if backup_path else None,
        }
        print(json.dumps(_to_json_serialisable(report), indent=2))
        return 0
    except (FileNotFoundError, ValueError) as exc:
        _output_error(exc)
        return 1
    except Exception as exc:  # noqa: BLE001
        _output_error(exc)
        return 1


def cmd_info(args) -> int:
    """info subcommand — show session metadata and status."""
    try:
        session_dir = _resolve(args)
        info = session_info(session_dir)
        print(json.dumps(_to_json_serialisable(info), indent=2))
        return 0
    except (FileNotFoundError, ValueError) as exc:
        _output_error(exc)
        return 1
    except Exception as exc:  # noqa: BLE001
        _output_error(exc)
        return 1


def cmd_find(args) -> int:
    """find subcommand — search for sessions with optional filters."""
    try:
        kwargs: dict = {}
        if args.sessions_root:
            kwargs["sessions_root"] = Path(args.sessions_root)
        if args.project:
            kwargs["project"] = args.project
        if args.after:
            kwargs["after"] = args.after
        if args.before:
            kwargs["before"] = args.before
        if args.keyword:
            kwargs["keyword"] = args.keyword
        if args.status:
            kwargs["status"] = args.status
        if args.limit is not None:
            kwargs["limit"] = args.limit

        sessions = find_sessions(**kwargs)
        print(json.dumps(_to_json_serialisable(sessions), indent=2))
        return 0
    except (FileNotFoundError, ValueError) as exc:
        _output_error(exc)
        return 1
    except Exception as exc:  # noqa: BLE001
        _output_error(exc)
        return 1


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------


def _add_session_args(sub: argparse.ArgumentParser) -> None:
    """Add common session resolution arguments to a subparser."""
    sub.add_argument(
        "session",
        help="Session path (absolute), full session ID, or partial session ID.",
    )
    sub.add_argument(
        "--sessions-root",
        metavar="DIR",
        help="Root directory containing project subdirectories.",
    )
    sub.add_argument(
        "--project",
        metavar="NAME",
        help="Limit session resolution to this project.",
    )


def main(argv: list[str] | None = None) -> int:
    """CLI entry point.

    Returns exit code: 0 = success/healthy, 1 = broken/error, 2 = invalid args.
    """
    parser = argparse.ArgumentParser(
        prog="amplifier-session",
        description="Unified CLI for Amplifier session management.",
    )
    subparsers = parser.add_subparsers(dest="subcommand")
    subparsers.required = True

    # diagnose
    diag_p = subparsers.add_parser(
        "diagnose",
        help="Diagnose a session transcript.",
    )
    _add_session_args(diag_p)
    diag_p.set_defaults(func=cmd_diagnose)

    # repair
    repair_p = subparsers.add_parser(
        "repair",
        help="Repair a broken transcript.",
    )
    _add_session_args(repair_p)
    repair_p.set_defaults(func=cmd_repair)

    # rewind
    rewind_p = subparsers.add_parser(
        "rewind",
        help="Rewind transcript to before the issue.",
    )
    _add_session_args(rewind_p)
    rewind_p.set_defaults(func=cmd_rewind)

    # info
    info_p = subparsers.add_parser(
        "info",
        help="Show session metadata and health status.",
    )
    _add_session_args(info_p)
    info_p.set_defaults(func=cmd_info)

    # find
    find_p = subparsers.add_parser(
        "find",
        help="Find sessions with optional filters.",
    )
    find_p.add_argument(
        "--sessions-root",
        metavar="DIR",
        help="Root directory containing project subdirectories.",
    )
    find_p.add_argument(
        "--project",
        metavar="NAME",
        help="Filter by project name.",
    )
    find_p.add_argument(
        "--after",
        metavar="DATE",
        help="Only include sessions created after this ISO date.",
    )
    find_p.add_argument(
        "--before",
        metavar="DATE",
        help="Only include sessions created before this ISO date.",
    )
    find_p.add_argument(
        "--keyword",
        metavar="TERM",
        help="Only include sessions whose transcript contains this keyword.",
    )
    find_p.add_argument(
        "--status",
        choices=["healthy", "broken"],
        help="Filter by health status (runs diagnosis; may be slow).",
    )
    find_p.add_argument(
        "--limit",
        type=int,
        default=50,
        metavar="N",
        help="Maximum number of results (default: 50).",
    )
    find_p.set_defaults(func=cmd_find)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
