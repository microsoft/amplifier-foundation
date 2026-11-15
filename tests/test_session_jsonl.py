"""Incremental persistence, lazy reads, and external-writer/crash recovery."""

import json
import os
from pathlib import Path

import pytest
from amplifier_foundation.session import jsonl
from amplifier_foundation.session.history import SessionHistoryStore, associate_events


@pytest.fixture
def tmp_path(tmp_path):
    if os.name == "nt":
        from amplifier_foundation.session import _windows_ownership as win

        directory = tmp_path / "private-history"
        with win._pinned(win._API(), directory, root=directory, create=True):
            pass
        return directory
    return tmp_path


def rows(n):
    return [
        {"role": "user" if i % 2 == 0 else "assistant", "content": "x" * 1000 + str(i)}
        for i in range(n)
    ]


def save(store, messages):
    store.save(messages, {"test": True}, incremental=True)


@pytest.mark.parametrize("prepare_index", [False, True])
@pytest.mark.parametrize("warm", [False, True])
def test_resume_load_and_first_save_share_only_prepared_source_index(
    tmp_path, monkeypatch, prepare_index, warm
):
    from amplifier_foundation.session import history

    store = SessionHistoryStore(tmp_path, windows_policy="private")
    data = rows(8)
    store.save(data, {"unknown": {"retained": [1, 2]}})
    jsonl._CACHE.clear()
    if warm:
        store.indexed_messages()
    original_bytes = store.transcript_path.read_bytes()
    original_stamp = jsonl.file_stamp(store.transcript_path, windows_policy="private")
    decode, digest = history._decode, jsonl._digest
    decoded, digested = [], []

    def counted_decode(raw):
        value = decode(raw)
        if isinstance(value, dict) and "role" in value:
            decoded.append(len(raw))
        return value

    def counted_digest(value):
        digested.append(value["role"])
        return digest(value)

    monkeypatch.setattr(history, "_decode", counted_decode)
    monkeypatch.setattr(jsonl, "_digest", counted_digest)
    loaded = store.load(include_events=False, prepare_index=prepare_index)
    assert loaded.messages == data
    assert loaded.metadata == {"unknown": {"retained": [1, 2]}}
    assert not loaded.diagnostics
    assert len(decoded) == len(data)
    assert len(digested) == (len(data) if prepare_index and not warm else 0)
    store.save(loaded.messages, loaded.metadata, incremental=True)
    assert len(decoded) == len(data) * (2 if not prepare_index and not warm else 1)
    assert store.transcript_path.read_bytes() == original_bytes
    assert jsonl.file_stamp(store.transcript_path, windows_policy="private") == original_stamp


def test_resume_prepared_index_preserves_append_and_borrowed_nested_edit(tmp_path):
    store = SessionHistoryStore(tmp_path, windows_policy="private")
    data = rows(3)
    data[0]["continuation"] = {"nested": [{"value": True}]}
    store.save(data, {})
    jsonl._CACHE.clear()
    loaded = store.load(include_events=False, prepare_index=True)
    before_append = store.transcript_path.read_bytes()
    loaded.messages.append({"role": "assistant", "content": "next"})
    save(store, loaded.messages)
    assert store.transcript_path.with_suffix(".jsonl.backup").read_bytes() == before_append
    assert store.load_messages() == loaded.messages
    before_edit = store.transcript_path.read_bytes()
    # Python equality treats True == 1; canonical bytes must still detect this.
    loaded.messages[0]["continuation"]["nested"][0]["value"] = 1
    save(store, loaded.messages)
    assert store.transcript_path.read_bytes() != before_edit
    assert store.transcript_path.with_suffix(".jsonl.backup").read_bytes() == before_edit
    assert type(store.load_messages()[0]["continuation"]["nested"][0]["value"]) is int


def test_resume_preparation_stops_at_existing_row_budget(tmp_path, monkeypatch):
    store = SessionHistoryStore(tmp_path, windows_policy="private")
    store.save(rows(4), {})
    jsonl._CACHE.clear()
    monkeypatch.setattr(jsonl, "_MAX_ROWS", 2)
    digest = jsonl._digest
    digested = []

    def counted(value):
        digested.append(value["role"])
        return digest(value)

    monkeypatch.setattr(jsonl, "_digest", counted)
    loaded = store.load(include_events=False, prepare_index=True)
    assert loaded.messages == rows(4)
    assert len(digested) == 2
    assert not jsonl._CACHE
    save(store, loaded.messages)
    assert store.load_messages() == rows(4)
    assert not jsonl._CACHE


@pytest.mark.parametrize("pending", [False, True])
def test_resume_preparation_keeps_recovered_backup_separate(tmp_path, pending):
    store = SessionHistoryStore(tmp_path, windows_policy="private")
    store.save(rows(2), {})
    store.save(rows(3), {})
    if pending:
        jsonl.intent_path(store.transcript_path).write_text("{}")
    else:
        store.transcript_path.write_bytes(b"invalid primary\n")
    jsonl._CACHE.clear()
    loaded = store.load(include_events=False, prepare_index=True)
    assert loaded.messages == rows(2)
    assert "recovered_backup" in {item.code for item in loaded.diagnostics}
    primary = (store.transcript_path.absolute(), "private")
    backup = (store.transcript_path.with_suffix(".jsonl.backup").absolute(), "private")
    assert primary not in jsonl._CACHE
    assert backup in jsonl._CACHE
    save(store, loaded.messages)
    assert store.load_messages() == rows(2)
    assert not jsonl.intent_path(store.transcript_path).exists()


def test_resume_preparation_declines_changed_source_without_changing_read(tmp_path, monkeypatch):
    from amplifier_foundation.session import history

    store = SessionHistoryStore(tmp_path, windows_policy="private")
    store.save(rows(2), {})
    jsonl._CACHE.clear()
    decode = history._decode
    changed = False
    extra = {"role": "assistant", "content": "external append"}

    def append_during_read(raw):
        nonlocal changed
        value = decode(raw)
        if isinstance(value, dict) and "role" in value and not changed:
            changed = True
            with store.transcript_path.open("ab") as stream:
                stream.write(json.dumps(extra).encode() + b"\n")
        return value

    monkeypatch.setattr(history, "_decode", append_during_read)
    loaded = store.load(include_events=False, prepare_index=True)
    assert loaded.messages == rows(2) + [extra]
    assert "changed_during_read" in {item.code for item in loaded.diagnostics}
    assert (store.transcript_path.absolute(), "private") not in jsonl._CACHE
    assert list(store.indexed_messages()) == loaded.messages


@pytest.mark.parametrize("value", [b'"\\ud800"', b"1e400"])
def test_resume_preparation_does_not_reject_readable_unwritable_values(tmp_path, value):
    store = SessionHistoryStore(tmp_path, windows_policy="private")
    store.save(rows(1), {"unknown": ["keep"]})
    raw = b'{"role":"assistant","content":' + value + b"}\n"
    store.transcript_path.write_bytes(raw)
    jsonl._CACHE.clear()
    expected = store.load(include_events=False)
    loaded = store.load(include_events=False, prepare_index=True)
    assert loaded.messages == expected.messages
    assert loaded.diagnostics == expected.diagnostics == []
    assert (store.transcript_path.absolute(), "private") not in jsonl._CACHE
    metadata = store.metadata_path.read_bytes()
    backup = store.transcript_path.with_suffix(".jsonl.backup")
    assert not backup.exists()
    with pytest.raises((ValueError, UnicodeError)):
        save(store, loaded.messages)
    assert store.transcript_path.read_bytes() == raw
    assert store.metadata_path.read_bytes() == metadata
    assert not backup.exists()


@pytest.mark.parametrize("value", [b"NaN", b"Infinity", b"-Infinity"])
def test_resume_preparation_retains_invalid_json_recovery(tmp_path, value):
    store = SessionHistoryStore(tmp_path, windows_policy="private")
    store.save(rows(1), {})
    store.save(rows(2), {})
    store.transcript_path.write_bytes(b'{"role":"assistant","content":' + value + b"}\n")
    jsonl._CACHE.clear()
    loaded = store.load(include_events=False, prepare_index=True)
    assert loaded.messages == rows(1)
    assert "recovered_backup" in {item.code for item in loaded.diagnostics}


@pytest.mark.parametrize("raw", [b"", b"\n \n", b'\n{"role":"user","content":"x"}\r\n\n', b'{"role":"user","content":"x"}'])
def test_resume_prepared_index_preserves_physical_offsets_and_termination(tmp_path, raw):
    store = SessionHistoryStore(tmp_path, windows_policy="private")
    store.save([], {})
    store.transcript_path.write_bytes(raw)
    jsonl._CACHE.clear()
    loaded = store.load(include_events=False, prepare_index=True)
    prepared = store.indexed_messages()
    jsonl._CACHE.clear()
    independent = store.indexed_messages()
    assert prepared.rows == independent.rows
    assert prepared.terminated == independent.terminated
    assert list(prepared) == loaded.messages


def test_append_noop_and_backup_advance_without_prefix_reads(tmp_path, monkeypatch):
    store = SessionHistoryStore(tmp_path, windows_policy="private")
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
    if os.name == "nt":
        from amplifier_foundation.session import _windows_history as backend
        from contextlib import contextmanager

        original_native = backend.open_binary

        @contextmanager
        def opened_native(path):
            if path.name in {"transcript.jsonl", "transcript.jsonl.backup"}:
                reads.append(path)
            with original_native(path) as stream:
                yield stream

        monkeypatch.setattr(backend, "open_binary", opened_native)
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
    store = SessionHistoryStore(tmp_path, windows_policy="private")
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
    store = SessionHistoryStore(tmp_path, windows_policy="private")
    save(store, rows(2))
    original = store.indexed_messages()
    with store.transcript_path.open("a") as stream:
        stream.write(json.dumps(rows(3)[2]) + "\n")
    assert list(store.indexed_messages()) == rows(3)
    SessionHistoryStore(tmp_path, windows_policy="private").save(rows(4), {})  # Unoptimized host path.
    assert list(store.indexed_messages()) == rows(4)
    with pytest.raises(ValueError):
        _ = original[:]


@pytest.mark.parametrize("complete_line", [False, True])
def test_crash_inside_append_never_exposes_partial_batch(
    tmp_path, monkeypatch, complete_line
):
    store = SessionHistoryStore(tmp_path, windows_policy="private")
    save(store, rows(2))
    original = jsonl._append

    def fail(path, chunks, **kwargs):
        if path == store.transcript_path:
            chunk = next(chunks)
            original(path, iter([chunk if complete_line else chunk[:10]]), **kwargs)
            raise OSError("simulated crash")
        original(path, chunks, **kwargs)

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
    store = SessionHistoryStore(tmp_path, windows_policy="private")
    save(store, rows(2))
    save(store, rows(3))
    if os.name == "nt":
        from amplifier_foundation.session._windows_history import write_pending

        write_pending(jsonl.intent_path(store.transcript_path))
    else:
        jsonl.intent_path(store.transcript_path).write_text("{}")
    store.save(rows(4), {})
    assert store.load_messages() == rows(4)
    assert not jsonl.intent_path(store.transcript_path).exists()


def test_bad_late_row_or_metadata_does_not_mutate_files(tmp_path):
    store = SessionHistoryStore(tmp_path, windows_policy="private")
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
    store = SessionHistoryStore(tmp_path, windows_policy="private")
    if os.name == "nt":
        from amplifier_foundation.session._windows_history import _replace

        _replace(store.transcript_path, [json.dumps(rows(1)[0]).encode()])
    else:
        store.transcript_path.write_text(json.dumps(rows(1)[0]))
    save(store, rows(2))
    assert store.load_messages() == rows(2)


def test_warm_page_decodes_only_requested_bodies_and_updates_projection(
    tmp_path, monkeypatch
):
    from amplifier_foundation.session import history

    store = SessionHistoryStore(tmp_path, windows_policy="private")
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
    store = SessionHistoryStore(tmp_path, windows_policy="private")
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
        store = SessionHistoryStore(tmp_path / str(i), windows_policy="private")
        save(store, rows(3))
        assert list(store.indexed_messages()) == rows(3)
    assert sum(len(item) for item in jsonl._CACHE.values()) <= 5


def test_compact_event_associations_keep_identity_without_prompt_payload(tmp_path):
    from amplifier_foundation.session.history import event_association_record

    store = SessionHistoryStore(tmp_path, windows_policy="private")
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
    store = SessionHistoryStore(tmp_path, windows_policy="private")
    save(store, rows(2))
    backup = store.transcript_path.with_suffix(".jsonl.backup")
    os.link(store.transcript_path, backup)
    if os.name == "nt":
        from amplifier_foundation.session.shared_state import SharedStateError

        with pytest.raises(SharedStateError, match="hardlink"):
            save(store, rows(3))
        return
    save(store, rows(3))
    assert store.load_messages() == rows(3)
    assert [json.loads(line) for line in backup.read_text().splitlines()] == rows(2)


def _fixture_bytes(path, content):
    if os.name == "nt":
        from amplifier_foundation.session._windows_history import _replace

        _replace(path, [content])
    else:
        path.write_bytes(content)


def _fixture_pending(path):
    if os.name == "nt":
        from amplifier_foundation.session._windows_history import write_pending

        write_pending(path)
    else:
        path.write_text('{"version":1}\n')


def test_replacement_reuses_over_budget_index_without_losing_nested_edit(tmp_path, monkeypatch):
    store = SessionHistoryStore(tmp_path, windows_policy="private")
    data = rows(3)
    data[-1]["metadata"] = {"nested": {"value": "before"}}
    save(store, data)
    before = store.transcript_path.read_bytes()
    monkeypatch.setattr(jsonl, "_MAX_ROWS", 1)
    calls = []
    digest = jsonl._digest

    def counted(value):
        calls.append(value["role"])
        return digest(value)

    monkeypatch.setattr(jsonl, "_digest", counted)
    data[-1]["metadata"]["nested"]["value"] = "AFTER!"
    save(store, data)
    assert calls == [row["role"] for row in data]  # One disk validation, not two.
    assert store.load_messages() == data
    assert store.transcript_path.stat().st_size == len(before)
    assert store.transcript_path.with_suffix(".jsonl.backup").read_bytes() == before
    assert not any(key[0] == store.transcript_path.absolute() for key in jsonl._CACHE)
    assert jsonl.append_messages(store.transcript_path, data, windows_policy="private") is True
    assert jsonl.append_messages(store.transcript_path, [], windows_policy="private") is False


@pytest.mark.parametrize("mutation", ["same-size", "corrupt", "missing", "pending"])
def test_replacement_revalidates_changed_primary_and_preserves_recovery(tmp_path, monkeypatch, mutation):
    store = SessionHistoryStore(tmp_path, windows_policy="private")
    old, current = rows(2), rows(3)
    store.save(old, {})
    store.save(current, {})  # Previous complete backup differs from the primary.
    path = store.transcript_path
    backup = path.with_suffix(".jsonl.backup")
    backup_before = backup.read_bytes()
    source_before = path.read_bytes()
    stamp = path.stat()
    candidate = rows(3)
    candidate[-1]["content"] += " edited"
    original = store._messages_content
    observations = []

    def encode_then_change(*args):
        content = original(*args)
        if mutation == "same-size":
            # Preserve size and mtime while changing the actual prefix.
            _fixture_bytes(path, source_before.replace(b"xxx", b"XXX", 1))
            os.utime(path, ns=(stamp.st_atime_ns, stamp.st_mtime_ns))
        elif mutation == "corrupt":
            _fixture_bytes(path, b"invalid JSON\n")
        elif mutation == "missing":
            path.unlink()
        else:
            # Intent changes without changing the primary's stamp.
            _fixture_pending(jsonl.intent_path(path))
        return content

    real_indexed = jsonl.indexed

    def indexed(path, **kwargs):
        observations.append(path)
        return real_indexed(path, **kwargs)

    monkeypatch.setattr(store, "_messages_content", encode_then_change)
    monkeypatch.setattr(jsonl, "indexed", indexed)
    save(store, candidate)
    assert store.load_messages() == candidate
    if mutation == "same-size":
        assert observations == [path, path]
        assert backup.read_bytes() == source_before.replace(b"xxx", b"XXX", 1)
    elif mutation == "pending":
        assert observations == [path, backup]
        assert backup.read_bytes() == backup_before
    elif mutation == "corrupt":
        assert observations == [path, path, backup]
        assert backup.read_bytes() == backup_before
    else:
        assert observations == [path, path]
        assert backup.read_bytes() == backup_before
    assert not jsonl.intent_path(path).exists()


@pytest.mark.parametrize("mismatch", ["path", "policy"])
def test_replacement_witness_cannot_validate_another_path_or_policy(tmp_path, monkeypatch, mismatch):
    store = SessionHistoryStore(tmp_path, windows_policy="private")
    save(store, rows(2))
    current = store.indexed_messages()
    witness = jsonl.TranscriptIndex(
        tmp_path / "other.jsonl" if mismatch == "path" else current.path,
        current.stamp, current.rows, current.terminated,
        windows_policy="legacy" if mismatch == "policy" else "private",
    )
    calls = []

    def reader(path, **kwargs):
        calls.append((path, kwargs))
        return jsonl.indexed(path, **kwargs)

    assert store._prepare_write(store.transcript_path, reader, validated=witness) is False
    assert calls == [(store.transcript_path, {"windows_policy": "private"})]


def test_replacement_validated_source_does_not_skip_invalid_late_input(tmp_path):
    store = SessionHistoryStore(tmp_path, windows_policy="private")
    store.save(rows(2), {})
    store.save(rows(3), {})
    paths = [store.transcript_path, store.transcript_path.with_suffix(".jsonl.backup"),
             store.metadata_path, store.metadata_path.with_suffix(".json.backup")]
    before = [path.read_bytes() for path in paths]
    invalid = rows(3)
    invalid[0]["content"] = "prefix edit"
    invalid[-1]["metadata"] = {"nested": {"invalid": float("nan")}}
    with pytest.raises(ValueError):
        save(store, invalid)
    assert [path.read_bytes() for path in paths] == before
    assert not jsonl.intent_path(store.transcript_path).exists()
