"""Fresh-process serialized candidate prepare/admit/rewrite allocation check."""

from __future__ import annotations

import argparse
import gc
import hashlib
import json
import tracemalloc
from contextlib import contextmanager
from pathlib import Path

from amplifier_foundation.session import transcript_writer as w
from amplifier_foundation.session.transcript_source import open_committed_source


@contextmanager
def fence(path):
    with (path / ".fixture-owner").open("a+b") as lease:
        w.fcntl.flock(lease.fileno(), w.fcntl.LOCK_EX)
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
        target = root / "session"
        target.mkdir()
        writer = w.TranscriptWriter(target, fence=fence)
        prior = writer.initialize(session_id="fixture", operation_id="init", admit=lambda: None).commit
        path = root / "candidate"
        # Write the huge scalar in bounded pieces before measurement. There is
        # no in-memory giant string/list hidden in fixture construction.
        with path.open("wb") as stream:
            stream.write(w._header("candidate"))
            stream.write(b'{"role":"user","content":"')
            for _ in range(mib * 16):
                stream.write(b"x" * 65536)
            stream.write(b'"}\n')
        with path.open("rb") as stream:
            digest, size = w._digest(stream)
        prefix = w.CommittedPrefix("fixture", "candidate", "g-candidate.jsonl", 1, size, 1,
                                    digest.hexdigest(), "build")
        gc.collect()
        tracemalloc.start()
        with path.open("rb") as stream:
            with w.prepare_serialized_candidate(
                stream, candidate_prefix=prefix, candidate_check=lambda: None,
            ) as prepared:
                canonical_hash = prepared.canonical_sha256

                def admit():
                    with prepared.open_verified() as verified:
                        actual = hashlib.sha256()
                        while raw := verified.read(65536):
                            actual.update(raw)
                        assert actual.hexdigest() == prefix.sha256

                ack = writer.rewrite_prepared(
                    prepared, expected=prior, operation_id="rewrite", admit=admit)
        _, peak = tracemalloc.get_traced_memory()
        tracemalloc.stop()
        assert peak < 2 * 1024 * 1024
        with open_committed_source(target, expected=ack.commit) as source:
            assert source.commit.message_count == 1
        assert writer.reconcile(operation_id="rewrite", expected=prior,
                                suffix_sha256=canonical_hash, kind="rewrite") is not None
        cases.append({"string_bytes": mib * 1024 * 1024, "python_peak": peak,
                      "raw_sha256": prefix.sha256, "canonical_sha256": canonical_hash,
                      "committed_sha256": ack.commit.sha256})
    print(json.dumps({"scope": "fresh-process additional Python allocation not RSS",
                      "cases": cases}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())