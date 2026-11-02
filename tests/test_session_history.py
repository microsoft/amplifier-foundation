"""Native history compatibility, strict recovery, and conservative CI enrichment."""

from __future__ import annotations

import json
import os
from dataclasses import asdict
from pathlib import Path

import pytest
from amplifier_foundation.session import (
    SessionHistoryError,
    SessionHistoryStore,
    associate_events,
)
from amplifier_foundation.session import history as history_module

USER = {"role": "user", "content": "hello"}
ANSWER = {"role": "assistant", "content": "world"}


def fixture_directory(path):
    if os.name == "nt":
        from amplifier_foundation.session import _windows_ownership as win

        with win._pinned(win._API(), path, root=path, create=True):
            pass
    else:
        path.mkdir(parents=True, exist_ok=True)


def fixture_bytes(path, data):
    """Create synthetic fixtures with the native privacy contract, not chmod."""
    if os.name == "nt" and not path.exists():
        from amplifier_foundation.session import _windows_history as backend

        fixture_directory(path.parent)
        api = backend._API()
        handle = api.open(path, exclusive=True, write=True)
        try:
            api.write_chunks(handle, [data])
        finally:
            api.close(handle)
    else:
        path.write_bytes(data)


def write_events(store: SessionHistoryStore, events: list[dict]) -> None:
    fixture_directory(store.events_path.parent)
    fixture_bytes(store.events_path, "".join(json.dumps(event) + "\n" for event in events).encode())


def event(kind: str, **data) -> dict:
    return {
        "event": kind,
        "timestamp": "2026-09-19T12:00:00Z",
        "data": {"session_id": "session", **data},
    }


def normalized(kind: str, **data) -> dict:
    return {"event": kind, "session_id": "session", "data": data}


@pytest.fixture
def store(tmp_path: Path) -> SessionHistoryStore:
    return SessionHistoryStore(tmp_path / "session", windows_policy="private")


def test_missing_session_is_empty_and_does_not_create_files(store):
    assert not store.exists()
    history = store.load()
    assert history.messages == [] and history.metadata == {} and history.events == []
    assert history.diagnostics == []
    assert all(stamp is None for stamp in history.revision.values())
    assert not store.session_dir.exists()


def test_roundtrip_preserves_continuation_reminders_and_attachments(store):
    messages = [
        USER,
        {
            "role": "user",
            "content": "<system-reminder>today</system-reminder>",
            "metadata": {
                "ephemeral": True,
                "persisted": True,
                "reminder_placement": "before_request",
                "_seq": 2,
            },
        },
        {
            "role": "assistant",
            "content": [
                {"type": "thinking", "thinking": "summary", "signature": "opaque"}
            ],
            "metadata": {
                "openai:response_id": "r1",
                "openai:reasoning_items": [{"encrypted_content": "opaque"}],
            },
        },
        {
            "role": "user",
            "content": [
                {"type": "image", "source": {"type": "base64", "data": "c2FtcGxl"}},
                {"type": "text", "text": "look"},
            ],
        },
    ]
    metadata = {
        "session_id": "session",
        "bundle": "example",
        "unknown_host_field": {"a": 1},
    }
    store.save(messages, metadata)
    loaded = store.load()
    assert loaded.messages == messages and loaded.metadata == metadata
    assert store.exists()
    assert {path.name for path in store.session_dir.iterdir()} == {
        "transcript.jsonl",
        "metadata.json",
    }


def test_cli_default_system_filter_and_optional_preservation(store):
    messages = [
        {"role": "system", "content": "system"},
        {"role": "developer", "content": "context"},
        USER,
        ANSWER,
    ]
    store.save(messages, {})
    assert store.load_messages() == [USER, ANSWER]
    store.save_messages(messages, preserve_system=True)
    assert store.load_messages() == messages


def test_host_sanitizer_is_explicit_and_metadata_is_replaced(store):
    store.save([USER], {"host": "old"})
    store.save(
        [USER],
        {"other_host": "new"},
        sanitizer=lambda message: {**message, "content": "sanitized"},
    )
    assert store.load_metadata() == {"other_host": "new"}
    assert store.load_messages() == [{"role": "user", "content": "sanitized"}]
    assert USER["content"] == "hello"


def test_model_dump_input_is_supported(store):
    class Message:
        def model_dump(self):
            return USER

    store.save_messages([Message()])
    assert store.load_messages() == [USER]


@pytest.mark.parametrize(
    "messages",
    [
        [None],
        ["not a message"],
        [{"content": "missing role"}],
        [{"role": ""}],
        [{"role": 4}],
        [{"role": "user", "content": object()}],
        [{"role": "user", "content": float("nan")}],
        {},
    ],
)
def test_invalid_messages_never_create_partial_session(store, messages):
    with pytest.raises(ValueError):
        store.save(messages, {})
    assert not store.session_dir.exists()


@pytest.mark.parametrize(
    "metadata", [None, [], {"invalid": object()}, {"invalid": float("inf")}]
)
def test_invalid_metadata_is_validated_before_transcript_write(store, metadata):
    store.save([USER], {"old": True})
    before = {p.name: p.read_bytes() for p in store.session_dir.iterdir()}
    with pytest.raises(ValueError):
        store.save([ANSWER], metadata)
    assert before == {p.name: p.read_bytes() for p in store.session_dir.iterdir()}


def test_invalid_sanitizer_output_is_rejected(store):
    with pytest.raises(ValueError):
        store.save_messages([USER], sanitizer=lambda _: None)
    assert not store.session_dir.exists()


def test_cli_backup_filenames_and_previous_complete_content(store):
    store.save([USER], {"version": 1})
    store.save([USER, ANSWER], {"version": 2})
    assert (
        json.loads((store.session_dir / "transcript.jsonl.backup").read_text()) == USER
    )
    assert json.loads((store.session_dir / "metadata.json.backup").read_text()) == {
        "version": 1
    }
    assert {p.name for p in store.session_dir.iterdir()} == {
        "transcript.jsonl",
        "metadata.json",
        "transcript.jsonl.backup",
        "metadata.json.backup",
    }


def test_incremental_message_and_metadata_writes_do_not_touch_other_file(store):
    store.save([USER], {"name": "old"})
    metadata_stamp = store.metadata_path.stat()
    store.save_messages([USER, ANSWER])
    assert store.metadata_path.stat() == metadata_stamp
    transcript_stamp = store.transcript_path.stat()
    store.save_metadata({"name": "new"})
    assert store.transcript_path.stat() == transcript_stamp


@pytest.mark.parametrize(
    "bad",
    [
        b'{"role":"user","content":"private"}\nBROKEN\n',
        b"[]\n",
        b'{"role": ""}\n',
        b"\xff\n",
        b'{"role":"user","content":NaN}\n',
    ],
)
def test_corrupt_transcript_raises_instead_of_dropping_rows(store, bad):
    fixture_directory(store.session_dir)
    fixture_bytes(store.transcript_path, bad)
    with pytest.raises(SessionHistoryError) as caught:
        store.load_messages()
    assert caught.value.source == "transcript"
    assert "private" not in str(caught.value)
    assert caught.value.diagnostics[0].code == "invalid_file"
    assert caught.value.diagnostics[0].line in (1, 2)
    assert store.transcript_path.read_bytes() == bad


def test_missing_primary_reads_backup_without_restoring_or_touching_files(store):
    fixture_directory(store.session_dir)
    backup = store.session_dir / "transcript.jsonl.backup"
    fixture_bytes(backup, (json.dumps(USER) + "\n").encode())
    stamp = backup.stat()
    assert store.exists()
    assert store.load_messages() == [USER]
    assert not store.transcript_path.exists()
    assert backup.stat() == stamp
    assert [d.code for d in store.diagnostics] == ["recovered_backup"]


def test_corrupt_primary_uses_complete_backup_and_save_keeps_good_backup(store):
    store.save([USER], {})
    store.save([USER, ANSWER], {})
    backup = store.session_dir / "transcript.jsonl.backup"
    good_backup = backup.read_bytes()
    store.transcript_path.write_text("BROKEN\n")
    assert store.load_messages() == [USER]
    assert [d.code for d in store.diagnostics] == ["invalid_file", "recovered_backup"]
    assert store.transcript_path.read_text() == "BROKEN\n"
    store.save_messages([USER, ANSWER])
    assert backup.read_bytes() == good_backup
    assert store.load_messages() == [USER, ANSWER]


def test_both_bad_copies_raise_and_cannot_be_overwritten_accidentally(store):
    fixture_directory(store.session_dir)
    fixture_bytes(store.transcript_path, b"bad primary")
    backup = store.session_dir / "transcript.jsonl.backup"
    fixture_bytes(backup, b"bad backup")
    with pytest.raises(SessionHistoryError):
        store.load_messages()
    with pytest.raises(SessionHistoryError):
        store.save_messages([USER])
    assert store.transcript_path.read_text() == "bad primary"
    assert backup.read_text() == "bad backup"


def test_valid_empty_primary_is_authoritative_over_nonempty_backup(store):
    store.save([USER], {})
    store.save_messages([])
    assert store.load_messages() == []
    assert store.diagnostics == []


@pytest.mark.parametrize("bad", ["[]", "null", "BROKEN", '{"x":NaN}'])
def test_metadata_corruption_is_explicit_and_backup_recovery_is_read_only(store, bad):
    store.save([USER], {"name": "original"})
    store.save_metadata({"name": "second"})
    store.metadata_path.write_text(bad)
    assert store.load_metadata() == {"name": "original"}
    assert store.metadata_path.read_text() == bad
    (store.session_dir / "metadata.json.backup").unlink()
    with pytest.raises(SessionHistoryError):
        store.load_metadata()


def test_metadata_and_transcript_recovery_diagnostics_both_survive_combined_load(store):
    store.save([USER], {"name": "one"})
    store.save([USER, ANSWER], {"name": "two"})
    store.transcript_path.write_text("bad")
    store.metadata_path.write_text("bad")
    loaded = store.load(include_events=False)
    assert loaded.messages == [USER]
    assert loaded.metadata == {"name": "one"}
    assert [(d.source, d.code) for d in loaded.diagnostics] == [
        ("transcript", "invalid_file"),
        ("transcript", "recovered_backup"),
        ("metadata", "invalid_file"),
        ("metadata", "recovered_backup"),
    ]


def test_native_ci_path_not_legacy_root_log(store):
    store.save([USER], {})
    (store.session_dir / "events.jsonl").write_text(
        json.dumps(event("wrong:logger")) + "\n"
    )
    assert store.load().events == []
    write_events(store, [event("prompt:submit", prompt="hello")])
    assert [e["event"] for e in store.load().events] == ["prompt:submit"]


def test_relocated_ci_log_and_explicit_session_id(tmp_path):
    store = SessionHistoryStore(
        tmp_path / "arbitrary",
        events_path=tmp_path / "private-activity" / "relocated.jsonl",
        session_id="session",
        windows_policy="private",
    )
    write_events(store, [event("prompt:submit", prompt="hello")])
    assert next(store.iter_events())["session_id"] == "session"
    assert not store.session_dir.exists()


def test_resume_does_not_open_or_stat_events(store, monkeypatch):
    store.save([USER], {})
    original_open, original_stat = Path.open, Path.stat

    def checked_open(path, *args, **kwargs):
        assert path != store.events_path
        return original_open(path, *args, **kwargs)

    def checked_stat(path, *args, **kwargs):
        assert path != store.events_path
        return original_stat(path, *args, **kwargs)

    monkeypatch.setattr(Path, "open", checked_open)
    monkeypatch.setattr(Path, "stat", checked_stat)
    assert store.load_messages() == [USER]
    assert store.load_metadata() == {}
    loaded = store.load(include_events=False)
    assert loaded.events == [] and "events" not in loaded.revision


def test_events_never_invent_replace_or_reorder_transcript_messages(store):
    store.save([USER, ANSWER], {})
    write_events(
        store,
        [
            event("prompt:submit", prompt="not saved"),
            event("content_block:end", text="different answer"),
        ],
    )
    before = store.transcript_path.read_bytes()
    loaded = store.load()
    assert loaded.messages == [USER, ANSWER]
    assert len(loaded.events) == 2
    assert not any(a.message_indices for a in loaded.associations)
    assert store.transcript_path.read_bytes() == before


def test_event_normalization_preserves_payload_and_extra_envelope_fields(store):
    native = event("custom:activity", nested={"opaque": [1, 2, 3]})
    native["workspace"] = "example"
    native["custom"] = True
    write_events(store, [native])
    before = store.events_path.read_bytes()
    actual = next(store.iter_events())
    assert actual["data"] == native["data"]
    assert actual["custom"] is True and actual["workspace"] == "example"
    assert actual["line"] == 1 and actual["session_id"] == "session"
    assert store.events_path.read_bytes() == before


def test_child_events_excluded_and_conflicts_diagnosed(store):
    records = [
        event("tool:post", tool_call_id="root"),
        event("tool:post", session_id="child"),
        {**event("tool:pre"), "session_id": "child"},
        {"event": "legacy", "ts": "now", "session_id": "session", "data": {}},
        {"event": "unscoped", "data": {}},
    ]
    write_events(store, records)
    loaded = list(store.iter_events())
    assert [e["event"] for e in loaded] == ["tool:post", "legacy", "unscoped"]
    assert loaded[1]["timestamp"] == "now"
    assert [(d.code, d.line) for d in store.diagnostics] == [
        ("conflicting_session_ids", 3),
        ("unscoped_event", 5),
    ]


def test_bad_activity_rows_report_locations_and_continue_without_payload_leak(store):
    fixture_directory(store.events_path.parent)
    good = (json.dumps(event("good")) + "\n").encode()
    fixture_bytes(store.events_path,
        b"PRIVATE INVALID\n\xff\n[]\n" + good + b'{"event":"truncated'
    )
    actual = list(store.iter_events())
    assert [e["event"] for e in actual] == ["good"]
    assert [d.line for d in store.diagnostics] == [1, 2, 3, 5]
    assert store.diagnostics[-1].code == "incomplete_event"
    assert "PRIVATE" not in repr([asdict(d) for d in store.diagnostics])


def test_event_iterator_is_lazy(store):
    write_events(store, [event("first")])
    with store.events_path.open("a") as stream:
        stream.write("BAD LATER\n")
    iterator = store.iter_events()
    assert next(iterator)["event"] == "first"
    assert store.diagnostics == []
    assert list(iterator) == []
    assert store.diagnostics[0].line == 2


def test_invalid_event_data_or_identity_are_diagnosed(store):
    write_events(
        store, [{"event": "x", "data": []}, {"event": "x", "data": {"session_id": 4}}]
    )
    assert list(store.iter_events()) == []
    assert [d.code for d in store.diagnostics] == [
        "invalid_event_data",
        "invalid_session_id",
    ]


def test_changed_during_read_is_explicit_without_retry_or_overwrite(store, monkeypatch):
    store.save([USER], {})
    original = history_module.file_stamp
    calls = 0

    def changed_stamp(path, **kwargs):
        nonlocal calls
        if path == store.transcript_path:
            calls += 1
            if calls == 2:
                return None
        return original(path, **kwargs)

    monkeypatch.setattr(history_module, "file_stamp", changed_stamp)
    loaded = store.load(include_events=False)
    assert loaded.messages == [USER]
    assert [(d.code, d.source) for d in loaded.diagnostics] == [
        ("changed_during_read", "transcript")
    ]
    assert loaded.revision["transcript"] is not None


def test_parallel_tool_completion_order_associates_by_id_without_reordering():
    messages = [
        USER,
        {"role": "assistant", "tool_calls": [{"id": "a"}, {"id": "b"}]},
        {"role": "tool", "tool_call_id": "a", "content": "A"},
        {"role": "tool", "tool_call_id": "b", "content": "B"},
    ]
    events = [
        normalized("tool:pre", tool_call_id="b"),
        normalized("tool:post", tool_call_id="b"),
        normalized("tool:post", tool_call_id="a"),
    ]
    actual = associate_events(messages, events)
    assert [a.message_indices for a in actual] == [(1,), (3,), (2,)]
    assert all(a.method == "tool_call_id" and a.turn_index == 0 for a in actual)


def test_block_form_tool_ids_and_message_ids():
    messages = [
        USER,
        {
            "role": "assistant",
            "metadata": {"message_id": "m"},
            "content": [{"type": "tool_use", "id": "a"}],
        },
        {
            "role": "user",
            "content": [{"type": "tool_result", "tool_use_id": "a", "content": "done"}],
        },
    ]
    events = [
        normalized("tool:pre", tool_call_id="a"),
        normalized("tool:post", tool_call_id="a"),
        normalized("content_block:end", message_id="m"),
    ]
    actual = associate_events(messages, events)
    assert [a.message_indices for a in actual] == [(1,), (2,), (1,)]
    assert all(a.turn_index == 0 and a.turn_message_index == 0 for a in actual)


def test_reused_tool_ids_are_not_guessed():
    messages = [
        {"role": "assistant", "tool_calls": [{"id": "a"}]},
        {"role": "tool", "tool_call_id": "a", "content": "one"},
        {"role": "assistant", "tool_calls": [{"id": "a"}]},
    ]
    actual = associate_events(messages, [normalized("tool:post", tool_call_id="a")])
    assert actual[0].message_indices == () and actual[0].method is None


def test_repeated_prompts_preserved_when_full_sequences_agree():
    messages = [USER, ANSWER, USER, ANSWER]
    events = [
        normalized("prompt:submit", prompt="hello"),
        normalized("prompt:submit", prompt="hello"),
    ]
    actual = associate_events(messages, events)
    assert [a.message_indices for a in actual] == [(0,), (2,)]
    assert [a.turn_index for a in actual] == [0, 1]
    assert [a.turn_message_index for a in actual] == [
        a.message_indices[0] for a in actual
    ]


def test_partial_repeated_prompt_sequence_is_ambiguous():
    actual = associate_events(
        [USER, ANSWER, USER], [normalized("prompt:submit", prompt="hello")]
    )
    assert actual[0].message_indices == ()


def test_unique_prompt_can_associate_with_partial_log_but_activity_does_not_inherit_turn():
    messages = [USER, ANSWER, {"role": "user", "content": "next"}]
    actual = associate_events(
        messages,
        [normalized("prompt:submit", prompt="next"), normalized("llm:request")],
    )
    assert actual[0].message_indices == (2,) and actual[0].turn_index == 1
    assert actual[1].message_indices == () and actual[1].turn_index is None


def test_auxiliary_calls_unscoped_events_and_same_timestamps_do_not_associate():
    messages = [{**USER, "message_id": "m", "timestamp": "now"}]
    events = [
        normalized("llm:response", purpose="session_naming", message_id="m"),
        {"event": "prompt:submit", "timestamp": "now", "data": {"prompt": "hello"}},
        normalized("llm:response", timestamp="now"),
    ]
    actual = associate_events(messages, events)
    assert actual[0].auxiliary is True
    assert all(a.message_indices == () for a in actual)


def test_persisted_reminders_do_not_shift_human_turn_indices():
    messages = [
        USER,
        {
            "role": "user",
            "content": '<system-reminder source="clock">today</system-reminder>',
        },
        {"role": "user", "content": "opaque reminder", "metadata": {"ephemeral": True}},
        ANSWER,
        {"role": "user", "content": "next"},
    ]
    actual = associate_events(
        messages,
        [
            normalized("prompt:submit", prompt="hello"),
            normalized("prompt:submit", prompt="next"),
        ],
    )
    assert [a.message_indices for a in actual] == [(0,), (4,)]
    assert [a.turn_index for a in actual] == [0, 1]
    assert [a.turn_message_index for a in actual] == [
        a.message_indices[0] for a in actual
    ]


def test_invalid_existing_metadata_prevents_any_paired_write(store):
    store.save([USER], {"name": "one"})
    store.metadata_path.write_text("BROKEN")
    before = {path.name: path.read_bytes() for path in store.session_dir.iterdir()}
    with pytest.raises(SessionHistoryError):
        store.save([USER, ANSWER], {"name": "two"})
    assert {
        path.name: path.read_bytes() for path in store.session_dir.iterdir()
    } == before


def test_scan_limit_counts_invalid_blank_and_other_session_lines(store):
    write_events(store, [event("other", session_id="child"), event("wanted")])
    store.events_path.write_bytes(b"INVALID\n\n" + store.events_path.read_bytes())
    assert list(store.iter_events(max_lines=3)) == []
    assert [(d.code, d.line) for d in store.diagnostics] == [
        ("invalid_event", 1),
        ("scan_limit", 4),
    ]
    assert [row["event"] for row in store.iter_events()] == ["wanted"]


def test_exact_scan_limit_at_end_does_not_report_incomplete_history(store):
    write_events(store, [event("first"), event("second")])
    assert len(list(store.iter_events(max_lines=2))) == 2
    assert not store.diagnostics


@pytest.mark.parametrize("limit", [0, -1, True, 1.5, "2"])
def test_invalid_scan_limit_is_rejected_without_reading(store, limit):
    with pytest.raises(ValueError, match="max_lines"):
        list(store.iter_events(max_lines=limit))
    assert not store.session_dir.exists()


def test_byte_limit_never_parses_an_oversized_row(store, monkeypatch):
    write_events(store, [event("first")])
    first_bytes = store.events_path.stat().st_size
    with store.events_path.open("ab") as stream:
        stream.write(b'{"event":"huge","data":{"raw":"' + b"x" * 100_000 + b'"}}\n')
    original = history_module._decode
    parsed_sizes = []

    def bounded_decode(raw):
        parsed_sizes.append(len(raw))
        return original(raw)

    monkeypatch.setattr(history_module, "_decode", bounded_decode)
    actual = list(store.iter_events(max_bytes=first_bytes + 200))
    assert [row["event"] for row in actual] == ["first"]
    assert parsed_sizes == [first_bytes]
    assert [(d.code, d.line) for d in store.diagnostics] == [("scan_limit", 2)]


def test_exact_byte_limit_at_end_is_not_truncated(store):
    write_events(store, [event("first")])
    assert len(list(store.iter_events(max_bytes=store.events_path.stat().st_size))) == 1
    assert not store.diagnostics


def test_byte_limit_counts_skipped_rows(store):
    write_events(store, [event("other", session_id="child"), event("wanted")])
    assert list(store.iter_events(max_bytes=2)) == []
    assert [(d.code, d.line) for d in store.diagnostics] == [("scan_limit", 1)]


@pytest.mark.parametrize("limit", [0, -1, True, 1.5, "2"])
def test_invalid_byte_limit_is_rejected(store, limit):
    with pytest.raises(ValueError, match="max_bytes"):
        list(store.iter_events(max_bytes=limit))


def test_windows_public_api_routes_reads_writes_append_and_naming_to_adapter(tmp_path, monkeypatch):
    """Routing/format evidence only. Fake I/O is NOT native privacy evidence."""
    from contextlib import contextmanager
    from amplifier_foundation.session import jsonl, _windows_history as backend
    from amplifier_foundation.session import _windows_ownership as win
    from amplifier_foundation.session.metadata import SessionMetadataStore

    calls = []
    store = SessionHistoryStore(tmp_path / "synthetic", windows_policy="private")

    class WindowsOS:
        name = "nt"

    @contextmanager
    def locked(directory):
        calls.append("metadata-lock")
        yield

    @contextmanager
    def opened(path):
        calls.append("read")
        with path.open("rb") as stream:
            yield stream

    def replaced(path, content, preserve):
        calls.append("replace")
        path.parent.mkdir(parents=True, exist_ok=True)
        backup = path.with_suffix(path.suffix + ".backup")
        if path.exists() and not preserve:
            backup.write_bytes(path.read_bytes())
        path.write_bytes(content.encode())

    def copied(source, target):
        calls.append("backup")
        target.write_bytes(source.read_bytes())

    def appended(path, chunks):
        calls.append("append")
        with path.open("ab") as stream:
            for chunk in chunks:
                stream.write(chunk)

    def pending(path):
        calls.append("pending")
        path.write_bytes(b'{"version":1}\n')

    def removed(path):
        calls.append("remove")
        path.unlink(missing_ok=True)

    monkeypatch.setattr(history_module, "os", WindowsOS())
    monkeypatch.setattr(jsonl, "os", WindowsOS())
    monkeypatch.setattr(win, "_absolute", lambda path: path)
    monkeypatch.setattr(backend, "metadata_lock", locked)
    monkeypatch.setattr(backend, "open_binary", opened)
    monkeypatch.setattr(backend, "stamp", jsonl._posix_stamp)
    monkeypatch.setattr(backend, "write_prepared", replaced)
    monkeypatch.setattr(backend, "copy_backup", copied)
    monkeypatch.setattr(backend, "append", appended)
    monkeypatch.setattr(backend, "write_pending", pending)
    monkeypatch.setattr(backend, "remove_pending", removed)
    monkeypatch.setattr(history_module, "write_with_backup", lambda *a: pytest.fail("generic writer"))
    monkeypatch.setattr(history_module, "_write_atomic", lambda *a: pytest.fail("generic atomic writer"))
    monkeypatch.setattr(jsonl, "_write_atomic", lambda *a: pytest.fail("generic intent writer"))
    store.save([USER], {"custom": True}, merge_metadata=True, incremental=True)
    SessionMetadataStore(store.session_dir, windows_policy="private").set_name("canonical")
    store.save([USER, ANSWER], {"turn": 2}, merge_metadata=True, incremental=True)
    loaded = store.load(include_events=False)
    assert loaded.messages == [USER, ANSWER]
    assert list(store.indexed_messages()) == loaded.messages
    assert loaded.metadata["name"] == "canonical" and loaded.metadata["custom"]
    assert {"read", "replace", "backup", "pending", "append", "remove", "metadata-lock"} <= set(calls)


@pytest.mark.parametrize("selection", [{}, {"windows_policy": "legacy"}])
def test_windows_legacy_default_never_enters_private_backend(tmp_path, monkeypatch, selection):
    """Ordinary mkdir/default consumers retain legacy routes, not ACL detection."""
    from amplifier_foundation.session import jsonl, _windows_history as backend
    from amplifier_foundation.session import fork_session
    from amplifier_foundation.session.store import write_transcript, write_metadata
    from amplifier_foundation.session.metadata import SessionMetadataStore

    class WindowsOS:
        name = "nt"

        def __getattr__(self, key):
            return getattr(os, key)

    monkeypatch.setattr(jsonl, "os", WindowsOS())
    monkeypatch.setattr(backend, "_API", lambda: pytest.fail("default entered native backend"))
    directory = tmp_path / "ordinary"
    directory.mkdir()
    store = SessionHistoryStore(directory, **selection)
    assert store.windows_policy == "legacy"
    store.save([USER], {"custom": True}, incremental=True)
    SessionMetadataStore(directory, **selection).set_name("legacy default")
    store.save([USER, ANSWER], {"turn": 2}, incremental=True, merge_metadata=True)
    assert store.load(include_events=False).messages == [USER, ANSWER]
    assert list(store.indexed_messages()) == [USER, ANSWER]
    write_transcript(directory, [USER], **selection)
    write_metadata(directory, {"session_id": "legacy"}, **selection)
    assert store.load_messages() == [USER]
    result = fork_session(directory, new_session_id="legacy-fork", include_events=False, **selection)
    assert SessionHistoryStore(result.session_dir).load_messages() == [USER]


@pytest.mark.parametrize("policy", [None, True, "", "auto", "PRIVATE", []])
def test_invalid_windows_policy_refuses_before_io(tmp_path, monkeypatch, policy):
    from amplifier_foundation.session import jsonl, fork_session
    from amplifier_foundation.session.metadata import SessionMetadataStore, metadata_lock
    from amplifier_foundation.session.store import write_transcript, write_metadata

    monkeypatch.setattr(Path, "open", lambda *a, **kw: pytest.fail("I/O before policy validation"))
    monkeypatch.setattr(Path, "stat", lambda *a, **kw: pytest.fail("stat before policy validation"))
    directory = tmp_path / "missing"
    operations = [
        lambda: SessionHistoryStore(directory, windows_policy=policy),
        lambda: SessionMetadataStore(directory, windows_policy=policy),
        lambda: jsonl.indexed(directory / "transcript.jsonl", windows_policy=policy),
        lambda: jsonl.append_messages(directory / "transcript.jsonl", [], windows_policy=policy),
        lambda: write_transcript(directory, [], windows_policy=policy),
        lambda: write_metadata(directory, {}, windows_policy=policy),
        lambda: fork_session(directory, windows_policy=policy),
    ]
    for operation in operations:
        with pytest.raises(ValueError, match="windows_policy"):
            operation()
    with pytest.raises(ValueError, match="windows_policy"):
        with metadata_lock(directory, windows_policy=policy):
            pytest.fail("invalid lock yielded")


@pytest.mark.parametrize("operation", [
    "exists", "load", "load_messages", "load_metadata", "indexed_messages",
    "events", "save", "incremental", "save_messages", "save_metadata",
    "naming", "repair", "fork",
])
def test_explicit_private_refusal_never_falls_back(tmp_path, monkeypatch, operation):
    from amplifier_foundation.session import jsonl, fork_session, _windows_history as backend
    from amplifier_foundation.session import _windows_ownership as win
    from amplifier_foundation.session.metadata import SessionMetadataStore
    from amplifier_foundation.session.shared_state import SharedStateError
    from amplifier_foundation.session.store import write_transcript

    class WindowsOS:
        name = "nt"

    directory = tmp_path / "source"
    SessionHistoryStore(directory).save([USER], {})  # Setup before selecting strict.
    store = SessionHistoryStore(directory, windows_policy="private")
    names = SessionMetadataStore(directory, windows_policy="private")
    error, calls = SharedStateError("synthetic strict security refusal"), []

    def refuse(*args, **kwargs):
        calls.append("strict")
        raise error

    monkeypatch.setattr(jsonl, "os", WindowsOS())
    monkeypatch.setattr(win, "_absolute", lambda path: path)
    for name in ("stamp", "open_binary", "metadata_lock", "write_prepared", "append",
                 "write_pending", "remove_pending", "copy_backup", "reserve_new_session"):
        monkeypatch.setattr(backend, name, refuse)
    monkeypatch.setattr(history_module, "_write_atomic", lambda *a, **kw: pytest.fail("legacy fallback"))
    monkeypatch.setattr(history_module, "write_with_backup", lambda *a, **kw: pytest.fail("legacy fallback"))
    operations = {
        "exists": store.exists, "load": store.load, "load_messages": store.load_messages,
        "load_metadata": store.load_metadata, "indexed_messages": store.indexed_messages,
        "events": lambda: list(store.iter_events()),
        "save": lambda: store.save([USER], {}),
        "incremental": lambda: store.save([USER], {}, incremental=True),
        "save_messages": lambda: store.save_messages([USER]),
        "save_metadata": lambda: store.save_metadata({}),
        "naming": lambda: names.set_name("strict"),
        "repair": lambda: write_transcript(directory, [USER], windows_policy="private"),
        "fork": lambda: fork_session(directory, include_events=False, windows_policy="private"),
    }
    with pytest.raises(SharedStateError) as caught:
        operations[operation]()
    assert caught.value is error and calls == ["strict"]
    assert store.windows_policy == names.windows_policy == "private"
    with pytest.raises(AttributeError):
        store.windows_policy = "legacy"
    with pytest.raises(AttributeError):
        names.windows_policy = "legacy"


def test_windows_index_policies_are_isolated_across_threads_and_lazy_lifetimes(tmp_path, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from contextlib import contextmanager
    from amplifier_foundation.session import jsonl, _windows_history as backend
    from amplifier_foundation.session import _windows_ownership as win
    from amplifier_foundation.session.shared_state import SharedStateError

    class WindowsOS:
        name = "nt"

    directory = tmp_path / "same-file"
    SessionHistoryStore(directory).save([USER, ANSWER], {})
    legacy = SessionHistoryStore(directory)
    private = SessionHistoryStore(directory, windows_policy="private")
    private_reads = []

    @contextmanager
    def opened(path):
        private_reads.append(path)
        with path.open("rb") as stream:
            yield stream

    monkeypatch.setattr(jsonl, "os", WindowsOS())
    monkeypatch.setattr(win, "_absolute", lambda path: path)
    monkeypatch.setattr(backend, "stamp", jsonl._posix_stamp)
    monkeypatch.setattr(backend, "open_binary", opened)
    with ThreadPoolExecutor(max_workers=2) as pool:
        left, right = list(pool.map(lambda store: store.indexed_messages(), (legacy, private)))
    assert left is not right
    assert (left.windows_policy, right.windows_policy) == ("legacy", "private")
    assert list(left) == list(right) == [USER, ANSWER] and private_reads
    # A cached strict revision and a suspended store generator retain selection.
    def refuse(path):
        raise SharedStateError("future private open refused")
    pending = private.iter_events()
    monkeypatch.setattr(backend, "open_binary", refuse)
    assert list(left) == [USER, ANSWER]  # Legacy never inherits another store's policy.
    with pytest.raises(SharedStateError):
        list(right)
    with pytest.raises(SharedStateError):
        next(pending)
    with pytest.raises(AttributeError):
        right.windows_policy = "legacy"


@pytest.mark.parametrize("policy", ["legacy", "private"])
def test_policy_selection_construction_is_immutable_and_io_free(tmp_path, monkeypatch, policy):
    from amplifier_foundation.session.metadata import SessionMetadataStore
    from amplifier_foundation.session import _windows_history as backend

    def forbidden(*args, **kwargs):
        pytest.fail("constructor performed filesystem I/O")

    monkeypatch.setattr(Path, "open", forbidden)
    monkeypatch.setattr(Path, "stat", forbidden)
    monkeypatch.setattr(Path, "mkdir", forbidden)
    monkeypatch.setattr(backend, "_API", forbidden)
    path = tmp_path / "not-created"
    history = SessionHistoryStore(path, windows_policy=policy)
    metadata = SessionMetadataStore(path, windows_policy=policy)
    assert history.windows_policy == metadata.windows_policy == policy
    with pytest.raises(AttributeError):
        history.windows_policy = "private" if policy == "legacy" else "legacy"
