"""Fresh-process cold committed-source allocation benchmark, no pytest globals."""

from __future__ import annotations

import argparse
import gc
import json
import tracemalloc
from contextlib import contextmanager
from pathlib import Path

from amplifier_foundation.session.transcript_source import open_committed_source
from amplifier_foundation.session.transcript_writer import TranscriptWriter, fcntl


@contextmanager
def fence(path):
    with (path / ".fixture-owner").open("a+b") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        yield lambda: None


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--work", type=Path, required=True)
    args = parser.parse_args()
    args.work.mkdir(exist_ok=False)
    cases = []
    for mib in (2, 8):
        root = args.work / str(mib)
        root.mkdir()
        value = "x" * (mib * 1024 * 1024)
        owner = TranscriptWriter(root, fence=fence)
        initial = owner.initialize(session_id="source", operation_id="init",
                                   admit=lambda: None).commit
        commit = owner.append([{"role": "user", "content": value}], expected=initial,
                              operation_id="huge", admit=lambda: None).commit
        gc.collect()
        tracemalloc.start()
        with open_committed_source(root) as source:
            bounds = source.message_range(0)
            count = sum(len(raw) for raw in source.iter_bytes(bounds.start, bounds.end))
            assert source.commit == commit and count > len(value)
            _, peak = tracemalloc.get_traced_memory()
        tracemalloc.stop()
        assert peak < 2 * 1024 * 1024
        cases.append({"huge_string_bytes": len(value), "python_peak": peak,
                      "streamed_record_bytes": count, "sha256": commit.sha256})
    print(json.dumps({
        "scope": "fresh-process additional Python allocation, not RSS",
        "cold": "new verify/hash/scan/disk-index per source; input allocated before tracing",
        "cases": cases,
    }))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())