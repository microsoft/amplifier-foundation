"""Run actual cold old Foundation entrypoints against synthetic layout fences.

Explicit qualification script, not a migration/writer implementation. Supply an
immutable source export via --baseline-root and an absent --work directory. This
does not qualify CLI/Unified entrypoints or Windows namespace durability.
"""

from __future__ import annotations

import argparse
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys


OPERATIONS = (
    "load", "index", "save", "save_messages", "raw_load", "line_load",
    "raw_write", "fork", "guard", "diagnose", "repair", "rewind",
)

PROBE = r"""
import importlib.util, json, pathlib, sys
baseline, session, operation = map(str, sys.argv[1:])
sys.path.insert(0, baseline)
from amplifier_foundation.session.history import SessionHistoryStore
from amplifier_foundation.session.store import (
    load_transcript, load_transcript_with_lines, write_transcript,
)
from amplifier_foundation.session.fork import fork_session
from amplifier_foundation.session.transcript_guard import NativeTranscriptGuard
import amplifier_foundation.session.history as history
assert pathlib.Path(history.__file__).is_relative_to(pathlib.Path(baseline))
path = pathlib.Path(session)
rows = [{"role": "user", "content": "new-old-writer-content"}]
try:
    if operation == "load": SessionHistoryStore(path).load_messages()
    elif operation == "index": SessionHistoryStore(path).indexed_messages()
    elif operation == "save": SessionHistoryStore(path).save(rows, {"session_id":"fixture"})
    elif operation == "save_messages": SessionHistoryStore(path).save_messages(rows)
    elif operation == "raw_load": load_transcript(path)
    elif operation == "line_load": load_transcript_with_lines(path)
    elif operation == "raw_write": write_transcript(path, rows)
    elif operation == "fork":
        fork_session(path, target_dir=path.parent, new_session_id="fork-destination",
                     include_events=False)
    elif operation == "guard": NativeTranscriptGuard(path / "transcript.jsonl")
    else:
        spec = importlib.util.spec_from_file_location(
            "old_session_script", pathlib.Path(baseline) / "scripts/amplifier-session.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        import types
        result = getattr(module, "cmd_" + operation)(
            types.SimpleNamespace(session=str(path), project=None, sessions_root=None))
        if result != 0:
            print(json.dumps({"refused":True,"operation":operation,"kind":"exit-nonzero"}))
            raise SystemExit(0)
    print(json.dumps({"refused":False,"operation":operation}))
except (OSError, ValueError, RuntimeError) as error:
    print(json.dumps({"refused":True,"operation":operation,"kind":type(error).__name__}))
"""


def inventory(root):
    return {
        str(path.relative_to(root)): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(root.rglob("*")) if path.is_file()
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline-root", required=True, type=Path)
    parser.add_argument("--work", required=True, type=Path)
    parser.add_argument("--revision", required=True)
    parser.add_argument("--writer-fixture", action="store_true",
                        help="Exercise actual candidate migration output, not layout-only fixture")
    args = parser.parse_args()
    baseline = args.baseline_root.absolute()
    if not (baseline / "amplifier_foundation/session/history.py").is_file():
        parser.error("Expected an explicit exported Foundation source root")
    args.work.mkdir(mode=0o700, parents=False, exist_ok=False)
    results = []
    for operation in OPERATIONS:
        session = args.work / operation
        session.mkdir()
        for name in ("transcript.jsonl", "transcript.jsonl.backup"):
            fence = session / name
            fence.mkdir()
            (fence / "format.json").write_text(json.dumps({
                "format": "amplifier.session.transcript", "v": 2,
                "status": "unsupported-by-legacy-entrypoint",
            }))
        versioned = session / "transcript.v2"
        versioned.mkdir()
        (versioned / "g-fixture.jsonl").write_text(
            '{"format":"amplifier.session.transcript","v":2}\n'
            '{"role":"user","content":"preserved-canonical-body"}\n'
        )
        (versioned / "current.json").write_text('{"synthetic":"not-a-writer-commit"}')
        recovery = session / "recovery"
        recovery.mkdir()
        (recovery / "original.jsonl").write_text(
            '{"role":"user","content":"preserved-original"}\n'
        )
        (session / "metadata.json").write_text('{"session_id":"fixture"}')
        if args.writer_fixture:
            # Use a separate, genuinely generated fixture rather than deleting
            # or repurposing any bytes from the layout-only qualification above.
            from amplifier_foundation.session import transcript_writer as writer
            generated = args.work / (operation + "-writer")
            generated.mkdir()
            for name in writer.LEGACY:
                (generated / name).write_text(
                    '{"role":"user","content":"retained-original"}\n')

            @contextmanager
            def fixture_fence(path):
                with (path / ".fixture-owner").open("a+b") as lock:
                    writer.fcntl.flock(lock.fileno(), writer.fcntl.LOCK_EX)
                    def check():
                        assert not lock.closed
                    yield check
            owner = writer.TranscriptWriter(generated, fence=fixture_fence)
            witnesses = {name: writer.observe_source(generated, name) for name in writer.LEGACY}
            commit = owner.migrate(
                session_id="fixture", operation_id="migration", expected_sources=witnesses,
                source="transcript.jsonl", admit=lambda: None).commit
            with (generated / "transcript.v2" / commit.body).open("ab") as body:
                body.write(b'{"role":"user","content":"uncommitted-tail"}\n')
            (generated / "metadata.json").write_text('{"session_id":"fixture"}')
            session = generated
        before = inventory(session)
        result = subprocess.run(
            [sys.executable, "-c", PROBE, str(baseline), str(session.absolute()), operation],
            cwd=args.work, env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
            text=True, capture_output=True, timeout=30,
        )
        # Parse only the final machine result, not old-script error text.
        last = result.stdout.strip().splitlines()
        evidence = json.loads(last[-1]) if last else {}
        unchanged = inventory(session) == before
        passed = result.returncode == 0 and evidence.get("refused") is True and unchanged
        results.append({
            "operation": operation, "passed": passed, "unchanged": unchanged,
            "exit": result.returncode, "refused": evidence.get("refused"),
            "kind": evidence.get("kind"),
        })
    report = {
        "baseline_revision": args.revision,
        "baseline_files": inventory(baseline),
        "platform": sys.platform,
        "scope": ("actual-old-Foundation-entrypoints-candidate-migration-output"
                  if args.writer_fixture else
                  "actual-old-Foundation-entrypoints-directory-fence-fixtures"),
        "not_qualified": [
            "CLI", "Unified", "migration", "writer", "Windows durability",
            "interrupted fence installation", "missing or damaged fence",
        ],
        "results": results,
    }
    (args.work / "result.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps({"passed": sum(row["passed"] for row in results),
                      "total": len(results), "result": str(args.work / "result.json")}))
    return 0 if all(row["passed"] for row in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())