"""Session repair tool for Amplifier transcript files.

Usage:
    python scripts/session-repair.py <session-dir>

Exit codes:
    0 - Success / session is healthy
    1 - Repair needed or repair failed
    2 - Invalid arguments
"""

from __future__ import annotations

import warnings

warnings.warn(
    "scripts/session-repair.py is deprecated. "
    "Use scripts/amplifier-session.py instead. "
    "Example: python scripts/amplifier-session.py diagnose <session>",
    DeprecationWarning,
    stacklevel=1,
)

import argparse  # noqa: E402
import json  # noqa: E402
import sys  # noqa: E402
from pathlib import Path  # noqa: E402
from typing import TYPE_CHECKING  # noqa: E402

# ---------------------------------------------------------------------------
# sys.path manipulation to ensure amplifier_foundation is importable
# ---------------------------------------------------------------------------

_here = Path(__file__).resolve().parent
_root = _here.parent
if str(_root) not in sys.path:
    sys.path.insert(0, str(_root))

from amplifier_foundation.session import (  # noqa: E402
    backup,  # pyright: ignore[reportAttributeAccessIssue]
    build_tool_index,  # pyright: ignore[reportAttributeAccessIssue]
    diagnose_transcript,  # pyright: ignore[reportAttributeAccessIssue]
    is_real_user_message,  # pyright: ignore[reportAttributeAccessIssue]
    load_transcript_with_lines,  # pyright: ignore[reportAttributeAccessIssue]
    repair_transcript,  # pyright: ignore[reportAttributeAccessIssue]
    rewind_transcript,  # pyright: ignore[reportAttributeAccessIssue]
    write_transcript,  # pyright: ignore[reportAttributeAccessIssue]
)

if TYPE_CHECKING:
    from amplifier_foundation.session import DiagnosisResult  # pyright: ignore[reportAttributeAccessIssue]

# Re-export for backward compatibility (tests import these from this module)
__all__ = [
    "parse_transcript",
    "build_tool_index",
    "is_real_user_message",
    "diagnose",
    "repair_transcript",
    "rewind_transcript",
]


# ---------------------------------------------------------------------------
# Thin wrappers (CLI-level adapters)
# ---------------------------------------------------------------------------


def parse_transcript(transcript_path: Path) -> list[dict]:
    """Read transcript.jsonl and return a list of entries with line numbers.

    Each returned dict is the original JSON object with an added 'line_num' key
    (1-based) indicating its position in the file. Empty lines are skipped.

    This is a thin adapter around ``load_transcript_with_lines``. The
    ``transcript_path`` argument should be the path to the transcript.jsonl
    file itself; the session directory is inferred from its parent.
    """
    return load_transcript_with_lines(transcript_path.parent)


def diagnose(session_dir: Path | str) -> DiagnosisResult:
    """Analyse a session transcript and return a structured diagnosis.

    Returns a DiagnosisResult with keys:
        status: "healthy" | "broken"
        failure_modes: list of strings
        orphaned_tool_ids: list of tool_use IDs with no matching tool_result
        misplaced_tool_ids: list of tool_use IDs whose results are out of order
        incomplete_turns: list of IncompleteTurn dicts with 'after_line' and 'missing'
        recommended_action: "none" | "repair" ("rewind" reserved for future use)
    """
    session_dir = Path(session_dir)
    entries = load_transcript_with_lines(session_dir)
    return diagnose_transcript(entries)


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    """CLI entry point for session repair.

    Returns exit code: 0 success, 1 broken/failed, 2 usage error.
    """
    parser = argparse.ArgumentParser(
        description="Diagnose and repair Amplifier session transcripts.",
    )
    parser.add_argument("session_dir", help="Path to the session directory")

    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument("--diagnose", action="store_true", help="Print JSON diagnosis")
    action.add_argument(
        "--repair", action="store_true", help="Repair a broken transcript"
    )
    action.add_argument(
        "--rewind",
        action="store_true",
        help="Rewind transcript to before the issue",
    )

    args = parser.parse_args(argv)
    session_dir = Path(args.session_dir)
    transcript_path = session_dir / "transcript.jsonl"

    if not transcript_path.exists():
        print(f"Error: {transcript_path} not found", file=sys.stderr)
        return 2

    # --diagnose ---------------------------------------------------------
    if args.diagnose:
        diag = diagnose(session_dir)
        print(json.dumps(diag, indent=2))
        return 0 if diag["status"] == "healthy" else 1

    # --repair -----------------------------------------------------------
    if args.repair:
        diag = diagnose(session_dir)
        if diag["status"] == "healthy":
            print("no-repair-needed")
            return 0

        backup(transcript_path, "pre-repair")
        entries = load_transcript_with_lines(session_dir)
        repaired = repair_transcript(entries, diag)
        write_transcript(session_dir, repaired)

        # Verify by re-diagnosing
        verification = diagnose(session_dir)
        report = {
            "status": "repaired" if verification["status"] == "healthy" else "failed",
            "original_diagnosis": diag,
            "verification": verification,
            "counts": {
                "original_entries": len(entries),
                "repaired_entries": len(repaired),
            },
        }
        print(json.dumps(report, indent=2))
        return 0 if verification["status"] == "healthy" else 1

    # --rewind -----------------------------------------------------------
    if args.rewind:
        diag = diagnose(session_dir)
        if diag["status"] == "healthy":
            print("no-rewind-needed")
            return 0

        backup(transcript_path, "pre-rewind")
        events_path = session_dir / "events.jsonl"
        if events_path.exists():
            backup(events_path, "pre-rewind")

        entries = load_transcript_with_lines(session_dir)
        rewound = rewind_transcript(entries, diag)
        write_transcript(session_dir, rewound)

        # Truncate events.jsonl proportionally if it exists
        if events_path.exists():
            original_count = len(entries)
            rewound_count = len(rewound)
            with open(events_path, encoding="utf-8") as f:
                event_lines = f.readlines()
            if original_count > 0 and event_lines:
                ratio = rewound_count / original_count
                keep = max(0, int(len(event_lines) * ratio))
                with open(events_path, "w", encoding="utf-8") as f:
                    f.writelines(event_lines[:keep])

        report = {
            "status": "rewound",
            "original_entries": len(entries),
            "rewound_entries": len(rewound),
        }
        print(json.dumps(report, indent=2))
        return 0

    return 2  # pragma: no cover


if __name__ == "__main__":
    sys.exit(main())
