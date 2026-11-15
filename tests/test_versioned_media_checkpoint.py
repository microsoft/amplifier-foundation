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
    # Revocation is issuer-owned, not a reversible receipt-carried Boolean.
    retained[0]._life.valid = True
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
            assert ("sha256" in value or (
                span.end - span.start <= 16384
                and set(value) == {"format", "generation", "min_reader", "v"}
            )), "whole message/metadata/envelope decoded"
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


@pytest.mark.parametrize("corruption", ["intent-garbage", "intent-kind", "intent-bounds",
    "intent-suffix-digest", "intent-predecessor-digest",
    "plan-missing-selection", "plan-wrong-source", "plan-bool-stamp"])
def test_preexisting_invalid_writer_controls_cannot_mint_or_settle(tmp_path, corruption):
    path, writer, ack = session(tmp_path)
    root = path / "transcript.v2"
    publication = root / "operations/append/published.json"
    publication.unlink()
    if corruption.startswith("intent"):
        target = root / "operations/append/intent.json"
        if corruption == "intent-garbage":
            target.write_bytes(b"not-json")
        else:
            intent = json.loads(target.read_bytes())
            if corruption == "intent-suffix-digest":
                intent["suffix_sha256"] = "b" * 64
            elif corruption == "intent-predecessor-digest":
                intent["expected"]["sha256"] = "b" * 64
            else:
                intent["kind" if corruption == "intent-kind" else "suffix_bytes"] = (
                    "migration" if corruption == "intent-kind" else 0)
            target.write_text(json.dumps(intent))
    else:
        target = root / "migration.json"
        plan = json.loads(target.read_bytes())
        if corruption == "plan-missing-selection":
            del plan["source"], plan["sources"]
        elif corruption == "plan-wrong-source":
            plan["source"] = "transcript.jsonl"
        else:
            plan["sources"]["transcript.jsonl"] = {
                "dev": True, "ino": 1, "size": 1, "mtime_ns": 1,
                "ctime_ns": 1, "sha256": "a" * 64,
            }
        target.write_text(json.dumps(plan))
    with pytest.raises(Refused):
        acknowledge(writer, ack)
    assert not publication.exists()


@pytest.mark.parametrize("version", [b"2.0", b"2e0"])
def test_float_header_version_refused_even_when_hash_and_operation_records_match(tmp_path, version):
    path = tmp_path / "session"
    path.mkdir(mode=0o700)
    writer = TranscriptWriter(path, fence=fixture_fence)
    ack = writer.initialize(session_id="session", operation_id="init", admit=lambda: None)
    root = path / "transcript.v2"
    body = root / ack.commit.body
    raw = body.read_bytes().replace(b'"v":2', b'"v":' + version, 1)
    altered = dataclasses.replace(ack.commit, committed_bytes=len(raw), sha256=hashlib.sha256(raw).hexdigest())
    body.write_bytes(raw)
    for name in ("current.json", "operations/init/result.json", "operations/init/published.json"):
        (root / name).write_bytes(_descriptor(altered))
    with pytest.raises(Refused):
        acknowledge(writer, altered)


def test_fork_child_requires_fresh_owned_acknowledgement(tmp_path):
    _, writer, ack = session(tmp_path)
    receipt = acknowledge(writer, ack)
    pid = os.fork()
    if pid == 0:
        try:
            with pytest.raises(Refused):
                receipt.verify()
            assert acknowledge(writer, ack).verify()["operation_id"] == "append"
        except BaseException:
            os._exit(1)
        os._exit(0)
    _, status = os.waitpid(pid, 0)
    assert os.waitstatus_to_exitcode(status) == 0
    assert receipt.verify()["operation_id"] == "append"


def test_initialize_and_rewrite_current_operation_closure(tmp_path):
    path = tmp_path / "session"
    path.mkdir(mode=0o700)
    writer = TranscriptWriter(path, fence=fixture_fence)
    initial = writer.initialize(session_id="session", operation_id="init", admit=lambda: None)
    assert list(acknowledge(writer, initial).iter_refs()) == []
    rewrite = writer.rewrite([row()], expected=initial.commit, operation_id="rewrite", admit=lambda: None)
    receipt = acknowledge(writer, rewrite)
    assert receipt.generation == "r-rewrite"
    assert list(receipt.iter_refs()) == [fake_ref()]


@pytest.mark.parametrize("change", ["suffix-bytes", "suffix-hash"])
def test_rewrite_intent_bytes_must_match_selected_actual_region(tmp_path, change):
    path, writer, ack = session(tmp_path)
    rewritten = writer.rewrite([row()], expected=ack.commit, operation_id="rewrite", admit=lambda: None)
    operation = path / "transcript.v2/operations/rewrite"
    (operation / "published.json").unlink()
    target = operation / "intent.json"
    intent = json.loads(target.read_bytes())
    intent["suffix_bytes" if change == "suffix-bytes" else "suffix_sha256"] = (
        0 if change == "suffix-bytes" else "0" * 64)
    target.write_text(json.dumps(intent))
    with pytest.raises(Refused):
        acknowledge(writer, rewritten)
    assert not (operation / "published.json").exists()


@pytest.mark.parametrize("change", ["count-split", "inside-header"])
def test_append_predecessor_requires_real_row_boundary_and_message_count(tmp_path, change):
    path, writer, ack = session(tmp_path)
    root = path / "transcript.v2"
    operation = root / "operations/append"
    publication = operation / "published.json"
    publication.unlink()
    target = operation / "intent.json"
    intent = json.loads(target.read_bytes())
    if change == "count-split":
        intent["expected"]["message_count"] = 1
        intent["suffix_messages"] = 0
    else:
        raw = (root / ack.commit.body).read_bytes()
        intent["expected"]["committed_bytes"] = 1
        intent["expected"]["sha256"] = hashlib.sha256(raw[:1]).hexdigest()
        intent["suffix_bytes"] = len(raw) - 1
        intent["suffix_sha256"] = hashlib.sha256(raw[1:]).hexdigest()
    target.write_text(json.dumps(intent))
    with pytest.raises(Refused):
        acknowledge(writer, ack)
    assert not publication.exists()


def serialized_rows(rows, *, raw=None):
    if raw is None:
        raw = _header("serialized") + b"".join(
            json.dumps(item, separators=(",", ":")).encode() + b"\n" for item in rows)
    return raw, CommittedPrefix(
        "session", "serialized", "g-serialized.jsonl", 1, len(raw), len(rows),
        hashlib.sha256(raw).hexdigest(), "serialized",
    )


def scan_refs(raw, prefix, check=lambda: None):
    from amplifier_foundation.session.versioned_checkpoint import scan_serialized_refs
    return scan_serialized_refs(io.BytesIO(raw), prefix=prefix, check=check)


@pytest.mark.parametrize("metadata", [
    None, {}, {MEDIA: {"refs": []}}, {MEDIA: {"refs": [fake_ref()]}},
    {MEDIA: {"v": 1, "refs": [fake_ref()]}},
    {"opaque": {MEDIA: {"refs": [fake_ref()]}}},
])
def test_serialized_ref_schema_positive_and_no_nested_promotion(metadata):
    rows = [{"role": "tool", "metadata": metadata,
             "content": json.dumps({MEDIA: {"refs": [fake_ref()]}})}]
    raw, prefix = serialized_rows(rows)
    expected = metadata.get(MEDIA, {}).get("refs", []) if metadata else []
    assert list(scan_refs(raw, prefix)) == expected


@pytest.mark.parametrize("metadata", [
    False, 0, "", [], {MEDIA: None}, {MEDIA: False}, {MEDIA: []},
    {MEDIA: {"refs": None}}, {MEDIA: {}}, {MEDIA: {"refs": [], "extra": None}},
    *({MEDIA: {"v": value, "refs": []}} for value in (True, 1.0, None, 2)),
    {MEDIA: {"refs": [{**fake_ref(), "mime": "image/webp"}]}},
    {MEDIA: {"refs": [{**fake_ref(), "session_id": "other"}]}},
])
def test_serialized_ref_schema_strict_precommit_refusals(metadata):
    raw, prefix = serialized_rows([{"role": "tool", "metadata": metadata}])
    with pytest.raises(Refused):
        list(scan_refs(raw, prefix))


def test_serialized_ref_original_provenance_may_differ_and_data_is_not_authority():
    ref = fake_ref()
    ref["provenance"]["session_id"] = "original"
    raw, prefix = serialized_rows([row([ref])])
    refs = list(scan_refs(raw, prefix))
    assert refs == [ref]
    # Exact prefix + arbitrary stream/guard may validate DATA, not mint ACK.
    with pytest.raises(Refused):
        list(verified_refs(prefix))
    with pytest.raises(Refused):
        list(verified_refs(refs))


def writer_with_generation_at_operation_limit(tmp_path, kind, operation_length, refs):
    path = tmp_path / "session"
    path.mkdir(mode=0o700)
    writer = TranscriptWriter(path, fence=fixture_fence)
    operation_id = "x" * operation_length
    ack = writer.initialize(session_id="session",
                            operation_id=operation_id if kind == "m" else "init",
                            admit=lambda: None)
    if kind == "m":
        ack = writer.append([row(refs)], expected=ack.commit,
                            operation_id="seed", admit=lambda: None)
    else:
        ack = writer.rewrite([row(refs)], expected=ack.commit,
                             operation_id=operation_id, admit=lambda: None)
    assert ack.commit.generation == kind + "-" + operation_id
    assert CommittedTranscriptReader(path).verify() == ack.commit
    return writer, ack


@pytest.mark.parametrize("kind", ["m", "r"])
@pytest.mark.parametrize("operation_length", [178, 179, 180])
@pytest.mark.parametrize("with_ref", [False, True])
def test_prepared_refs_accept_writer_generation_at_operation_limit(
    tmp_path, kind, operation_length, with_ref,
):
    from amplifier_foundation.session.transcript_writer import prepare_serialized_candidate
    refs = [fake_ref()] if with_ref else []
    writer, ack = writer_with_generation_at_operation_limit(
        tmp_path, kind, operation_length, refs)
    with (writer.session_dir / "transcript.v2" / ack.commit.body).open("rb") as stream:
        with prepare_serialized_candidate(
            stream, candidate_prefix=ack.commit, candidate_check=lambda: None,
        ) as prepared:
            assert list(prepared.iter_refs()) == refs
            prepared.check()


@pytest.mark.parametrize("kind", ["m", "r"])
@pytest.mark.parametrize("operation_length", [178, 179, 180])
@pytest.mark.parametrize("next_kind", ["append", "rewrite"])
def test_typed_ack_accepts_predecessor_generation_at_operation_limit(
    tmp_path, kind, operation_length, next_kind,
):
    from contextlib import closing
    writer, previous = writer_with_generation_at_operation_limit(
        tmp_path, kind, operation_length, [])
    ack = getattr(writer, next_kind)(
        [row([])], expected=previous.commit, operation_id="next", admit=lambda: None)
    with closing(SessionMediaStore(writer.session_dir / "media", "session",
                                   checkpoint_verifier=verified_refs)) as store:
        with acknowledging(writer, session_id="session", expected=ack) as receipt:
            accepted = store.acknowledge_checkpoint(receipt)
        assert accepted["state"] == "committed" and accepted["ref_count"] == 0
        assert receipt.verify()["sha256"] == ack.commit.sha256


@pytest.mark.parametrize("field,value", [
    *(("generation", value) for value in
      (None, True, 1, "", "../escape", "bad/generation", ".leading", "x\n", "x" * 222)),
    *((field, value) for field in ("session_id", "operation_id")
      for value in ("x" * 181, "../escape", None, True)),
])
def test_prefix_generation_grammar_and_operation_session_limits_remain_strict(field, value):
    from amplifier_foundation.session.versioned_checkpoint import _prefix_data
    raw, prefix = serialized_rows([row([])])
    changed = dataclasses.replace(prefix, **{field: value})
    if field == "generation":
        changed = dataclasses.replace(changed, body=f"g-{value}.jsonl")
    with pytest.raises(ValueError):
        _prefix_data(dataclasses.asdict(changed))
    with pytest.raises(ValueError):
        list(scan_refs(raw, changed))


@pytest.mark.parametrize("corruption", [
    "digest", "short", "tail", "count", "header", "float-header", "header-duplicate",
    "no-final-LF", "duplicate-ref-key", "duplicate-opaque-key", "invalid-role",
])
def test_serialized_scan_integrity_and_semantic_refusals(corruption):
    raw, prefix = serialized_rows([row()])
    if corruption == "digest":
        prefix = dataclasses.replace(prefix, sha256="b" * 64)
    elif corruption == "short":
        raw = raw[:-1]
    elif corruption == "tail":
        raw += b"\n"
    elif corruption == "count":
        prefix = dataclasses.replace(prefix, message_count=0)
    else:
        if corruption == "header":
            raw = raw.replace(b'"generation":"serialized"', b'"generation":"other"', 1)
        elif corruption == "float-header":
            raw = raw.replace(b'"v":2', b'"v":2.0', 1)
        elif corruption == "header-duplicate":
            raw = raw.replace(b'"v":2', b'"v":2,"v":2', 1)
        elif corruption == "no-final-LF":
            raw = raw[:-1]
        elif corruption == "duplicate-ref-key":
            raw = raw.replace(b'"width":2', b'"width":2,"width":2', 1)
        elif corruption == "duplicate-opaque-key":
            raw = raw[:-2] + b',"opaque":{"key":1,"key":2}}\n'
        else:
            raw = raw.replace(b'"role":"tool"', b'"role":false', 1)
        prefix = dataclasses.replace(prefix, committed_bytes=len(raw),
                                     sha256=hashlib.sha256(raw).hexdigest())
    with pytest.raises(Refused):
        list(scan_refs(raw, prefix))


@pytest.mark.parametrize("which", ["after-last", "buffered-next", "zero-ref"])
def test_serialized_scan_rechecks_lifetime_after_yield_and_at_zero_ref_exit(which):
    from amplifier_foundation.session.versioned_checkpoint import scan_serialized_refs
    raw, prefix = serialized_rows([row([] if which == "zero-ref" else [fake_ref()] * 2)])
    live = [True]
    calls = [0]
    def check():
        calls[0] += 1
        if not live[0]:
            raise RuntimeError("prepared lifetime ended")
    if which == "zero-ref":
        class RevokedEOF(io.BytesIO):
            eof_checks = 0

            def read(self, size):
                data = super().read(size)
                if size == 1 and self.tell() == len(raw) and not data:
                    self.eof_checks += 1
                    # Allow preverification and the full zero-ref scan; revoke
                    # during the final integrity pass, not initial entry.
                    if self.eof_checks == 2:
                        live[0] = False
                return data
        refs = scan_serialized_refs(RevokedEOF(raw), prefix=prefix, check=check)
    else:
        refs = scan_refs(raw, prefix, check)
        assert next(refs) == fake_ref()
        if which == "after-last":
            assert next(refs) == fake_ref()
        live[0] = False
    with pytest.raises(RuntimeError, match="lifetime"):
        next(refs)
    assert calls[0] > 1


def test_serialized_scan_source_mutation_after_last_yield_and_late_schema_refuse():
    from amplifier_foundation.session.versioned_checkpoint import scan_serialized_refs
    raw, prefix = serialized_rows([row()])
    stream = io.BytesIO(raw)
    refs = scan_serialized_refs(stream, prefix=prefix, check=lambda: None)
    assert next(refs) == fake_ref()
    stream.seek(len(raw) - 3)
    stream.write(b"xxx")
    with pytest.raises(Refused):
        next(refs)
    raw, prefix = serialized_rows([row(), row(metadata={MEDIA: None})])
    refs = scan_refs(raw, prefix)
    assert next(refs) == fake_ref()
    with pytest.raises(Refused):
        next(refs)


def test_receipt_retains_unknown_envelope_key_compatibility(tmp_path):
    rows = [row(metadata={MEDIA: {"refs": [fake_ref()], "opaque-existing": "keep"}})]
    _, writer, ack = session(tmp_path, rows)
    assert list(acknowledge(writer, ack).iter_refs()) == [fake_ref()]
    raw, prefix = serialized_rows(rows)
    with pytest.raises(Refused):
        list(scan_refs(raw, prefix))


@pytest.mark.parametrize("shape", ["huge", "wide"])
def test_serialized_scan_bounded_reads_decode_and_memory(shape, monkeypatch):
    from amplifier_foundation.session._media_conversion_parser import CHUNK, Scanner
    from amplifier_foundation.session.versioned_checkpoint import scan_serialized_refs
    rows = [row([fake_ref()] * (1800 if shape == "wide" else 1),
                content="q" * ((8 if shape == "huge" else 2) * 1024 * 1024))]
    rows[0]["metadata"]["opaque"] = "m" * (2 * 1024 * 1024)
    raw, prefix = serialized_rows(rows)
    class Bounded(io.BytesIO):
        def read(self, size):
            assert type(size) is int and 0 <= size <= CHUNK
            return super().read(size)
    original = Scanner.small
    def small(scanner, span, limit=16384):
        value = original(scanner, span, limit)
        if isinstance(value, dict):
            assert "sha256" in value or set(value) == {"format", "generation", "v", "min_reader"}
        assert not isinstance(value, list)
        if isinstance(value, str):
            assert len(value) <= 4096
        return value
    monkeypatch.setattr(Scanner, "small", small)
    stream = Bounded(raw)
    del rows, raw
    tracemalloc.start()
    try:
        count = sum(1 for _ in scan_serialized_refs(stream, prefix=prefix, check=lambda: None))
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    assert not stream.closed
    assert count == (1800 if shape == "wide" else 1)
    assert peak < 4 * 1024 * 1024, peak
    print(f"serialized-memory {shape}: refs={count} peak_bytes={peak}")
