"""Synthetic memory/work benchmark; creates no live sessions or external resources.

Run with --work pointing to a new directory. Two corpus sizes (2MiB and 8MiB)
contain one known huge individual string, not a large list of tiny rows. The
string exists before tracing begins. Reports peak *additional Python allocation*,
wall time and exact body-I/O; does not claim RSS or bounded-memory public resume.
"""

from __future__ import annotations

import argparse
import gc
import json
import os
import time
import tracemalloc
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

from amplifier_foundation.session import transcript_writer as w
from amplifier_foundation.session.history import SessionHistoryStore


@contextmanager
def fixture_fence(path):
    """The only writers are this script; flock supplies actual mutual exclusion."""
    with (path / ".fixture-owner").open("a+b") as lock:
        w.fcntl.flock(lock.fileno(), w.fcntl.LOCK_EX)

        def check():
            if lock.closed:
                raise RuntimeError("Fixture ownership expired")
        yield check


def text_only_admission():
    pass


def measured(function):
    gc.collect()
    tracemalloc.start()
    start = time.perf_counter()
    result = function()
    seconds = time.perf_counter() - start
    _, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    return result, {"seconds": seconds, "python_peak": peak}


class Counted:
    def __init__(self, stream, counts):
        self.stream, self.counts = stream, counts

    def __getattr__(self, name):
        return getattr(self.stream, name)

    def read(self, size=-1):
        raw = self.stream.read(size)
        self.counts["read"] += len(raw)
        return raw

    def write(self, raw):
        self.counts["write"] += len(raw)
        return self.stream.write(raw)


def run_case(root, size):
    value = "x" * size
    rows = [{"role": "user", "content": value}]
    legacy = root / "legacy"
    legacy.mkdir()
    store = SessionHistoryStore(legacy)
    _, baseline = measured(lambda: store.save(rows, {"session_id": "fixture"}))
    baseline_bytes = (legacy / "transcript.jsonl").stat().st_size

    session = root / "versioned"
    session.mkdir()
    writer = w.TranscriptWriter(session, fence=fixture_fence)
    initial = writer.initialize(session_id="fixture", operation_id="init",
                                admit=text_only_admission).commit
    first, encoding = measured(lambda: writer.append(
        rows, expected=initial, operation_id="huge", admit=text_only_admission))
    # Warm appends must not re-read the now-huge prefix.
    original_file = w._Dir.file
    io_counts = {"read": 0, "write": 0}

    @contextmanager
    def counted(directory, name, *args, **kwargs):
        with original_file(directory, name, *args, **kwargs) as stream:
            yield Counted(stream, io_counts) if name.endswith(".jsonl") else stream

    suffix = [{"role": "assistant", "content": "small settled suffix"}]
    with patch.object(w._Dir, "file", counted):
        next_commit, warm = measured(lambda: writer.append(
            suffix, expected=first.commit, operation_id="warm", admit=text_only_admission))
    body_path = session / "transcript.v2" / next_commit.commit.body
    with body_path.open("rb") as stream:
        digest, _ = w._digest(stream)
    assert digest.hexdigest() == next_commit.commit.sha256
    assert io_counts["read"] == 0
    assert io_counts["write"] < 1024

    migration = root / "migration"
    migration.mkdir()
    source = migration / "transcript.jsonl"
    # Copy legacy bytes in bounded chunks, not a measured whole-line read.
    with (legacy / "transcript.jsonl").open("rb") as original, source.open("wb") as target:
        while raw := original.read(64 * 1024):
            target.write(raw)
    sources = {name: w.observe_source(migration, name) for name in w.LEGACY}
    converter = w.TranscriptWriter(migration, fence=fixture_fence)
    converted, conversion = measured(lambda: converter.migrate(
        session_id="fixture", operation_id="convert", expected_sources=sources,
        source="transcript.jsonl", admit=text_only_admission))
    with source.parent.joinpath("recovery/convert/transcript.jsonl").open("rb") as stream:
        original_hash, _ = w._digest(stream)
    assert original_hash.hexdigest() == sources["transcript.jsonl"].sha256
    assert converted.commit.message_count == 1
    assert encoding["python_peak"] < 2 * 1024 * 1024
    assert conversion["python_peak"] < 3 * 1024 * 1024
    return {
        "huge_string_chars": len(value), "baseline_jsonl_bytes": baseline_bytes,
        "legacy_full_save": baseline, "suffix_encoding": encoding,
        "warm_small_append": warm, "warm_body_io": io_counts,
        "migration": conversion, "original_sha256": original_hash.hexdigest(),
        "committed_sha256": converted.commit.sha256,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--work", type=Path, required=True)
    args = parser.parse_args()
    if os.name != "posix":
        parser.error("POSIX-only synthetic qualification")
    args.work.mkdir(exist_ok=False)
    cases = []
    for mib in (2, 8):
        root = args.work / str(mib)
        root.mkdir()
        cases.append(run_case(root, mib * 1024 * 1024))
    result = {
        "scope": "synthetic-posix-huge-individual-string-additional-python-allocation",
        "sizes_ratio": 4, "cases": cases,
        "not_qualified": ["public resume memory", "RSS", "Windows durability", "live activation"],
        "time_ratios": {key: cases[1][key]["seconds"] / cases[0][key]["seconds"]
                        for key in ("legacy_full_save", "suffix_encoding", "migration")},
    }
    report = args.work / "result.json"
    report.write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())