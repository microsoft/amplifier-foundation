"""Reader-only fixtures; none invokes a production v2 writer or migration."""

import hashlib
import json
import os

import pytest

from amplifier_foundation.session.transcript_v2 import (
    CommittedTranscriptReader,
    UnsupportedTranscript,
)

pytestmark = pytest.mark.skipif(os.name != "posix", reason="POSIX reader qualification")


def fixture(root, *, rows=None, tail=b""):
    folder = root / "transcript.v2"
    folder.mkdir()
    header = {"format": "amplifier.session.transcript", "v": 2, "generation": "fixture",
              "min_reader": ["committed-prefix-v2", "session-media-v1"]}
    rows = rows if rows is not None else [
        {"role": "user", "content": "question"},
        {"role": "tool", "tool_call_id": "call-1", "content": "result",
         "metadata": {"provider": {"opaque": [1, "signature"]},
                      "experimental.media.v1": {"refs": []}}},
    ]
    raw = b"".join((json.dumps(row) + "\n").encode() for row in [header, *rows])
    body = folder / "g-fixture.jsonl"
    body.write_bytes(raw + tail)
    descriptor = {
        "format": "amplifier.session.transcript", "v": 2, "session_id": "fixture",
        "generation": "fixture", "body": "g-fixture.jsonl", "sequence": 1,
        "committed_bytes": len(raw), "message_count": len(rows),
        "sha256": hashlib.sha256(raw).hexdigest(), "operation_id": "append-1",
        "min_reader": ["committed-prefix-v2", "session-media-v1"],
    }
    (folder / "current.json").write_text(json.dumps(descriptor))
    return rows, descriptor, body


def test_reads_only_exact_committed_prefix_and_preserves_metadata(tmp_path):
    rows, _, body = fixture(
        tmp_path, tail=b'{"role":"assistant","content":"complete-but-uncommitted"}\n'
                       b'{"role":"user","content":"partial'
    )
    before = body.read_bytes()
    reader = CommittedTranscriptReader(tmp_path)
    assert reader.verify().message_count == 2
    assert list(reader.iter_messages()) == rows
    assert body.read_bytes() == before
    assert sorted(path.name for path in (tmp_path / "transcript.v2").iterdir()) == [
        "current.json", "g-fixture.jsonl"
    ]


@pytest.mark.parametrize("field,value", [
    ("v", True), ("v", 99), ("committed_bytes", True),
    ("message_count", 3), ("committed_bytes", 99999), ("sha256", "f" * 64),
    ("body", "../other.jsonl"), ("generation", "../other"),
    ("min_reader", ["unknown"]), ("sequence", -1),
])
def test_bad_descriptor_never_falls_back_to_legacy(tmp_path, field, value):
    _, descriptor, _ = fixture(tmp_path)
    descriptor[field] = value
    (tmp_path / "transcript.v2" / "current.json").write_text(json.dumps(descriptor))
    (tmp_path / "transcript.jsonl.backup").write_text('{"role":"user","content":"stale"}\n')
    with pytest.raises(UnsupportedTranscript):
        list(CommittedTranscriptReader(tmp_path).iter_messages())


def test_hash_valid_but_invalid_row_is_not_accepted(tmp_path):
    _, descriptor, body = fixture(tmp_path)
    raw = body.read_bytes() + b'{"kind":"message","message":{"role":"user"}}\n'
    body.write_bytes(raw)
    descriptor.update(committed_bytes=len(raw), message_count=3,
                      sha256=hashlib.sha256(raw).hexdigest())
    (tmp_path / "transcript.v2" / "current.json").write_text(json.dumps(descriptor))
    with pytest.raises(UnsupportedTranscript):
        list(CommittedTranscriptReader(tmp_path).iter_messages())


@pytest.mark.parametrize("name", ["current.json", "g-fixture.jsonl"])
def test_symlink_leaf_refused(tmp_path, name):
    fixture(tmp_path)
    folder = tmp_path / "transcript.v2"
    path = folder / name
    real = folder / "elsewhere"
    path.rename(real)
    path.symlink_to(real)
    with pytest.raises(UnsupportedTranscript):
        list(CommittedTranscriptReader(tmp_path).iter_messages())


def test_descriptor_changed_during_iteration_refuses(tmp_path, monkeypatch):
    _, descriptor, _ = fixture(tmp_path)
    reader = CommittedTranscriptReader(tmp_path)
    original = reader._iterate

    def changing(prefix):
        for message in original(prefix):
            descriptor["sequence"] += 1
            (tmp_path / "transcript.v2" / "current.json").write_text(json.dumps(descriptor))
            yield message

    monkeypatch.setattr(reader, "_iterate", changing)
    with pytest.raises(UnsupportedTranscript):
        reader.iter_messages()


def test_body_changed_between_verification_and_iteration_refuses(tmp_path, monkeypatch):
    _, descriptor, body = fixture(tmp_path)
    reader = CommittedTranscriptReader(tmp_path)
    original = reader.verify

    def verify_then_change():
        prefix = original()
        body.write_bytes(body.read_bytes().replace(b"question", b"tampered"))
        return prefix

    monkeypatch.setattr(reader, "verify", verify_then_change)
    with pytest.raises(UnsupportedTranscript):
        reader.iter_messages()  # Refuses before even the first row is exposed.


def test_committed_partial_row_is_not_a_commit(tmp_path):
    _, descriptor, body = fixture(tmp_path)
    raw = body.read_bytes().rstrip(b"\n")
    body.write_bytes(raw)
    descriptor.update(committed_bytes=len(raw), sha256=hashlib.sha256(raw).hexdigest())
    (tmp_path / "transcript.v2" / "current.json").write_text(json.dumps(descriptor))
    with pytest.raises(UnsupportedTranscript):
        list(CommittedTranscriptReader(tmp_path).iter_messages())


def test_public_readers_preserve_v1_and_recognize_v2_without_backup_fallback(tmp_path):
    from amplifier_foundation.session.history import SessionHistoryStore
    from amplifier_foundation.session.store import load_transcript

    legacy = tmp_path / "legacy"
    legacy.mkdir()
    rows = [{"role": "user", "content": "old-format"}]
    SessionHistoryStore(legacy).save(rows, {"session_id": "legacy"})
    assert load_transcript(legacy) == rows
    assert SessionHistoryStore(legacy).indexed_messages()[0] == rows[0]
    versioned = tmp_path / "versioned"
    versioned.mkdir()
    expected, _, _ = fixture(versioned)
    (versioned / "transcript.jsonl.backup").write_text(
        '{"role":"user","content":"stale-legacy"}\n'
    )
    store = SessionHistoryStore(versioned)
    assert store.exists()
    assert store.load_messages() == expected
    assert store.indexed_messages() == expected
    assert store.load(include_events=False).messages == expected
    assert load_transcript(versioned) == expected


@pytest.mark.parametrize("operation", [
    "save", "incremental", "save_messages", "repair_read", "repair_write",
])
def test_public_legacy_writers_and_repair_refuse_versioned(tmp_path, operation):
    from amplifier_foundation.session.history import SessionHistoryStore
    from amplifier_foundation.session.store import load_transcript_with_lines, write_transcript

    fixture(tmp_path)
    legacy = tmp_path / "transcript.jsonl"
    legacy.mkdir()
    (legacy / "format.json").write_text("{}")
    before = {str(path): path.read_bytes() for path in tmp_path.rglob("*") if path.is_file()}
    store = SessionHistoryStore(tmp_path)
    with pytest.raises(UnsupportedTranscript):
        if operation == "save":
            store.save([], {})
        elif operation == "incremental":
            store.save([], {}, incremental=True)
        elif operation == "save_messages":
            store.save_messages([])
        elif operation == "repair_read":
            load_transcript_with_lines(tmp_path)
        else:
            write_transcript(tmp_path, [])
    assert {str(path): path.read_bytes() for path in tmp_path.rglob("*")
            if path.is_file()} == before


def test_broken_versioned_pointer_is_not_empty_legacy_history(tmp_path):
    from amplifier_foundation.session.history import SessionHistoryStore

    (tmp_path / "transcript.v2").symlink_to(tmp_path / "missing")
    assert SessionHistoryStore(tmp_path).exists()
    with pytest.raises(UnsupportedTranscript):
        SessionHistoryStore(tmp_path).load_messages()


def test_observed_v2_disappearance_never_reselects_stale_legacy(tmp_path, monkeypatch):
    from amplifier_foundation.session.history import SessionHistoryStore

    fixture(tmp_path)
    (tmp_path / "transcript.jsonl").write_text('{"role":"user","content":"stale"}\n')
    store = SessionHistoryStore(tmp_path)
    original = store._versioned_reader

    def selected_then_move():
        selected = original()
        (tmp_path / "transcript.v2").rename(tmp_path / "retained-v2")
        return selected

    monkeypatch.setattr(store, "_versioned_reader", selected_then_move)
    with pytest.raises(UnsupportedTranscript):
        store.load(include_events=False)


def test_keyword_and_turn_timestamp_never_consult_stale_legacy(tmp_path):
    from amplifier_foundation.session.finder import _transcript_contains_keyword
    from amplifier_foundation.session.events import get_last_timestamp_for_turn

    rows = [
        {"role": "user", "content": "versioned-keyword", "timestamp": "2026-01-01"},
        {"role": "assistant", "content": "reply", "timestamp": "2026-01-02"},
    ]
    fixture(tmp_path, rows=rows)
    legacy = tmp_path / "transcript.jsonl"
    legacy.write_text('{"role":"user","content":"stale-keyword","timestamp":"old"}\n')
    assert _transcript_contains_keyword(tmp_path, "versioned-keyword")
    assert not _transcript_contains_keyword(tmp_path, "stale-keyword")
    assert get_last_timestamp_for_turn(legacy, 1) == "2026-01-02"