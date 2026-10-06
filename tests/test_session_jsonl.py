"""Incremental persistence, lazy reads, and external-writer/crash recovery."""

import json
import os
from pathlib import Path

import pytest
from amplifier_foundation.session import jsonl
from amplifier_foundation.session.history import SessionHistoryStore, associate_events


def rows(n):
    return [
        {"role": "user" if i % 2 == 0 else "assistant", "content": "x" * 1000 + str(i)}
        for i in range(n)
    ]


def save(store, messages):
    store.save(messages, {"test": True}, incremental=True)


def test_append_noop_and_backup_advance_without_prefix_reads(tmp_path, monkeypatch):
    store = SessionHistoryStore(tmp_path)
    data = rows(10)
    save(store, data)
    save(store, data + rows(1))  # Establish indexes and first backup.
    old_primary = store.transcript_path.read_bytes()
    inode = store.transcript_path.stat().st_ino
    original = Path.open
    reads = []

    def opened(path, mode="r", *args, **kwargs):
        if (
            path.name in {"transcript.jsonl", "transcript.jsonl.backup"}
            and mode == "rb"
        ):
            reads.append(path)
        return original(path, mode, *args, **kwargs)

    monkeypatch.setattr(Path, "open", opened)
    save(store, data + rows(2))
    assert store.transcript_path.stat().st_ino == inode
    # One bounded copy of the previously appended tail; no primary/backup parse.
    assert reads == [store.transcript_path]
    assert (
        store.transcript_path.with_suffix(".jsonl.backup").read_bytes() == old_primary
    )
    stamp = store.transcript_path.stat()
    save(store, data + rows(2))
    assert store.transcript_path.stat() == stamp
    assert store.load_messages() == data + rows(2)


def test_edits_truncation_and_in_place_mutation_are_not_missed(tmp_path):
    store = SessionHistoryStore(tmp_path)
    data = rows(10)
    save(store, data)
    index = store.indexed_messages()
    assert index[-1] == data[-1]
    changed = [*data]
    changed[0] = {"role": "user", "content": "different"}
    save(store, changed)
    assert store.load_messages() == changed
    with pytest.raises(ValueError, match="changed"):
        _ = index[0]
    save(store, changed[:2])
    assert store.load_messages() == changed[:2]
    path = store.transcript_path
    old = path.stat()
    path.write_text(path.read_text().replace("different", "DIFFERENT"))
    os.utime(path, ns=(old.st_atime_ns, old.st_mtime_ns))
    assert store.indexed_messages()[0]["content"] == "DIFFERENT"


def test_external_cli_append_and_replace_rebuild_cache(tmp_path):
    store = SessionHistoryStore(tmp_path)
    save(store, rows(2))
    original = store.indexed_messages()
    with store.transcript_path.open("a") as stream:
        stream.write(json.dumps(rows(3)[2]) + "\n")
    assert list(store.indexed_messages()) == rows(3)
    SessionHistoryStore(tmp_path).save(rows(4), {})  # Unoptimized CLI path.
    assert list(store.indexed_messages()) == rows(4)
    with pytest.raises(ValueError):
        _ = original[:]


@pytest.mark.parametrize("complete_line", [False, True])
def test_crash_inside_append_never_exposes_partial_batch(
    tmp_path, monkeypatch, complete_line
):
    store = SessionHistoryStore(tmp_path)
    save(store, rows(2))
    original = jsonl._append

    def fail(path, chunks):
        if path == store.transcript_path:
            chunk = next(chunks)
            original(path, iter([chunk if complete_line else chunk[:10]]))
            raise OSError("simulated crash")
        original(path, chunks)

    monkeypatch.setattr(jsonl, "_append", fail)
    with pytest.raises(OSError):
        save(store, rows(4))
    assert jsonl.intent_path(store.transcript_path).exists()
    assert store.load_messages() == rows(2)
    assert "interrupted_append" in [d.code for d in store.diagnostics]
    assert list(store.indexed_messages()) == rows(2)
    monkeypatch.setattr(jsonl, "_append", original)
    save(store, rows(4))
    assert store.load_messages() == rows(4)
    assert not jsonl.intent_path(store.transcript_path).exists()


def test_legacy_writer_can_repair_interrupted_append(tmp_path):
    store = SessionHistoryStore(tmp_path)
    save(store, rows(2))
    save(store, rows(3))
    jsonl.intent_path(store.transcript_path).write_text("{}")
    store.save(rows(4), {})
    assert store.load_messages() == rows(4)
    assert not jsonl.intent_path(store.transcript_path).exists()


def test_bad_late_row_or_metadata_does_not_mutate_files(tmp_path):
    store = SessionHistoryStore(tmp_path)
    save(store, rows(2))
    before = store.transcript_path.read_bytes()
    for data, metadata in (
        (rows(2) + [{"role": "assistant", "content": float("nan")}], {}),
        (rows(3), {"bad": object()}),
    ):
        with pytest.raises((ValueError, TypeError)):
            store.save(data, metadata, incremental=True)
        assert store.transcript_path.read_bytes() == before
        assert not jsonl.intent_path(store.transcript_path).exists()


def test_unterminated_cli_last_line_is_replaced_not_joined(tmp_path):
    store = SessionHistoryStore(tmp_path)
    store.transcript_path.write_text(json.dumps(rows(1)[0]))
    save(store, rows(2))
    assert store.load_messages() == rows(2)


def test_warm_page_decodes_only_requested_bodies_and_updates_projection(
    tmp_path, monkeypatch
):
    from amplifier_foundation.session import history

    store = SessionHistoryStore(tmp_path)
    save(store, rows(100))
    index = store.indexed_messages()
    assert len(index.project("roles", lambda row, i: row["role"])) == 100
    decode = history._decode
    calls = []

    def counted(raw):
        calls.append(len(raw))
        return decode(raw)

    monkeypatch.setattr(history, "_decode", counted)
    assert store.indexed_messages()[-5:] == rows(100)[-5:]
    assert len(calls) == 5
    save(store, rows(101))
    calls.clear()
    assert (
        len(store.indexed_messages().project("roles", lambda row, i: row["role"]))
        == 101
    )
    assert len(calls) == 1


def test_indexed_association_matches_complete_history_without_retaining_prompts(
    tmp_path,
):
    store = SessionHistoryStore(tmp_path)
    messages = rows(6)
    events = [
        {"event": "prompt:submit", "session_id": "s", "data": {"prompt": m["content"]}}
        for m in messages
        if m["role"] == "user"
    ]
    save(store, messages)
    index = store.indexed_messages()
    assert associate_events(index, events) == associate_events(messages, events)
    assert messages[0]["content"] not in repr(index._projections)


def test_cache_is_bounded_and_evicts_without_changing_data(tmp_path, monkeypatch):
    monkeypatch.setattr(jsonl, "_MAX_ROWS", 5)
    jsonl._CACHE.clear()
    for i in range(4):
        store = SessionHistoryStore(tmp_path / str(i))
        save(store, rows(3))
        assert list(store.indexed_messages()) == rows(3)
    assert sum(len(item) for item in jsonl._CACHE.values()) <= 5


def test_compact_event_associations_keep_identity_without_prompt_payload(tmp_path):
    from amplifier_foundation.session.history import event_association_record

    store = SessionHistoryStore(tmp_path)
    messages = rows(20)
    save(store, messages)
    events = [
        {"event": "prompt:submit", "session_id": "s", "data": {"prompt": m["content"]}}
        for m in messages
        if m["role"] == "user"
    ]
    compact = [event_association_record(e) for e in events]
    assert associate_events(store.indexed_messages(), compact) == associate_events(
        messages, events
    )
    assert all("prompt" not in event["data"] for event in compact)
    assert sum(len(event["data"]["_prompt_digest"]) for event in compact) == 320


def test_hardlinked_backup_does_not_append_to_primary(tmp_path):
    store = SessionHistoryStore(tmp_path)
    save(store, rows(2))
    backup = store.transcript_path.with_suffix(".jsonl.backup")
    os.link(store.transcript_path, backup)
    save(store, rows(3))
    assert store.load_messages() == rows(3)
    assert [json.loads(line) for line in backup.read_text().splitlines()] == rows(2)
