"""Synthetic owned-writer/F1 qualification; no live data or host activation."""
import copy
import dataclasses
import hashlib
import io
import json
import os
import pickle
import tracemalloc
from contextlib import contextmanager
from unittest.mock import Mock

import pytest
from PIL import Image

from amplifier_foundation.session.media import MEDIA_NAMESPACE as MEDIA, MediaRefusal, SessionMediaStore
from amplifier_foundation.session.transcript_v2 import CommittedPrefix, CommittedTranscriptReader
from amplifier_foundation.session.transcript_writer import CommitAck, TranscriptWriter, _descriptor, _header
from amplifier_foundation.session.versioned_checkpoint import (
    VersionedCheckpointReceipt, VersionedCheckpointRefused as Refused,
    acknowledge_versioned_checkpoint as acknowledging,
    verified_versioned_checkpoint_refs as verified_refs,
)

pytestmark = pytest.mark.skipif(os.name != "posix", reason="POSIX writer qualification")


@contextmanager
def fixture_fence(path):
    # Explicit single-writer synthetic fixture, NOT a production fencing adapter.
    assert path.is_absolute()
    yield lambda: None


def fake_ref():
    return {"v": 1, "session_id": "session", "sha256": "a" * 64,
            "mime": "image/png", "bytes": 10, "width": 2, "height": 2,
            "provenance": {"kind": "tool", "session_id": "session", "id": "call-1",
                           "created_at": "2026-01-01T00:00:00Z", "expires_at": None}}


def row(refs=None, **extra):
    return {"role": "tool", "tool_call_id": "call-1", "content": "label",
            "metadata": {MEDIA: {"refs": refs if refs is not None else [fake_ref()]}}, **extra}


def session(tmp_path, rows=None):
    path = tmp_path / "session"
    path.mkdir(mode=0o700)
    writer = TranscriptWriter(path, fence=fixture_fence)
    initial = writer.initialize(session_id="session", operation_id="init", admit=lambda: None)
    ack = writer.append(rows or [row()], expected=initial.commit,
                        operation_id="append", admit=lambda: None)
    return path, writer, ack


def acknowledge(writer, expected):
    with acknowledging(writer, session_id="session", expected=expected) as receipt:
        pass
    return receipt


def replace_bytes(path, raw=None):
    replacement = path.with_name(".replacement")
    replacement.write_bytes(path.read_bytes() if raw is None else raw)
    replacement.chmod(path.stat().st_mode & 0o777)
    replacement.replace(path)


@pytest.mark.asyncio
@pytest.mark.parametrize("format", ["PNG", "JPEG"])
async def test_actual_media_publication_append_ack_resolve_and_process_local_resume(tmp_path, format):
    path = tmp_path / "session"
    path.mkdir(mode=0o700)
    store = SessionMediaStore(path / "media", "session", checkpoint_verifier=verified_refs)
    origin = {"kind": "tool", "session_id": "session", "id": "call-1"}
    pixels = io.BytesIO()
    Image.new("RGB", (2, 2), "purple").save(pixels, format=format)
    data = pixels.getvalue()
    try:
        ref = (await store.ingest(data, source_format="image-bytes", origin=origin, policy=None))["value"]
        await store.publish([ref], origin=origin, disposition="retain")
        writer = TranscriptWriter(path, fence=fixture_fence)
        initial = writer.initialize(session_id="session", operation_id="init", admit=lambda: None)
        admit = Mock(side_effect=lambda: store.validate_published_ref(ref))
        ack = writer.append([row([ref])], expected=initial.commit, operation_id="append", admit=admit)
        assert admit.call_count == 1
        with pytest.raises(MediaRefusal):
            store.resolve_ref(ref)
        with acknowledging(writer, session_id="session", expected=ack) as receipt:
            result = store.acknowledge_checkpoint(receipt)
        # Existing F1 output label is unchanged; manager owns label integration.
        assert result["state"] == "committed" and result["ref_count"] == 1
        assert receipt.verify() == dataclasses.asdict(ack.commit) | {"kind": "versioned-checkpoint-backed-v1"}
        assert list(receipt.iter_refs()) == [ref]
        with store.resolve_ref(ref) as stream:
            assert stream.read() == data
        assert CommittedTranscriptReader(path).load_messages() == [row([ref])]
        assert admit.call_count == 1
        facts = json.loads(json.dumps(receipt.verify()))
        store.close()
        store = SessionMediaStore(path / "media", "session", checkpoint_verifier=verified_refs)
        with pytest.raises(MediaRefusal, match="resumed host"):
            store.resolve_ref(ref)
        with pytest.raises(Refused):
            store.acknowledge_checkpoint(facts)
        fresh = acknowledge(TranscriptWriter(path, fence=fixture_fence), ack.commit)
        store.acknowledge_checkpoint(fresh)
        with store.resolve_ref(ref) as stream:
            assert stream.read() == data
    finally:
        store.close()


@pytest.mark.parametrize("selector", [True, {}, {"commit": {}}, object()])
def test_unsealed_lookalikes_cannot_select_or_mint(tmp_path, selector):
    _, writer, _ = session(tmp_path)
    with pytest.raises(Refused):
        acknowledge(writer, selector)
    with pytest.raises(Refused):
        list(verified_refs(selector))


def test_exact_types_and_forged_public_private_fields_cannot_recreate_authority(tmp_path):
    path, writer, ack = session(tmp_path)
    receipt = acknowledge(writer, ack)
    class Derived(VersionedCheckpointReceipt):
        pass
    class WriterLookalike:
        session_dir = path
        _owned = writer._owned
    with pytest.raises(Refused):
        acknowledge(WriterLookalike(), ack)
    changes = [{field.name: "forged"} for field in dataclasses.fields(receipt)
               if field.init and not field.name.startswith("_")]
    changes += [{"_seal": object()}, {"_attestation": "0" * 64},
                {"_session_dir": path / ".." / "session"},
                {"_controls": ()}, {"_body_stamp": ()}, {}]
    for change in changes:
        with pytest.raises(Refused):
            dataclasses.replace(receipt, **change).verify()
    with pytest.raises(Refused):
        copy.copy(receipt).verify()
    with pytest.raises(Refused):
        pickle.loads(pickle.dumps(receipt)).verify()
    derived = Derived(**{f.name: getattr(receipt, f.name) for f in dataclasses.fields(receipt) if f.init})
    with pytest.raises(Refused):
        list(verified_refs(derived))
    assert receipt.verify()["session_id"] == "session"


@pytest.mark.parametrize("change", [
    {"session_id": "other"}, {"generation": "other"}, {"body": "../escape"},
    {"operation_id": "init"}, {"sequence": True}, {"message_count": 0},
    {"committed_bytes": 1}, {"sha256": "b" * 64},
])
def test_selector_is_only_data_must_match_actual_current(tmp_path, change):
    _, writer, ack = session(tmp_path)
    with pytest.raises((Refused, ValueError)):
        acknowledge(writer, dataclasses.replace(ack.commit, **change))


def test_pointer_advance_and_historical_ack_refused(tmp_path):
    _, writer, ack = session(tmp_path)
    receipt = acknowledge(writer, ack)
    second = writer.append([{"role": "user", "content": "next"}], expected=ack.commit,
                           operation_id="next", admit=lambda: None)
    with pytest.raises(Refused):
        receipt.verify()
    with pytest.raises(Refused):
        acknowledge(writer, ack)
    assert acknowledge(writer, second).sequence == second.commit.sequence


@pytest.mark.parametrize("name", ["current.json", "migration.json", "ready.json",
    "operations/append/intent.json", "operations/append/result.json", "operations/append/published.json"])
def test_same_byte_control_replacement_invalidates_receipt(tmp_path, name):
    path, writer, ack = session(tmp_path)
    receipt = acknowledge(writer, ack)
    replace_bytes(path / "transcript.v2" / name)
    with pytest.raises(Refused):
        receipt.verify()


@pytest.mark.parametrize("kind", ["body", "descriptor", "ancestor", "hardlink"])
def test_links_and_same_byte_ancestor_replacement_refuse(tmp_path, kind):
    path, writer, ack = session(tmp_path)
    receipt = acknowledge(writer, ack)
    target = path / "transcript.v2" / (ack.commit.body if kind != "descriptor" else "current.json")
    if kind == "ancestor":
        moved = tmp_path / "moved"
        path.rename(moved)
        import shutil
        shutil.copytree(moved, path)
    elif kind == "hardlink":
        os.link(target, tmp_path / "alias")
    else:
        moved = target.with_suffix(".moved")
        target.rename(moved)
        target.symlink_to(moved)
    with pytest.raises(Refused):
        receipt.verify()


def test_preexisting_tail_is_ignored_but_postpin_tail_change_invalidates(tmp_path):
    path, writer, ack = session(tmp_path)
    body = path / "transcript.v2" / ack.commit.body
    with body.open("ab") as stream:
        stream.write(json.dumps(row([{**fake_ref(), "sha256": "b" * 64}])).encode() + b"\n")
    receipt = acknowledge(writer, ack)
    assert list(receipt.iter_refs()) == [fake_ref()]
    with body.open("ab") as stream:
        stream.write(b"incomplete noncanonical uncommitted tail")
    with pytest.raises(Refused):
        receipt.verify()
    assert list(acknowledge(writer, ack).iter_refs()) == [fake_ref()]


def test_mutation_after_last_yield_is_refused_on_exhaustion(tmp_path):
    path, writer, ack = session(tmp_path)
    receipt = acknowledge(writer, ack)
    iterator = receipt.iter_refs()
    assert next(iterator) == fake_ref()
    replace_bytes(path / "transcript.v2" / "current.json")
    with pytest.raises(Refused):
        next(iterator)


@pytest.mark.parametrize("late", [False, True])
def test_context_exception_including_fence_exit_revokes_retained_receipt(tmp_path, late):
    _, writer, ack = session(tmp_path)
    retained = []
    if late:
        @contextmanager
        def failing_fence(path):
            yield lambda: None
            raise RuntimeError("late ownership failure")
        writer.fence = failing_fence
    with pytest.raises(RuntimeError):
        with acknowledging(writer, session_id="session", expected=ack) as receipt:
            retained.append(receipt)
            if not late:
                raise RuntimeError("caller failed")
    with pytest.raises(Refused):
        retained[0].verify()


def test_fsync_failure_creates_no_receipt_or_admission_replay(tmp_path, monkeypatch):
    path, writer, ack = session(tmp_path)
    before = (path / "transcript.v2" / ack.commit.body).read_bytes()
    fail = Mock(side_effect=OSError("synthetic fsync failure"))
    monkeypatch.setattr(os, "fsync", fail)
    yielded = []
    with pytest.raises(Refused):
        with acknowledging(writer, session_id="session", expected=ack) as receipt:
            yielded.append(receipt)
    assert not yielded and fail.call_count == 1
    assert (path / "transcript.v2" / ack.commit.body).read_bytes() == before


def test_reader_fixture_is_not_writer_closure(tmp_path):
    path = tmp_path / "session"
    root = path / "transcript.v2"
    root.mkdir(parents=True)
    raw = _header("fixture") + b'{"role":"user"}\n'
    prefix = CommittedPrefix("session", "fixture", "g-fixture.jsonl", 1, len(raw), 1,
                             hashlib.sha256(raw).hexdigest(), "fixture")
    (root / prefix.body).write_bytes(raw)
    (root / "current.json").write_bytes(_descriptor(prefix))
    assert CommittedTranscriptReader(path).verify() == prefix
    with pytest.raises(Refused):
        acknowledge(TranscriptWriter(path, fence=fixture_fence), CommitAck(prefix))


@pytest.mark.parametrize("metadata", [None, {}, {"nested": {MEDIA: {"refs": [fake_ref()]}}}])
def test_metadata_absent_null_and_nested_text_carriers_are_not_refs(tmp_path, metadata):
    rows = [{"role": "user", "content": json.dumps({MEDIA: {"refs": [fake_ref()]}})},
            {"role": "assistant", "metadata": metadata}]
    _, writer, ack = session(tmp_path, rows)
    assert list(acknowledge(writer, ack).iter_refs()) == []


@pytest.mark.parametrize("envelope", [None, [], {}, {"refs": None}, {"refs": [True]},
    {"v": True, "refs": []}, {"refs": [{**fake_ref(), "v": True}]},
    {"refs": [{**fake_ref(), "session_id": "other"}]},
    {"refs": [{**fake_ref(), "mime": "image/webp"}]}])
def test_malformed_present_namespace_and_non_F1_refs_refuse(tmp_path, envelope):
    _, writer, ack = session(tmp_path, [{"role": "tool", "metadata": {MEDIA: envelope}}])
    with pytest.raises(Refused):
        acknowledge(writer, ack)


def test_duplicate_keys_ref_insertion_and_drop_refuse_even_rehashed_descriptor(tmp_path):
    path, writer, ack = session(tmp_path)
    receipt = acknowledge(writer, ack)
    body = path / "transcript.v2" / ack.commit.body
    raw = body.read_bytes().replace(b'"refs":[', b'"refs":[],"refs":[')
    body.write_bytes(raw)
    altered = dataclasses.replace(ack.commit, committed_bytes=len(raw), sha256=hashlib.sha256(raw).hexdigest())
    root = path / "transcript.v2"
    for name in ("current.json", "operations/append/result.json", "operations/append/published.json"):
        (root / name).write_bytes(_descriptor(altered))
    with pytest.raises(Refused):
        receipt.verify()
    with pytest.raises((Refused, ValueError)):
        acknowledge(writer, altered)


@pytest.mark.parametrize("shape", ["huge-string", "wide-refs"])
def test_fixed_scanner_memory_for_large_scalar_and_ref_array(tmp_path, shape):
    refs = [fake_ref()] * (2400 if shape == "wide-refs" else 1)
    rows = [row(refs, content="q" * (8 * 1024 * 1024) if shape == "huge-string" else "label")]
    rows[0]["metadata"]["opaque"] = "m" * (2 * 1024 * 1024)
    _, writer, ack = session(tmp_path, rows)
    del rows, refs
    tracemalloc.start()
    try:
        receipt = acknowledge(writer, ack)
        count = sum(1 for _ in receipt.iter_refs())
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    assert count == (2400 if shape == "wide-refs" else 1)
    print(f"bounded-memory {shape}: refs={count} traced_peak_bytes={peak}")
    assert peak < 4 * 1024 * 1024, peak


@pytest.mark.parametrize("name", [
    "migration.json", "ready.json", "operations/append/result.json",
    "../transcript.jsonl/format.json", "../transcript.jsonl.backup/format.json",
])
def test_missing_writer_witness_refuses_before_publication_settlement(tmp_path, name):
    path, writer, ack = session(tmp_path)
    root = path / "transcript.v2"
    publication = root / "operations/append/published.json"
    publication.unlink()
    (root / name).unlink()
    with pytest.raises(Refused):
        acknowledge(writer, ack)
    assert not publication.exists()


def test_only_missing_current_publication_is_settled_without_replay(tmp_path, monkeypatch):
    path, writer, ack = session(tmp_path)
    root = path / "transcript.v2"
    publication = root / "operations/append/published.json"
    publication.unlink()
    before = {str(p.relative_to(path)): p.read_bytes() for p in path.rglob("*") if p.is_file()}
    with monkeypatch.context() as patch:
        for name in ("append", "rewrite", "initialize", "migrate", "reconcile"):
            patch.setattr(TranscriptWriter, name, Mock(side_effect=AssertionError("replay")))
        receipt = acknowledge(writer, ack)
    after = {str(p.relative_to(path)): p.read_bytes() for p in path.rglob("*") if p.is_file()}
    assert after == before | {"transcript.v2/operations/append/published.json": _descriptor(ack.commit)}
    assert receipt.verify()["operation_id"] == "append"


@pytest.mark.parametrize("mode", ["insert", "drop", "same-byte-body", "descriptor-ABA", "private-mode"])
def test_observed_mutations_invalidate_old_receipt(tmp_path, mode):
    path, writer, ack = session(tmp_path)
    receipt = acknowledge(writer, ack)
    root = path / "transcript.v2"
    body = root / ack.commit.body
    if mode == "same-byte-body":
        replace_bytes(body)
    elif mode == "private-mode":
        (root / "current.json").chmod(0o644)
    elif mode == "descriptor-ABA":
        current = root / "current.json"
        old = root / "old-descriptor"
        current.rename(old)
        current.write_bytes(old.read_bytes())
        current.unlink()
        old.rename(current)
    else:
        changed = [fake_ref(), {**fake_ref(), "sha256": "b" * 64}] if mode == "insert" else []
        body.write_bytes(_header(ack.commit.generation) + json.dumps(row(changed)).encode() + b"\n")
    with pytest.raises(Refused):
        receipt.verify()


@pytest.mark.parametrize("failure", ["caller-base-exception", "final-check"])
def test_all_context_exit_failures_invalidate(tmp_path, failure):
    _, writer, ack = session(tmp_path)
    retained = []
    failing = [False]
    class Cancelled(BaseException):
        pass
    @contextmanager
    def fence(path):
        def check():
            if failing[0]:
                raise RuntimeError("lease expired")
        yield check
    writer.fence = fence
    with pytest.raises((Cancelled, RuntimeError)):
        with acknowledging(writer, session_id="session", expected=ack) as receipt:
            retained.append(receipt)
            if failure == "caller-base-exception":
                raise Cancelled()
            failing[0] = True
    with pytest.raises(Refused):
        retained[0].verify()


def test_scanner_never_decodes_whole_message_metadata_array_or_huge_string(tmp_path, monkeypatch):
    from amplifier_foundation.session._media_conversion_parser import Scanner
    rows = [row([fake_ref()] * 30, content="x" * (128 * 1024))]
    rows[0]["metadata"]["opaque"] = "z" * (128 * 1024)
    _, writer, ack = session(tmp_path, rows)
    original = Scanner.small
    def checked(scanner, span, limit=16384):
        value = original(scanner, span, limit)
        assert not isinstance(value, list), "whole refs array decoded"
        if isinstance(value, dict):
            assert "sha256" in value, "whole message/metadata/envelope decoded"
        if isinstance(value, str):
            assert len(value) <= 4096, "huge string decoded"
        return value
    monkeypatch.setattr(Scanner, "small", checked)
    assert sum(1 for _ in acknowledge(writer, ack).iter_refs()) == 30


@pytest.mark.asyncio
@pytest.mark.parametrize("phase", ["bind", "resolve"])
async def test_store_exhausts_after_last_ref_before_bind_and_after_match(tmp_path, phase):
    path = tmp_path / "session"
    path.mkdir(mode=0o700)
    changing = [False]
    def verifier(receipt):
        for ref in verified_refs(receipt):
            yield ref
            if changing[0]:
                replace_bytes(path / "transcript.v2/current.json")
    store = SessionMediaStore(path / "media", "session", checkpoint_verifier=verifier)
    try:
        origin = {"kind": "tool", "session_id": "session", "id": "call-1"}
        image = io.BytesIO()
        Image.new("RGB", (2, 2), "red").save(image, format="PNG")
        ref = (await store.ingest(image.getvalue(), source_format="image-bytes",
                                  origin=origin, policy=None))["value"]
        await store.publish([ref], origin=origin, disposition="retain")
        writer = TranscriptWriter(path, fence=fixture_fence)
        initial = writer.initialize(session_id="session", operation_id="init", admit=lambda: None)
        ack = writer.append([row([ref])], expected=initial.commit, operation_id="append",
                            admit=lambda: store.validate_published_ref(ref))
        receipt = acknowledge(writer, ack)
        if phase == "resolve":
            store.acknowledge_checkpoint(receipt)
        changing[0] = True
        if phase == "bind":
            from amplifier_foundation.session.media import _key
            with pytest.raises(Refused):
                store.acknowledge_checkpoint(receipt)
            record = store._read(store._dirs["records"], _key(ref) + ".json")
            assert record["state"] == "published"
            assert not store._checkpoints
        else:
            with pytest.raises(Refused):
                store.resolve_ref(ref)
    finally:
        store.close()
