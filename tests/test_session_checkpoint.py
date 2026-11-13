"""Synthetic legacy-save acknowledgements, independent of a new writer layout."""

import dataclasses
import hashlib
import json
import os
from pathlib import Path

import pytest

from amplifier_foundation.session.checkpoint import (
    CheckpointRefused,
    acknowledge_legacy_checkpoint,
    verified_checkpoint_refs,
)

pytestmark = pytest.mark.skipif(os.name != "posix", reason="POSIX fsync qualification")
MEDIA = "experimental.media.v1"


def save(path: Path, rows):
    path.mkdir()
    raw = "".join(json.dumps(row) + "\n" for row in rows).encode()
    (path / "transcript.jsonl").write_bytes(raw)
    return raw


def ack(path, rows):
    return acknowledge_legacy_checkpoint(
        path, session_id="session-1", expected_messages=iter(rows)
    )


def test_exact_saved_primary_and_metadata_reachability(tmp_path):
    ref = {"v": 1, "sha256": "a" * 64, "provenance": {"id": "call-1"}}
    rows = [
        {"role": "user", "content": "hello", "metadata": {"opaque": {"keep": 3}}},
        {"role": "tool", "tool_call_id": "call-1", "content": "image label",
         "metadata": {MEDIA: {"refs": [ref]}}},
    ]
    path = tmp_path / "session"
    raw = save(path, rows)
    receipt = ack(path, rows)
    assert receipt.sha256 == hashlib.sha256(raw).hexdigest()
    assert receipt.prefix_bytes == len(raw)
    assert receipt.message_count == 2
    assert receipt.contains(ref)
    assert not receipt.contains({**ref, "v": True})
    assert not receipt.contains({**ref, "provenance": {"id": "other"}})
    assert receipt.verify()["kind"] == "legacy-checkpoint-backed-v1"
    assert (path / "transcript.jsonl").read_bytes() == raw
    assert sorted(item.name for item in path.iterdir()) == ["transcript.jsonl"]


@pytest.mark.parametrize("changed", [
    [], [{"role": "user", "content": "other"}],
    [{"role": "user", "content": "saved"}, {"role": "assistant", "content": "tail"}],
])
def test_mismatched_snapshot_cannot_acknowledge(tmp_path, changed):
    path = tmp_path / "session"
    raw = save(path, [{"role": "user", "content": "saved"}])
    with pytest.raises(CheckpointRefused):
        ack(path, changed)
    assert (path / "transcript.jsonl").read_bytes() == raw


def test_parseable_interrupted_tail_never_commits_or_falls_back(tmp_path):
    path = tmp_path / "session"
    rows = [{"role": "user", "content": "saved"}]
    raw = save(path, rows)
    (path / "transcript.jsonl.backup").write_bytes(raw)
    (path / "transcript.jsonl.append-pending").write_text("{}")
    with pytest.raises(CheckpointRefused, match="Unresolved"):
        ack(path, rows)
    assert (path / "transcript.jsonl.append-pending").exists()


@pytest.mark.parametrize("raw", [
    b'{"role":"user","role":"assistant"}\n', b'{"role":"user","content":NaN}\n',
    b'{"format":"amplifier.session.transcript","v":2}\n', b'{"role":"user"',
])
def test_ambiguous_invalid_or_enveloped_rows_refused(tmp_path, raw):
    path = tmp_path / "session"
    path.mkdir()
    (path / "transcript.jsonl").write_bytes(raw)
    with pytest.raises(CheckpointRefused):
        ack(path, [])


def test_mutation_replacement_and_forged_receipt_refused(tmp_path):
    path = tmp_path / "session"
    rows = [{"role": "user", "content": "saved"}]
    raw = save(path, rows)
    receipt = ack(path, rows)
    with pytest.raises(CheckpointRefused):
        dataclasses.replace(receipt, _seal=object()).verify()
    for change in (
        {"session_id": "different"}, {"prefix_bytes": 999},
        {"message_count": 99}, {"sha256": "b" * 64},
    ):
        with pytest.raises(CheckpointRefused):
            dataclasses.replace(receipt, **change).verify()
    other = path / "replacement"
    other.write_bytes(raw)
    other.replace(path / "transcript.jsonl")
    with pytest.raises(CheckpointRefused, match="source changed"):
        receipt.verify()


@pytest.mark.parametrize("target", ["leaf", "ancestor"])
def test_symlinks_are_not_canonical_checkpoint_authority(tmp_path, target):
    real = tmp_path / "real"
    rows = [{"role": "user", "content": "saved"}]
    raw = save(real, rows)
    path = tmp_path / "session"
    if target == "ancestor":
        path.symlink_to(real, target_is_directory=True)
    else:
        path.mkdir()
        (path / "transcript.jsonl").symlink_to(real / "transcript.jsonl")
    with pytest.raises(CheckpointRefused):
        ack(path, rows)
    assert (real / "transcript.jsonl").read_bytes() == raw


def test_fsync_failure_is_not_a_successful_ack(tmp_path, monkeypatch):
    path = tmp_path / "session"
    rows = [{"role": "user", "content": "saved"}]
    raw = save(path, rows)

    def fail(_):
        raise OSError("synthetic fsync failure")

    monkeypatch.setattr(os, "fsync", fail)
    with pytest.raises(CheckpointRefused):
        ack(path, rows)
    assert (path / "transcript.jsonl").read_bytes() == raw


def test_report_or_stringified_ref_cannot_establish_reachability(tmp_path):
    path = tmp_path / "session"
    ref = {"v": 1, "session_id": "session-1"}
    rows = [{"role": "tool", "content": json.dumps({MEDIA: {"refs": [ref]}}),
             "metadata": {"report": {"event": "committed", "refs": [ref]}}}]
    save(path, rows)
    assert not ack(path, rows).contains(ref)


def test_verifier_requires_local_receipt_and_exhausts_refs(tmp_path):
    ref = {"v": 1, "session_id": "session-1"}
    rows = [{"role": "tool", "metadata": {MEDIA: {"refs": [ref]}}}]
    path = tmp_path / "session"
    save(path, rows)
    assert list(verified_checkpoint_refs(ack(path, rows))) == [ref]
    with pytest.raises(CheckpointRefused):
        list(verified_checkpoint_refs({"session_id": "session-1", "refs": [ref]}))


def test_fifo_is_refused_without_waiting_for_a_writer(tmp_path):
    path = tmp_path / "session"
    path.mkdir()
    os.mkfifo(path / "transcript.jsonl")
    with pytest.raises(CheckpointRefused):
        ack(path, [])


def test_pending_appearing_during_scan_prevents_ack(tmp_path):
    path = tmp_path / "session"
    rows = [{"role": "user", "content": "saved"}]
    raw = save(path, rows)

    def expected():
        yield rows[0]
        (path / "transcript.jsonl.append-pending").write_text("{}")

    with pytest.raises(CheckpointRefused, match="Unresolved"):
        acknowledge_legacy_checkpoint(path, session_id="session-1",
                                      expected_messages=expected())
    assert (path / "transcript.jsonl").read_bytes() == raw


def test_generator_source_replacement_is_refused_at_end(tmp_path):
    path = tmp_path / "session"
    ref = {"v": 1, "session_id": "session-1"}
    rows = [{"role": "tool", "metadata": {MEDIA: {"refs": [ref]}}}]
    raw = save(path, rows)
    refs = verified_checkpoint_refs(ack(path, rows))
    assert next(refs) == ref
    replacement = path / "replacement"
    replacement.write_bytes(raw)
    replacement.replace(path / "transcript.jsonl")
    with pytest.raises(CheckpointRefused):
        next(refs)


def test_parent_traversal_is_not_normalized_past_symlink(tmp_path):
    path = tmp_path / "session"
    rows = [{"role": "user", "content": "saved"}]
    save(path, rows)
    (tmp_path / "link").symlink_to(tmp_path, target_is_directory=True)
    with pytest.raises(CheckpointRefused, match="Parent traversal"):
        ack(tmp_path / "link" / ".." / "session", rows)


def test_mutation_during_flush_cannot_return_stale_receipt(tmp_path, monkeypatch):
    path = tmp_path / "session"
    rows = [{"role": "user", "content": "saved"}]
    raw = save(path, rows)
    real_fsync = os.fsync
    changed = False

    def flush(descriptor):
        nonlocal changed
        real_fsync(descriptor)
        if not changed:
            changed = True
            (path / "transcript.jsonl").write_bytes(raw + b"\n")

    monkeypatch.setattr(os, "fsync", flush)
    with pytest.raises(CheckpointRefused, match="source changed"):
        ack(path, rows)


@pytest.mark.parametrize("mode", ["directory", "missing", "invalid"])
def test_no_backup_fallback_for_unusable_primary(tmp_path, mode):
    path = tmp_path / "session"
    path.mkdir()
    rows = [{"role": "user", "content": "saved"}]
    raw = (json.dumps(rows[0]) + "\n").encode()
    (path / "transcript.jsonl.backup").write_bytes(raw)
    primary = path / "transcript.jsonl"
    if mode == "directory":
        primary.mkdir()
    elif mode == "invalid":
        primary.write_bytes(b"not-json")
    with pytest.raises(CheckpointRefused):
        ack(path, rows)
    assert (path / "transcript.jsonl.backup").read_bytes() == raw