"""Synthetic exact exported-graph serialized rewrite -> actual F1 typed ACK.

Use an explicit verified source export; never imports a live host or modifies its
configuration. The caller records every mechanically overlaid module hash. This
qualifies the named candidate graph, not manager acceptance or live activation.
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import sys
from contextlib import closing, contextmanager
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--foundation-root", type=Path, required=True)
    parser.add_argument("--work", type=Path, required=True)
    args = parser.parse_args()
    root = args.foundation_root.absolute()
    args.work = args.work.absolute()
    args.work.mkdir(mode=0o700, exist_ok=False)
    sys.path.insert(0, str(root))
    from amplifier_foundation.session.media import MediaRefusal, SessionMediaStore
    from amplifier_foundation.session.media_conversion import (
        iter_serialized_candidate_refs,
    )
    from amplifier_foundation.session.versioned_checkpoint import (
        acknowledge_versioned_checkpoint,
    )
    from PIL import Image

    from amplifier_foundation.session import transcript_writer as w
    from amplifier_foundation.session.checkpoint import verified_media_checkpoint_refs
    assert Path(w.__file__).resolve().is_relative_to(root)
    media_namespace = "experimental.media.v1"

    @contextmanager
    def fence(path):
        with (path / ".synthetic-owner").open("a+b") as lease:
            w.fcntl.flock(lease.fileno(), w.fcntl.LOCK_EX)
            yield lambda: None

    results = []
    for image_format in ("PNG", "JPEG"):
        session = args.work / image_format.lower()
        session.mkdir(mode=0o700)
        writer = w.TranscriptWriter(session, fence=fence)
        initial = writer.initialize(session_id="fixture", operation_id="init", admit=lambda: None)
        with closing(SessionMediaStore(
            session / "media", "fixture", checkpoint_verifier=verified_media_checkpoint_refs,
        )) as store:
            pixels = io.BytesIO()
            Image.new("RGB", (2, 2), "blue").save(pixels, format=image_format)
            raw_image = pixels.getvalue()
            origin = {"kind": "tool", "session_id": "fixture", "id": "call-1"}
            ref = store.ingest_sync(raw_image, source_format="image-bytes",
                                    origin=origin, policy=None)["value"]
            store.publish_sync([ref], origin=origin, disposition="retain")
            rows = [{"role": "tool", "content": "retained image", "tool_call_id": "call-1",
                     "metadata": {media_namespace: {"refs": [ref]}}}]
            raw = w._header("candidate") + b"".join(
                (json.dumps(row) + "\n").encode() for row in rows)
            path = args.work / (image_format + "-candidate.jsonl")
            path.write_bytes(raw)
            prefix = w.CommittedPrefix("fixture", "candidate", "g-candidate.jsonl", 1,
                                        len(raw), 1, hashlib.sha256(raw).hexdigest(), "build")
            with path.open("rb") as stream:
                with w.prepare_serialized_candidate(
                    stream, candidate_prefix=prefix, candidate_check=lambda: None,
                ) as prepared:
                    digest = prepared.canonical_sha256
                    admitted = []

                    def admit():
                        with prepared.open_verified() as verified:
                            for actual in iter_serialized_candidate_refs(
                                verified, candidate_prefix=prepared.candidate_prefix,
                            ):
                                store.validate_published_ref(actual)
                                admitted.append(actual)
                        prepared.check()

                    ack = writer.rewrite_prepared(prepared, expected=initial.commit,
                                                  operation_id="converted", admit=admit)
                    assert admitted == [ref]
                    try:
                        store.resolve_ref(ref)
                    except MediaRefusal:
                        pass
                    else:
                        raise AssertionError("Unacknowledged publication resolved")
                    with acknowledge_versioned_checkpoint(
                        writer, session_id="fixture", expected=ack,
                    ) as receipt:
                        result = store.acknowledge_checkpoint(receipt)
                    receipt.verify()
                # Enclosing prepared-candidate checks also completed successfully.
                assert writer.reconcile(operation_id="converted", expected=initial.commit,
                                        suffix_sha256=digest, kind="rewrite") is not None
            with store.resolve_ref(ref) as resolved:
                assert resolved.read() == raw_image
            facts = receipt.verify()
            try:
                store.acknowledge_checkpoint(facts)
            except ValueError:
                pass
            else:
                raise AssertionError("Serialized facts minted checkpoint authority")
            results.append({"image": image_format, "ack_state": result["state"],
                            "ref_count": result["ref_count"], "canonical_sha256": digest,
                            "kind": facts["kind"], "sha256": ack.commit.sha256})
    print(json.dumps({"scope": "synthetic-exported-candidate-graph-actual-F1-typedACK",
                      "results": results}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
