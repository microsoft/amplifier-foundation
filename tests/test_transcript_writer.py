"""Synthetic POSIX writer/migration qualification, never live activation."""

import asyncio
import hashlib
import io
import json
import os
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from dataclasses import replace
from pathlib import Path

import pytest

from amplifier_foundation.session import transcript_writer as w
from amplifier_foundation.session.transcript_encoding import (
    canonical_bytes,
    canonical_records,
)
from amplifier_foundation.session.transcript_v2 import (
    CommittedTranscriptReader,
    UnsupportedTranscript,
)

pytestmark = pytest.mark.skipif(os.name != "posix", reason="POSIX synthetic qualification")
ROWS = [
    {"role": "user", "content": "question"},
    {"role": "tool", "content": "answer", "tool_call_id": "call",
     "metadata": {"opaque": {"signature": "preserved"}, "experimental.media.v1": {"refs": []}}},
]


@contextmanager
def host_fence(path):
    # Real exclusion over the fixture's participating host writers. Production
    # callers must additionally quiesce nonparticipating/old writers themselves.
    with (path / ".test-host.lock").open("a+b") as lock:
        w.fcntl.flock(lock, w.fcntl.LOCK_EX)
        active = True

        def check():
            assert active and not lock.closed
        yield check
        active = False


def admit():
    pass  # Text-only fixtures; no assets to publish.


def prepare(path, rows=ROWS):
    path.mkdir(exist_ok=True)
    (path / "transcript.jsonl").write_bytes(
        b"".join((json.dumps(row, ensure_ascii=False) + "\n").encode() for row in rows))
    (path / "transcript.jsonl.backup").write_bytes(b"backup-original\x00opaque")
    return {name: w.observe_source(path, name) for name in w.LEGACY}


def migrate(path, sources, operation_id="migration"):
    writer = w.TranscriptWriter(path, fence=host_fence)
    ack = writer.migrate(session_id="fixture", operation_id=operation_id,
                         expected_sources=sources, source="transcript.jsonl", admit=admit)
    return writer, ack.commit


def read(path):
    return CommittedTranscriptReader(path).load_messages()


def inventory(path):
    return {str(p.relative_to(path)): p.read_bytes()
            for p in path.rglob("*") if p.is_file()}


def assert_originals(path, sources):
    for name, witness in sources.items():
        if witness is None:
            continue
        candidates = [path / name, path / "recovery" / "migration" / name]
        assert any(p.is_file() and hashlib.sha256(p.read_bytes()).hexdigest() == witness.sha256
                   for p in candidates)


def test_migration_append_noop_rewrite_and_reconciliation(tmp_path):
    sources = prepare(tmp_path)
    writer, initial = migrate(tmp_path, sources)
    assert read(tmp_path) == ROWS
    assert_originals(tmp_path, sources)
    for name in w.LEGACY:
        assert (tmp_path / name / "format.json").is_file()
    root = tmp_path / "transcript.v2"
    old_body = (root / initial.body).read_bytes()
    extra = [{"role": "assistant", "content": "new"}]
    ack = writer.append(iter(extra), expected=initial, operation_id="append", admit=admit)
    assert ack.commit.message_count == 3
    assert (root / initial.body).read_bytes().startswith(old_body)
    assert read(tmp_path) == ROWS + extra
    before = inventory(tmp_path)
    assert writer.append([], expected=ack.commit, operation_id="noop", admit=admit).commit == ack.commit
    assert inventory(tmp_path) == before
    again = writer.append(extra, expected=initial, operation_id="append", admit=admit)
    assert again.reconciled and again.commit == ack.commit
    rewritten = writer.rewrite(extra, expected=ack.commit, operation_id="rewrite", admit=admit)
    assert rewritten.commit.generation != initial.generation
    assert (root / initial.body).read_bytes() == before["transcript.v2/" + initial.body]
    assert read(tmp_path) == extra
    historical = writer.append(extra, expected=initial, operation_id="append", admit=admit)
    assert historical.commit == ack.commit and historical.reconciled
    assert_originals(tmp_path, sources)


def test_explicit_new_session_initialization(tmp_path):
    writer = w.TranscriptWriter(tmp_path, fence=host_fence)
    first = writer.initialize(session_id="new", operation_id="init", admit=admit)
    assert read(tmp_path) == []
    assert first.commit.message_count == 0
    second = writer.append(ROWS, expected=first.commit, operation_id="first", admit=admit)
    assert second.commit.message_count == 2 and read(tmp_path) == ROWS
    assert writer.initialize(session_id="new", operation_id="init", admit=admit).reconciled


@pytest.mark.parametrize("evidence", ["transcript.jsonl.append-pending", "recovery"])
def test_initialization_refuses_interrupted_or_recovery_evidence(tmp_path, evidence):
    (tmp_path / evidence).mkdir()
    with pytest.raises(w.TranscriptConflict):
        w.TranscriptWriter(tmp_path, fence=host_fence).initialize(
            session_id="new", operation_id="init", admit=admit)
    assert not (tmp_path / "transcript.v2").exists()


def test_migration_historical_lost_ack_after_later_commit(tmp_path):
    sources = prepare(tmp_path)
    writer, prefix = migrate(tmp_path, sources)
    writer.append(ROWS, expected=prefix, operation_id="later", admit=admit)
    result = writer.migrate(session_id="fixture", operation_id="migration",
                            expected_sources=sources, source="transcript.jsonl", admit=admit)
    assert result.commit == prefix and result.reconciled
    assert read(tmp_path) == ROWS + ROWS


@pytest.mark.parametrize("raw", [
    b'{"role":"user","content":"torn',
    b'{"role":"user","metadata":{"dup":1,"dup":2}}\n',
    b'{"role":"user","content":NaN}\n',
    b'{"kind":"envelope","message":{"role":"user"}}\n',
    b'\n', b'[]\n', b'{"role":"user"} trailing\n',
    b'{"role":"user","content":"\\ud800"}\n',
])
def test_malformed_migration_preserves_source_and_never_empty_healthy(tmp_path, raw):
    sources = prepare(tmp_path)
    (tmp_path / "transcript.jsonl").write_bytes(raw)
    sources["transcript.jsonl"] = w.observe_source(tmp_path, "transcript.jsonl")
    with pytest.raises((ValueError, OSError)):
        migrate(tmp_path, sources)
    assert_originals(tmp_path, sources)
    with pytest.raises(UnsupportedTranscript):
        read(tmp_path)


@pytest.mark.parametrize("state", ["root", "journal", "ready", "body", "partial-current"])
def test_incomplete_new_namespace_is_not_empty_healthy_resume(tmp_path, state):
    from amplifier_foundation.session.history import SessionHistoryStore
    from amplifier_foundation.session.store import load_transcript
    root = tmp_path / "transcript.v2"
    root.mkdir()
    if state == "journal":
        (root / "migration.json").write_text('{"format":"amplifier.session.transcript","v":2}')
    elif state == "ready":
        (root / "ready.json").write_text("{}")
    elif state == "body":
        (root / "g-incomplete.jsonl").write_text('{"role":"user"}\n')
    elif state == "partial-current":
        (root / "current.json").write_text('{"format":')
    assert SessionHistoryStore(tmp_path).exists()
    for loader in (read, load_transcript, lambda p: SessionHistoryStore(p).load_messages()):
        with pytest.raises(UnsupportedTranscript):
            loader(tmp_path)


@pytest.mark.parametrize("damage", ["primary", "backup", "ready", "journal"])
def test_committed_migration_requires_fences_and_ready_journal(tmp_path, damage):
    sources = prepare(tmp_path)
    migrate(tmp_path, sources)
    names = {"primary": "transcript.jsonl/format.json",
             "backup": "transcript.jsonl.backup/format.json",
             "ready": "transcript.v2/ready.json", "journal": "transcript.v2/migration.json"}
    (tmp_path / names[damage]).write_bytes(b"{}")
    with pytest.raises(UnsupportedTranscript):
        read(tmp_path)


def test_tail_exactly_preserved_before_new_append(tmp_path):
    sources = prepare(tmp_path)
    writer, prefix = migrate(tmp_path, sources)
    body = tmp_path / "transcript.v2" / prefix.body
    tail = b'{"role":"user","content":"uncommitted"}\n\xff\x00torn'
    with body.open("ab") as stream:
        stream.write(tail)
    assert read(tmp_path) == ROWS
    writer.append([{"role": "assistant", "content": "settled"}], expected=prefix,
                  operation_id="next", admit=admit)
    assert [p.read_bytes() for p in (tmp_path / "recovery" / "next").glob("tail-*.bin")] == [tail]
    assert read(tmp_path)[-1]["content"] == "settled"


def test_rewrite_retains_old_generation_even_with_uncommitted_binary_tail(tmp_path):
    writer, prefix = migrate(tmp_path, prepare(tmp_path))
    body = tmp_path / "transcript.v2" / prefix.body
    with body.open("ab") as stream:
        stream.write(b"\xff\x00complete-and-torn-tail")
    before = body.read_bytes()
    writer.rewrite(ROWS, expected=prefix, operation_id="compact", admit=admit)
    assert body.read_bytes() == before
    assert read(tmp_path) == ROWS


@pytest.mark.parametrize("stage", ["before", "after"])
def test_descriptor_lost_ack_reconciles_same_op_never_duplicate(tmp_path, monkeypatch, stage):
    sources = prepare(tmp_path)
    writer, prefix = migrate(tmp_path, sources)
    original = os.replace

    def fail(*args, **kwargs):
        if stage == "after":
            original(*args, **kwargs)
        raise OSError("synthetic lost descriptor acknowledgement")

    with monkeypatch.context() as patch:
        patch.setattr(os, "replace", fail)
        with pytest.raises(OSError):
            writer.append(ROWS, expected=prefix, operation_id="retry", admit=admit)
    assert writer._warm is None
    payload_hash = hashlib.sha256(
        b"".join(b"".join(canonical_bytes(row)) + b"\n" for row in ROWS)).hexdigest()
    reconciled = writer.reconcile(operation_id="retry", expected=prefix,
                                  suffix_sha256=payload_hash)
    assert (reconciled is not None) == (stage == "after")
    result = writer.append(ROWS, expected=prefix, operation_id="retry", admit=admit)
    assert read(tmp_path) == ROWS + ROWS
    assert result.commit.message_count == 4
    assert writer.append(ROWS, expected=prefix, operation_id="retry", admit=admit).reconciled
    with pytest.raises(w.TranscriptConflict):
        writer.append([{"role": "user", "content": "different"}], expected=prefix,
                      operation_id="retry", admit=admit)
    with pytest.raises(w.TranscriptConflict):
        writer.reconcile(operation_id="retry", expected=prefix, suffix_sha256="0"*64)


def test_cancellation_preserves_tail_invalidates_cache_then_same_op_retries(tmp_path, monkeypatch):
    sources = prepare(tmp_path)
    writer, prefix = migrate(tmp_path, sources)

    def cancel(*args, **kwargs):
        raise asyncio.CancelledError()

    with monkeypatch.context() as patch:
        patch.setattr(os, "replace", cancel)
        with pytest.raises(asyncio.CancelledError):
            writer.append(ROWS, expected=prefix, operation_id="cancel", admit=admit)
    assert writer._warm is None and read(tmp_path) == ROWS
    writer.append(ROWS, expected=prefix, operation_id="cancel", admit=admit)
    assert read(tmp_path) == ROWS + ROWS
    assert list((tmp_path / "recovery" / "cancel").glob("tail-*.bin"))


def test_warm_append_never_rehashes_or_parses_history(tmp_path, monkeypatch):
    sources = prepare(tmp_path)
    writer, prefix = migrate(tmp_path, sources)
    prefix = writer.append(ROWS, expected=prefix, operation_id="warm", admit=admit).commit

    def forbidden(*args, **kwargs):
        raise AssertionError("warm append reread history")

    monkeypatch.setattr(w, "_digest", forbidden)
    monkeypatch.setattr(w, "_validate_prefix", forbidden)
    prefix = writer.append(ROWS, expected=prefix, operation_id="warm2", admit=admit).commit
    assert prefix.message_count == 6


@pytest.mark.parametrize("rewrite", [False, True])
def test_source_replacement_during_asset_admission_refuses(tmp_path, rewrite):
    writer, prefix = migrate(tmp_path, prepare(tmp_path))
    prefix = writer.append(ROWS, expected=prefix, operation_id="warm", admit=admit).commit
    root = tmp_path / "transcript.v2"
    body = root / prefix.body
    descriptor = (root / "current.json").read_bytes()

    def replacement():
        retained = root / "externally-replaced-body"
        body.rename(retained)
        body.write_bytes(b"foreign-body")
    method = writer.rewrite if rewrite else writer.append
    with pytest.raises(w.TranscriptConflict):
        method(ROWS, expected=prefix, operation_id="replaced", admit=replacement)
    assert (root / "current.json").read_bytes() == descriptor
    assert writer._warm is None


def test_duplicate_header_fails_cold_incremental_verification(tmp_path):
    writer, prefix = migrate(tmp_path, prepare(tmp_path))
    root = tmp_path / "transcript.v2"
    body = root / prefix.body
    raw = body.read_bytes().replace(b'{"format":', b'{"v":2,"format":', 1)
    body.write_bytes(raw)
    data = json.loads((root / "current.json").read_bytes())
    data.update(committed_bytes=len(raw), sha256=hashlib.sha256(raw).hexdigest())
    (root / "current.json").write_bytes(w._small(data))
    with pytest.raises(ValueError):
        writer.append(ROWS, expected=prefix, operation_id="bad-header", admit=admit)
    assert writer._warm is None


@pytest.mark.parametrize("damage", ["hash", "eof", "body-replacement", "symlink", "missing-hash"])
def test_cold_or_replaced_source_never_uses_warm_hash(tmp_path, damage):
    sources = prepare(tmp_path)
    writer, prefix = migrate(tmp_path, sources)
    prefix = writer.append(ROWS, expected=prefix, operation_id="warm", admit=admit).commit
    root = tmp_path / "transcript.v2"
    body = root / prefix.body
    if damage == "hash":
        body.write_bytes(body.read_bytes().replace(b"question", b"tampered"))
    elif damage == "eof":
        body.write_bytes(b"")
    elif damage in ("body-replacement", "symlink"):
        saved = root / "other"
        body.rename(saved)
        if damage == "symlink":
            body.symlink_to(saved)
        else:
            body.write_bytes(b"replacement")
    else:
        path = root / "current.json"
        data = json.loads(path.read_bytes())
        del data["sha256"]
        path.write_text(json.dumps(data))
    before = inventory(tmp_path)
    with pytest.raises((ValueError, OSError)):
        writer.append(ROWS, expected=prefix, operation_id="bad", admit=admit)
    assert writer._warm is None and inventory(tmp_path) == before


def test_source_witness_and_concurrent_cas(tmp_path):
    sources = prepare(tmp_path)
    with pytest.raises(w.TranscriptConflict):
        migrate(tmp_path, sources | {"transcript.jsonl": replace(sources["transcript.jsonl"], sha256="0"*64)})
    _writer, prefix = migrate(tmp_path, sources)

    def append(index):
        local = w.TranscriptWriter(tmp_path, fence=host_fence)
        try:
            return local.append(ROWS, expected=prefix, operation_id=f"race-{index}", admit=admit)
        except w.TranscriptConflict:
            return None
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(append, range(2)))
    assert sum(result is not None for result in results) == 1
    assert len(read(tmp_path)) == 4


def test_admission_before_reachability_and_required_live_fence(tmp_path):
    sources = prepare(tmp_path)
    writer, prefix = migrate(tmp_path, sources)
    before = inventory(tmp_path)

    def refuse():
        assert read(tmp_path) == ROWS
        raise RuntimeError("assets not durably published")

    with pytest.raises(RuntimeError):
        writer.append(ROWS, expected=prefix, operation_id="asset-refusal", admit=refuse)
    assert read(tmp_path) == ROWS
    assert (tmp_path / "transcript.v2" / prefix.body).read_bytes() == before["transcript.v2/" + prefix.body]
    with pytest.raises(TypeError):
        w.TranscriptWriter(tmp_path, fence=None)

    @contextmanager
    def fake_fence(path):
        yield None
    with pytest.raises(TypeError):
        w.TranscriptWriter(tmp_path, fence=fake_fence).append(
            ROWS, expected=prefix, operation_id="nofence", admit=admit)


def test_canonical_encoder_and_transducer_stable_bytes():
    values = ["", "a"*100000, "é😀\n\t\"\\", {"z": [1, 2.5, None, True], "a": "x"}]
    for value in values:
        actual = b"".join(canonical_bytes(value))
        assert actual == json.dumps(value, sort_keys=True, ensure_ascii=True,
                                    allow_nan=False, separators=(",", ":")).encode()
    rows = [{"role": "user", "content": value} for value in values]
    original = b"".join((json.dumps(row, ensure_ascii=False) + "\n").encode() for row in rows)
    expected = b"".join(b"".join(canonical_bytes(row)) + b"\n" for row in rows)
    assert b"".join(canonical_records(io.BytesIO(original))) == expected


@pytest.mark.parametrize("value", [
    {("k" * 4097): "value"}, {"x": 1 << 1000000}, {"x": float("inf")},
    {"x": "\ud800"},
])
def test_unsupported_values_refuse_before_publishing(value, tmp_path):
    writer, prefix = migrate(tmp_path, prepare(tmp_path))
    before = (tmp_path / "transcript.v2" / "current.json").read_bytes()
    with pytest.raises((ValueError, UnicodeError)):
        writer.append([{"role": "user", "content": value}], expected=prefix,
                      operation_id="invalid", admit=admit)
    assert (tmp_path / "transcript.v2" / "current.json").read_bytes() == before
    assert writer._warm is None


@pytest.mark.parametrize("operation", [
    "migration", "migration-primary-only", "migration-backup-only",
    "initialize", "append", "rewrite", "tail",
])
def test_every_fsync_and_rename_failure_preserves_commit_and_originals(
        tmp_path, monkeypatch, record_property, operation):
    """Inject before AND after every durability/namespace syscall in one run.

    Interrupted operations may either reconcile or fail closed (e.g. root-only
    before a journal exists). No retry is allowed to manufacture empty history,
    mutate originals or duplicate the suffix.
    """
    def setup(path):
        sources = prepare(path)
        if operation.startswith("migration") or operation == "initialize":
            selected = "transcript.jsonl"
            if operation in ("migration-primary-only", "initialize"):
                (path / "transcript.jsonl.backup").unlink()
            if operation in ("migration-backup-only", "initialize"):
                (path / "transcript.jsonl").unlink()
            if operation == "migration-backup-only":
                selected = "transcript.jsonl.backup"
                (path / selected).write_bytes(
                    b"".join((json.dumps(row) + "\n").encode() for row in ROWS))
            sources = {name: w.observe_source(path, name) for name in w.LEGACY}
            writer = w.TranscriptWriter(path, fence=host_fence)
            if operation == "initialize":
                call = lambda: writer.initialize(session_id="fixture", operation_id="migration", admit=admit)
            else:
                call = lambda: writer.migrate(session_id="fixture", operation_id="migration",
                                              expected_sources=sources, source=selected, admit=admit)
        else:
            writer, prefix = migrate(path, sources)
            if operation == "tail":
                with (path / "transcript.v2" / prefix.body).open("ab") as stream:
                    stream.write(b"\xff\x00torn-tail")
            method = writer.rewrite if operation == "rewrite" else writer.append
            call = lambda: method(ROWS, expected=prefix, operation_id="fault", admit=admit)
        return sources, writer, call

    trace = []
    originals = {name: getattr(os, name) for name in ("fsync", "rename", "replace")}
    def recorder(name):
        def wrapped(*args, **kwargs):
            trace.append(name)
            return originals[name](*args, **kwargs)
        return wrapped
    sources, _, call = setup(tmp_path / "count")
    with monkeypatch.context() as patch:
        for name in originals:
            patch.setattr(os, name, recorder(name))
        call()
    assert len(trace) > 10
    record_property("successful_durability_boundaries", len(trace))
    record_property("injected_before_after_cases", len(trace) * 2)
    for index in range(len(trace)):
        for after in (False, True):
            path = tmp_path / f"{index}-{after}"
            sources, writer, call = setup(path)
            counter = 0
            def injector(name, target=index, fail_after=after):
                def wrapped(*args, **kwargs):
                    nonlocal counter
                    current = counter
                    counter += 1
                    if current == target:
                        if fail_after:
                            originals[name](*args, **kwargs)
                        raise OSError("synthetic durability boundary")
                    return originals[name](*args, **kwargs)
                return wrapped
            with monkeypatch.context() as patch:
                for name in originals:
                    patch.setattr(os, name, injector(name))
                with pytest.raises(OSError):
                    call()
            assert writer._warm is None
            assert_originals(path, sources)
            if operation == "tail":
                tail = b"\xff\x00torn-tail"
                original_body = path / "transcript.v2" / "g-m-migration.jsonl"
                assert (original_body.read_bytes().endswith(tail)
                        or any(saved.read_bytes() == tail for saved in
                               (path / "recovery" / "fault").glob("tail-*.bin")))
            try:
                observed = read(path)
            except UnsupportedTranscript:
                assert operation.startswith("migration") or operation == "initialize"
            else:
                assert observed in (ROWS, ROWS + ROWS, [] if operation == "initialize" else ROWS)
            try:
                call()
            except (ValueError, OSError):
                # An unjournaled/ambiguous partial control record requires
                # explicit owner diagnosis; do not guess/replay it.
                pass
            else:
                assert read(path) == (
                    ROWS + ROWS if operation in ("append", "tail")
                    else [] if operation == "initialize" else ROWS)
            assert_originals(path, sources)


@pytest.mark.parametrize("absent", [None, "transcript.jsonl", "transcript.jsonl.backup"])
def test_every_transition_namespace_retains_a_legacy_entry(tmp_path, monkeypatch, absent):
    """Separate no-empty proof from byte-preservation; inspect each mutation."""
    sources = prepare(tmp_path)
    if absent is not None:
        (tmp_path / absent).unlink()
        sources[absent] = None
    selected = "transcript.jsonl.backup" if absent == "transcript.jsonl" else "transcript.jsonl"
    if selected.endswith(".backup"):
        (tmp_path / selected).write_bytes(b'{"role":"user","content":"backup"}\n')
        sources[selected] = w.observe_source(tmp_path, selected)
    originals = {name: getattr(os, name) for name in ("mkdir", "rename", "replace", "fsync")}
    checked = []

    def wrapping(name):
        def call(*args, **kwargs):
            result = originals[name](*args, **kwargs)
            entries = [tmp_path / key for key in w.LEGACY]
            assert any(p.exists() for p in entries), "both old names absent: fabricated empty history"
            for p in entries:
                if p.is_dir():
                    assert (p / "format.json").is_file(), "bare empty directory was published"
            checked.append(name)
            return result
        return call
    with monkeypatch.context() as patch:
        for name in originals:
            patch.setattr(os, name, wrapping(name))
        w.TranscriptWriter(tmp_path, fence=host_fence).migrate(
            session_id="fixture", operation_id="migration",
            expected_sources=sources, source=selected, admit=admit)
    assert len(checked) > 20
    assert_originals(tmp_path, sources)


def test_huge_individual_string_memory_and_suffix_work_are_maintained(tmp_path):
    root = Path(__file__).resolve().parents[1]
    result = subprocess.run(
        [sys.executable, str(root / "tests/qualify_transcript_writer.py"),
         "--work", str(tmp_path / "benchmark")],
        env={**os.environ, "PYTHONPATH": str(root)},
        capture_output=True, text=True, timeout=120, check=True)
    report = json.loads(result.stdout)
    assert [case["huge_string_chars"] for case in report["cases"]] == [2*1024**2, 8*1024**2]
    assert [case["warm_body_io"] for case in report["cases"]] == [
        {"read": 0, "write": 54}, {"read": 0, "write": 54}]