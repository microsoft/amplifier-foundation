"""Synthetic offline adapter tests; no live session, migration or resources."""

import asyncio
import base64
import hashlib
import io
import json
import os
from types import SimpleNamespace
from contextlib import contextmanager

import pytest

pytestmark = pytest.mark.skipif(os.name != "posix", reason="POSIX-only candidate durability qualification")

from amplifier_foundation.session.media_conversion import (
    NAMESPACE, Unsupported, adopt_selected_media, build_candidate, preview, reconcile,
    iter_verified_candidate_refs, transfer_prefix, verify_export,
)
from amplifier_foundation.session._media_conversion_parser import Scanner, _SPECIAL


@contextmanager
def fence(owner):
    assert owner == "fixture-owner"
    yield


def origin(row, block):
    return {
        "kind": "migration", "session_id": "fixture",
        "id": f"receipt-{row}-{block}", "created_at": "2025-01-01T00:00:00+00:00",
        "expires_at": "2025-01-01T00:00:01+00:00",
    }


class Media:
    """Fixture admission owner, not raster verification evidence."""
    def __init__(self):
        self.assets = {}
        self.calls = 0

    async def ingest(self, value, **kwargs):
        assert kwargs["source_format"] == "image-bytes"
        stream = value["stream"]
        data = bytearray()
        while chunk := stream.read(65536):
            data.extend(chunk)
        digest = hashlib.sha256(data).hexdigest()
        self.assets[digest] = bytes(data)
        self.calls += 1
        ref = {
            "v": 1, "session_id": "fixture", "sha256": digest,
            "bytes": len(data), "width": 1, "height": 1, "mime": value["mime"],
            "provenance": kwargs["origin"],
        }
        return {"value": ref, "staged_refs": [ref]}

    async def publish(self, refs, **kwargs):
        assert kwargs["disposition"] == "retain"
        return {"refs": refs, "publication": {"durability": "assets-only"}}

    @contextmanager
    def reader(self, ref):
        yield io.BytesIO(self.assets[ref["sha256"]])


def image(data=b"original image encoding", **kwargs):
    return {
        "type": "image",
        "source": {"type": "base64", "media_type": "image/png",
                   "data": base64.b64encode(data).decode()},
        **kwargs,
    }


def prepare(tmp_path, rows, **kwargs):
    path = tmp_path / "transcript.jsonl"
    path.write_bytes(b"".join(json.dumps(row, ensure_ascii=False).encode() + b"\n" for row in rows))
    return path, preview(path, owner="fixture-owner", session_id="fixture", fence=fence, **kwargs)


def build(plan, path, media=None, **kwargs):
    media = media or Media()
    result = asyncio.run(build_candidate(
        plan, path, fence=fence, capability=media, origin_for=origin,
        asset_reader=media.reader, **kwargs,
    ))
    return result, media


def test_semantic_binary_closure_and_original_backup(tmp_path):
    rows = [
        {"role": "assistant", "content": [{"type": "text", "text": "calling"}],
         "tool_calls": [{"id": "call-1", "function": {"arguments": '{"opaque":"text"}'}}],
         "usage": {"input_tokens": 32}, "provider_state": "\\u0000opaque=="},
        {"role": "tool", "tool_call_id": "call-1", "status": "failed",
         "content": [{"type": "text", "text": "before π"}, image(), image(visibility="internal"),
                     {"type": "text", "text": "after"}],
         "metadata": {"reasoning": {"signature": "never JSON parse"}, "unknown": "x" * 100000}},
    ]
    path, plan = prepare(tmp_path, rows)
    original = path.read_bytes()
    backup = tmp_path / "transcript.jsonl.backup"
    backup.write_bytes(b"old backup not canonical")
    plan = preview(path, owner="fixture-owner", session_id="fixture", fence=fence, backups=(backup,))
    result, media = build(plan, tmp_path / "operation")
    assert path.read_bytes() == original
    assert (tmp_path / "operation/original-0").read_bytes() == original
    assert (tmp_path / "operation/original-1").read_bytes() == backup.read_bytes()
    converted = [json.loads(line) for line in (tmp_path / "operation/candidate.jsonl").read_bytes().splitlines()]
    assert converted[0] == rows[0]
    refs = converted[1]["metadata"].pop(NAMESPACE)["refs"]
    assert len(refs) == 2 and refs[0]["sha256"] == refs[1]["sha256"]
    assert refs[0]["provenance"] != refs[1]["provenance"]
    assert media.assets[refs[0]["sha256"]] == b"original image encoding"
    assert converted[1]["metadata"] == rows[1]["metadata"]
    assert converted[1]["tool_call_id"] == "call-1"
    assert converted[1]["status"] == "failed"
    assert converted[1]["content"][2]["visibility"] == "internal"
    assert result.images == 2
    assert result.inline_removed_bytes == sum(
        len(json.dumps(block, ensure_ascii=False).encode()) for block in rows[1]["content"]
        if block["type"] == "image"
    )
    assert reconcile(tmp_path / "operation") == "verified-candidate-not-applied"
    assert "unconfirmed" in reconcile(tmp_path / "operation", installed=tmp_path / "operation/candidate.jsonl")


@pytest.mark.parametrize("count,selected_records", [(0, None), (1, None), (2, None), (2, 1)])
def test_inline_removed_bytes_exact_original_spans_and_saved_receipt(
    tmp_path, count, selected_records
):
    # Deliberately noncanonical whitespace: account source spans, not re-encoded
    # blocks, decoded pixels, metadata insertion or net transcript reduction.
    raw_block = (
        b'{ "type" : "image",  "source": { "type":"base64",'
        b' "media_type" : "image/png", "data" : "b3JpZ2luYWw=" } }'
    )
    assert len(raw_block) != len(json.dumps(json.loads(raw_block)).encode())
    row = b'{"role":"user","content":[' + raw_block + b'],"metadata":null}\n'
    original = row * count if count else b'{"role":"user","content":"plain text"}\n'
    source = tmp_path / "transcript.jsonl"
    source.write_bytes(original)
    plan = preview(
        source, owner="fixture-owner", session_id="fixture", fence=fence,
        selected_records=selected_records,
    )
    operation = tmp_path / "operation"
    result, _ = build(plan, operation)
    expected_count = count if selected_records is None else selected_records
    expected = expected_count * len(raw_block)
    receipt = json.loads((operation / "verified.json").read_bytes())
    assert result.images == receipt["images"] == expected_count
    assert result.inline_removed_bytes == receipt["inline_removed_bytes"] == expected
    assert result.decoded_bytes == receipt["decoded_bytes"] == expected_count * len(b"original")
    assert source.read_bytes() == original
    assert reconcile(operation) == "verified-candidate-not-applied"
    if expected:
        assert expected != result.original_bytes - result.bytes


@pytest.mark.parametrize("metadata", [None, {}, {"opaque": {"nested": [1, 2, "x"]}}])
def test_metadata_variants(tmp_path, metadata):
    path, plan = prepare(tmp_path, [{"role": "user", "content": [image()], "metadata": metadata}])
    build(plan, tmp_path / "op")
    value = json.loads((tmp_path / "op/candidate.jsonl").read_bytes())
    assert len(value["metadata"][NAMESPACE]["refs"]) == 1


def test_prefix_first_no_later_asset_or_fresh_expiry(tmp_path):
    path, plan = prepare(tmp_path, [
        {"role": "user", "content": [image(b"selected")]},
        {"role": "user", "content": [image(b"unselected")]},
    ], selected_records=1)
    result, media = build(plan, tmp_path / "op")
    assert len(media.assets) == media.calls == result.images == 1
    assert list(media.assets.values()) == [b"selected"]
    ref = json.loads((tmp_path / "op/candidate.jsonl").read_bytes())["metadata"][NAMESPACE]["refs"][0]
    assert ref["provenance"]["expires_at"] == origin(0, 0)["expires_at"]


def test_no_arbitrary_json_text_promotion(tmp_path):
    text = json.dumps({"content": [image()]})
    path, plan = prepare(tmp_path, [{"role": "tool", "content": text, "output": {"opaque": text}}])
    result, media = build(plan, tmp_path / "op")
    assert media.calls == result.images == 0
    assert (tmp_path / "op/candidate.jsonl").read_bytes() == path.read_bytes()


@pytest.mark.parametrize("source", [
    {"type": "url", "url": "https://invalid.example/image"},
    {"type": "base64", "data": "YQ==", "media_type": "image/gif"},
    {"type": "base64", "data": "YQ==", "media_type": "image/png", "future": True},
])
def test_unknown_source_refused_intact(tmp_path, source):
    path = tmp_path / "source"
    original = json.dumps({"role": "user", "content": [{"type": "image", "source": source}]}).encode()
    path.write_bytes(original)
    with pytest.raises(Unsupported):
        preview(path, owner="fixture-owner", session_id="fixture", fence=fence)
    assert path.read_bytes() == original


@pytest.mark.parametrize("raw", [
    b'{"role":"user",\n"content":"pretty"}\n',
    b'{"role":"user","content":"x","content":"y"}\n',
    b'{"role":"user","content":"x","\\u0063ontent":"y"}\n',
    b'{"role":"user","content":"\\ud800"}\n',
    b'{"role":"user","content":"\xff"}\n',
    b'{"role":"user","content":"x"}{}\n',
    b'{"format":"amplifier.session.transcript","v":2}\n',
    b'\n{"role":"user","content":"x"}\n',
])
def test_malformed_or_ambiguous_refused(tmp_path, raw):
    path = tmp_path / "source"
    path.write_bytes(raw)
    with pytest.raises(Unsupported):
        preview(path, owner="fixture-owner", session_id="fixture", fence=fence)
    assert path.read_bytes() == raw


@pytest.mark.parametrize("payload", ["YQ", "YR==", "YQ==Yg==", "YQ== ", "", "!!!!"])
def test_strict_base64(tmp_path, payload):
    block = image()
    block["source"]["data"] = payload
    path, plan = prepare(tmp_path, [{"role": "user", "content": [block]}])
    with pytest.raises(Unsupported):
        build(plan, tmp_path / "op")
    assert reconcile(tmp_path / "op") == "interrupted-preserve-and-inspect-journal"


def test_source_race_cancel_and_unknown_no_replay(tmp_path):
    path, plan = prepare(tmp_path, [{"role": "user", "content": [image()]}])
    original = path.read_bytes()
    path.write_bytes(original + b" ")
    with pytest.raises(Unsupported):
        build(plan, tmp_path / "stale")
    assert not (tmp_path / "stale").exists()
    path.write_bytes(original)
    plan = preview(path, owner="fixture-owner", session_id="fixture", fence=fence)
    with pytest.raises(InterruptedError):
        build(plan, tmp_path / "cancel", cancelled=lambda: True)
    assert path.read_bytes() == original
    with pytest.raises(FileExistsError):
        build(plan, tmp_path / "cancel")


def test_asset_fault_and_candidate_tamper(tmp_path):
    path, plan = prepare(tmp_path, [{"role": "user", "content": [image()]}])
    media = Media()
    @contextmanager
    def corrupt(ref):
        yield io.BytesIO(b"changed")
    with pytest.raises(Unsupported):
        asyncio.run(build_candidate(plan, tmp_path / "bad", fence=fence, capability=media,
                                    origin_for=origin, asset_reader=corrupt))
    assert media.calls == 1  # effect retained, never secretly replayed/rolled back
    build(plan, tmp_path / "good")
    with (tmp_path / "good/candidate.jsonl").open("ab") as out:
        out.write(b" ")
    with pytest.raises(Unsupported):
        reconcile(tmp_path / "good")


def test_json_string_chunk_boundaries():
    # Tiny reads force every escape/surrogate and UTF-8 sequence across buffers.
    class Tiny(io.BytesIO):
        def read(self, count=-1):
            assert 0 <= count <= 65536
            return super().read(min(count, 3))
    value = 'é𐀀 " \\ \n /'
    for ascii_only in (True, False):
        raw = json.dumps(value, ensure_ascii=ascii_only).encode()
        scanner = Scanner(Tiny(raw))
        assert b"".join(scanner.string_chunks()).decode() == value
    assert _SPECIAL.search(b"abcd\\ef").start() == 4


@pytest.mark.parametrize("count_limit", [10, None])
def test_export_independent_and_adopted_prefix(tmp_path, count_limit):
    path, plan = prepare(tmp_path, [
        {"role": "user", "content": [image(b"first")]},
        {"role": "user", "content": [image(b"last")]},
    ])
    media = Media()
    asyncio.run(build_candidate(plan, tmp_path / "export", fence=fence, capability=media,
                               origin_for=origin, asset_reader=media.reader, export_assets=True))
    verify_export(tmp_path / "export/portable")
    converted = tmp_path / "export/candidate.jsonl"
    selected = preview(converted, owner="fixture-owner", session_id="fixture", fence=fence,
                       selected_records=1)
    class Target:
        policy = SimpleNamespace(max_images=count_limit)
        def __init__(self):
            self.values = []
            self.assets = {}
        async def adopt(self, refs, source_store):
            assert source_store is media  # actual source-owner identity, not a path
            self.values.extend(refs)
            for ref in refs:
                self.assets[ref["sha256"]] = source_store.assets[ref["sha256"]]
            return [{**ref, "session_id": "child"} for ref in refs]
        @contextmanager
        def reader(self, ref):
            assert ref["session_id"] == "child"
            yield io.BytesIO(self.assets[ref["sha256"]])
    target = Target()
    result = asyncio.run(transfer_prefix(
        selected, tmp_path / "fork", fence=fence, source_store=media,
        target_store=target, target_session_id="child", asset_reader=target.reader,
    ))
    assert result.records == len(target.values) == 1
    forked = json.loads((tmp_path / "fork/candidate.jsonl").read_bytes())
    ref = forked["metadata"][NAMESPACE]["refs"][0]
    assert ref["session_id"] == "child"
    assert ref["provenance"] == target.values[0]["provenance"]
    assert (tmp_path / "fork/portable/assets" / ref["sha256"]).read_bytes() == b"first"
    # Portable readback uses its own files, independent of source store lifetime.
    media.assets.clear()
    target.assets.clear()
    verify_export(tmp_path / "export/portable")
    verify_export(tmp_path / "fork/portable")
    (tmp_path / "fork/portable/assets" / ref["sha256"]).write_bytes(b"tamper")
    with pytest.raises(Unsupported):
        verify_export(tmp_path / "fork/portable")


def test_slot_collision_and_unqualified_nested_content(tmp_path):
    for content in [
        [{"type": "text", "text": "[Session image " + "a" * 64 + "]"}, image()],
        [{"type": "tool_result", "tool_call_id": "x", "output": {"content": [image()]}}],
    ]:
        path = tmp_path / "source"
        raw = json.dumps({"role": "tool", "content": content}).encode()
        path.write_bytes(raw)
        with pytest.raises(Unsupported):
            preview(path, owner="fixture-owner", session_id="fixture", fence=fence)
        assert path.read_bytes() == raw


@pytest.mark.parametrize("change", ["replace", "overwrite", "truncate", "symlink"])
def test_source_identity_races(tmp_path, change):
    path, plan = prepare(tmp_path, [{"role": "user", "content": [image()]}])
    original = path.read_bytes()
    if change == "replace":
        other = tmp_path / "other"
        other.write_bytes(original)
        other.replace(path)
    elif change == "overwrite":
        path.write_bytes(original.replace(b"user", b"tool"))
    elif change == "truncate":
        path.write_bytes(original[:10])
    else:
        other = tmp_path / "other"
        other.write_bytes(original)
        path.unlink()
        path.symlink_to(other)
    with pytest.raises(Unsupported):
        build(plan, tmp_path / "operation")
    assert not (tmp_path / "operation").exists()


def test_cancel_after_stage_and_lost_publish_ack(tmp_path):
    path, plan = prepare(tmp_path, [{"role": "user", "content": [image()]}])
    class LostAck(Media):
        async def publish(self, refs, **kwargs):
            await super().publish(refs, **kwargs)
            raise TimeoutError("unknown effect")
    media = LostAck()
    with pytest.raises(TimeoutError):
        build(plan, tmp_path / "unknown", media)
    journal = (tmp_path / "unknown/journal.jsonl").read_text()
    assert "publication-intent" in journal
    assert "published-assets-only" not in journal
    assert reconcile(tmp_path / "unknown") == "interrupted-preserve-and-inspect-journal"
    with pytest.raises(FileExistsError):
        build(plan, tmp_path / "unknown", media)
    assert media.calls == 1
    staged = Media()
    with pytest.raises(InterruptedError):
        build(plan, tmp_path / "cancel-stage", staged, cancelled=lambda: staged.calls > 0)
    assert staged.calls == 1
    assert "publication-intent" not in (tmp_path / "cancel-stage/journal.jsonl").read_text()


def test_source_changes_during_ingest_preserves_both_versions(tmp_path):
    path, plan = prepare(tmp_path, [{"role": "user", "content": [image()]}])
    original = path.read_bytes()
    class Racing(Media):
        async def ingest(self, value, **kwargs):
            result = await super().ingest(value, **kwargs)
            path.write_bytes(original.replace(b"user", b"tool"))
            return result
    with pytest.raises(Unsupported):
        build(plan, tmp_path / "race", Racing())
    assert (tmp_path / "race/original-0").read_bytes() == original
    assert path.read_bytes() != original
    assert not (tmp_path / "race/verified.json").exists()


@pytest.mark.parametrize("boundary", ["staged", "assets-verified", "verified-candidate-not-applied"])
def test_fault_at_journal_boundaries_never_replaces_source(tmp_path, monkeypatch, boundary):
    from amplifier_foundation.session import media_conversion as conversion
    path, plan = prepare(tmp_path, [{"role": "user", "content": [image()]}])
    original = path.read_bytes()
    saved = conversion._event
    def fail(journal, **value):
        if value.get("state") == boundary:
            raise OSError("injected journal/finalization fault")
        return saved(journal, **value)
    monkeypatch.setattr(conversion, "_event", fail)
    with pytest.raises(OSError):
        build(plan, tmp_path / "fault")
    assert path.read_bytes() == original
    assert (tmp_path / "fault/original-0").read_bytes() == original
    # A finalization failure may leave a fully verified candidate, never an
    # applied source or implicit media checkpoint.
    assert "applied" not in reconcile(tmp_path / "fault").replace("not-applied", "")


@pytest.mark.parametrize("field,value", [
    ("v", True), ("v", 2), ("bytes", True), ("width", 0), ("sha256", "../unsafe"),
    ("session_id", "../foreign"),
])
def test_existing_invalid_refs_fail_before_effect(tmp_path, field, value):
    ref = {"v": 1, "session_id": "fixture", "sha256": "a" * 64, "mime": "image/png",
           "bytes": 1, "width": 1, "height": 1, "provenance": origin(0, 0), field: value}
    raw = json.dumps({"role": "user", "content": "legacy",
                      "metadata": {NAMESPACE: {"refs": [ref]}}}).encode()
    path = tmp_path / "source"
    path.write_bytes(raw)
    with pytest.raises(Unsupported):
        preview(path, owner="fixture-owner", session_id="fixture", fence=fence)
    assert path.read_bytes() == raw


def test_schema_bounds_explicit_not_unbounded_materialization(tmp_path):
    for raw in [
        b'{"role":"user","content":"x","metadata":{' + b'"key":"' + b"x" * 5000 + b'"' +
        b',"other":[' + b"[" * 130 + b"0" + b"]" * 130 + b"]}}\n",
        json.dumps({"role": "user", "content": "text", **{f"k{i}": i for i in range(257)}}).encode(),
        b'{"role":"user","content":"text","' + b"k" * 5000 + b'":0}\n',
    ]:
        path = tmp_path / "source"
        path.write_bytes(raw)
        with pytest.raises(Unsupported):
            preview(path, owner="fixture-owner", session_id="fixture", fence=fence)


def test_portable_prefix_excludes_all_suffix_and_backup_bytes(tmp_path):
    secret = b"unselected-capture-secret"
    path, _ = prepare(tmp_path, [
        {"role": "user", "content": "selected"},
        {"role": "user", "content": [image(secret)]},
    ])
    backup = tmp_path / "backup"
    backup.write_bytes(b"backup-private-secret")
    plan = preview(path, owner="fixture-owner", session_id="fixture", fence=fence,
                   selected_records=1, backups=(backup,))
    result, _ = build(plan, tmp_path / "op", export_assets=True)
    portable = tmp_path / "op/portable"
    verify_export(portable)
    assert result.images == 0
    for item in portable.rglob("*"):
        if item.is_file():
            raw = item.read_bytes()
            assert secret not in raw
            assert base64.b64encode(secret) not in raw
            assert b"backup-private-secret" not in raw
    assert (tmp_path / "op/original-0").read_bytes() == path.read_bytes()
    assert (tmp_path / "op/original-1").read_bytes() == backup.read_bytes()
    with pytest.raises(Unsupported):
        verify_export(tmp_path / "op")  # private recovery directory is NOT delivery


def test_multiple_same_record_adoptions_stop_immediately_on_cancel(tmp_path):
    path, plan = prepare(tmp_path, [{"role": "user", "content": [image(b"a"), image(b"b")]}])
    _, media = build(plan, tmp_path / "source")
    selected = preview(tmp_path / "source/candidate.jsonl", owner="fixture-owner",
                       session_id="fixture", fence=fence)
    class CancelTarget:
        policy = SimpleNamespace(max_images=10)
        calls = 0
        async def adopt(self, refs, source_store):
            self.calls += 1
            return [{**refs[0], "session_id": "child"}]
    target = CancelTarget()
    with pytest.raises(InterruptedError):
        asyncio.run(transfer_prefix(
            selected, tmp_path / "fork", fence=fence, source_store=media,
            target_store=target, target_session_id="child", asset_reader=media.reader,
            cancelled=lambda: target.calls > 0,
        ))
    assert target.calls == 1


def test_nonarray_ref_conversion_is_explicitly_refused(tmp_path):
    ref = {"v": 1, "session_id": "fixture", "sha256": "a" * 64, "mime": "image/png",
           "bytes": 1, "width": 1, "height": 1, "provenance": origin(0, 0)}
    path = tmp_path / "source"
    path.write_text(json.dumps({"role": "user", "content": "no exact slot",
                               "metadata": {NAMESPACE: {"refs": [ref]}}}))
    with pytest.raises(Unsupported, match="explicit canonical image slots"):
        preview(path, owner="fixture-owner", session_id="fixture", fence=fence)


def test_sync_selected_helper_metadata_expiry_and_failure_no_mutation():
    import copy
    from datetime import datetime, timezone
    ref = {"v": 1, "session_id": "fixture", "sha256": "a" * 64, "mime": "image/png",
           "bytes": 1, "width": 1, "height": 1, "provenance": origin(0, 0)}
    message = {
        "role": "tool", "tool_call_id": "call1", "usage": {"input": 10}, "status": "failed",
        "content": [{"type": "text", "text": "before"},
                    {"type": "text", "text": "[Session image " + ref["sha256"] + "]"},
                    {"type": "text", "text": "after"}],
        "metadata": {NAMESPACE: {"refs": [ref]}, "opaque": {"reasoning": "signature"}},
    }
    original = copy.deepcopy(message)
    class Source:
        def validate_ref(self, value, *, committed, check_expiry):
            assert committed is True
            assert check_expiry is False
            assert value == ref
    class Target:
        policy = SimpleNamespace(max_images=None)
        calls = 0
        def adopt_sync(self, refs, source_store):
            self.calls += 1
            return [{**refs[0], "session_id": "child"}]
    source, target = Source(), Target()
    before_expiry = datetime(2025, 1, 1, tzinfo=timezone.utc)
    result = adopt_selected_media([message], source_store=source, target_store=target,
                                  target_session_id="child", now=before_expiry)
    assert result[0]["metadata"][NAMESPACE]["refs"][0]["session_id"] == "child"
    assert result[0]["metadata"][NAMESPACE]["refs"][0]["provenance"] == ref["provenance"]
    assert result[0]["usage"] == message["usage"]
    assert message == original and target.calls == 1
    expired = adopt_selected_media([message], source_store=source, target_store=target,
                                   target_session_id="child",
                                   now=datetime(2025, 1, 2, tzinfo=timezone.utc))
    assert expired[0]["metadata"][NAMESPACE]["refs"] == []
    assert "expired" in expired[0]["content"][1]["text"]
    assert target.calls == 1 and message == original
    with pytest.raises(Unsupported):
        adopt_selected_media([message], source_store=None, target_store=None,
                             target_session_id="child", now=before_expiry)
    class Failed(Target):
        def adopt_sync(self, refs, source_store):
            raise OSError("failed copy")
    with pytest.raises(OSError):
        adopt_selected_media([message], source_store=source, target_store=Failed(),
                             target_session_id="child", now=before_expiry)
    assert message == original


@pytest.mark.parametrize("phase,raises", [
    ("validate", False), ("validate", True), ("adopt", False), ("adopt", True),
])
def test_mutating_sync_callbacks_cannot_change_original_or_extend_ttl(phase, raises):
    import copy
    from datetime import datetime, timezone
    ref = {"v": 1, "session_id": "fixture", "sha256": "a" * 64, "mime": "image/png",
           "bytes": 1, "width": 1, "height": 1, "provenance": origin(0, 0)}
    message = {"role": "tool", "content": [{"type": "text", "text": "[Session image " + "a" * 64 + "]"}],
               "metadata": {NAMESPACE: {"refs": [ref]}}}
    original = copy.deepcopy(message)
    class Source:
        def validate_ref(self, value, **kwargs):
            if phase == "validate":
                value["provenance"]["expires_at"] = "2099-01-01T00:00:00+00:00"
                if raises:
                    raise OSError("mutated then failed validation")
    class Target:
        policy = SimpleNamespace(max_images=None)
        def adopt_sync(self, refs, source_store):
            refs[0]["session_id"] = "child"
            if phase == "adopt":
                refs[0]["provenance"]["expires_at"] = "2099-01-01T00:00:00+00:00"
                if raises:
                    raise OSError("mutated then failed adoption")
            return refs
    kwargs = dict(source_store=Source(), target_store=Target(), target_session_id="child",
                  now=datetime(2025, 1, 1, tzinfo=timezone.utc))
    if raises:
        with pytest.raises(OSError):
            adopt_selected_media([message], **kwargs)
    elif phase == "adopt":
        with pytest.raises(Unsupported):
            adopt_selected_media([message], **kwargs)
    else:
        result = adopt_selected_media([message], **kwargs)
        assert result[0]["metadata"][NAMESPACE]["refs"][0]["provenance"] == ref["provenance"]
    assert message == original


@pytest.mark.parametrize("policy", [
    None, {}, object(), SimpleNamespace(),
    *[SimpleNamespace(max_images=value) for value in
      (True, False, 0, -1, 1.0, "2", float("inf"), float("nan"))],
])
def test_transfer_refuses_missing_or_malformed_policy_before_operation(tmp_path, policy):
    _, plan = prepare(tmp_path, [{"role": "user", "content": "no inferred authority"}])
    target = SimpleNamespace(policy=policy)
    with pytest.raises(Unsupported, match="host policy|Host image count"):
        asyncio.run(transfer_prefix(
            plan, tmp_path / "never-started", fence=fence, source_store=object(),
            target_store=target, target_session_id="child",
            asset_reader=lambda _: pytest.fail("no asset access"),
        ))
    assert not (tmp_path / "never-started").exists()


def test_mutating_async_adoption_cannot_change_expiry(tmp_path):
    path, plan = prepare(tmp_path, [{"role": "user", "content": [image()]}])
    _, media = build(plan, tmp_path / "source")
    selected = preview(tmp_path / "source/candidate.jsonl", owner="fixture-owner",
                       session_id="fixture", fence=fence)
    class Mutating:
        policy = SimpleNamespace(max_images=10)
        async def adopt(self, refs, source_store):
            refs[0]["session_id"] = "child"
            refs[0]["provenance"]["expires_at"] = "2099-01-01T00:00:00+00:00"
            return refs
    with pytest.raises(Unsupported, match="original provenance/expiry"):
        asyncio.run(transfer_prefix(
            selected, tmp_path / "bad", fence=fence, source_store=media, target_store=Mutating(),
            target_session_id="child", asset_reader=media.reader,
        ))
    assert not (tmp_path / "bad/verified.json").exists()


def test_bounded_installed_candidate_enumerator_final_recheck(tmp_path):
    path, plan = prepare(tmp_path, [{"role": "user", "content": [image()]}])
    _, media = build(plan, tmp_path / "op")
    installed = tmp_path / "installed"
    installed.write_bytes((tmp_path / "op/candidate.jsonl").read_bytes())
    refs = iter_verified_candidate_refs(
        tmp_path / "op", installed=installed, session_id="fixture", asset_reader=media.reader,
    )
    assert next(refs)["session_id"] == "fixture"
    with installed.open("ab") as output:
        output.write(b" ")
    with pytest.raises(Unsupported):
        next(refs)  # Exhaustion is mandatory before a checkpoint is bound.


def test_group_transfer_collection_cancellation_does_not_scan_remaining_prefix(monkeypatch):
    import sqlite3
    from amplifier_foundation.session import media_conversion as conversion
    db = sqlite3.connect(":memory:")
    db.execute("create table transfer_refs(row,start,end,origin,ref,adopted)")
    seen = 0
    class Source:
        def records(self):
            yield object()
    monkeypatch.setattr(conversion, "_message", lambda *_: {})
    def refs(*_):
        nonlocal seen
        for index in range(200):
            seen += 1
            yield SimpleNamespace(start=index, end=index + 1), {"provenance": origin(0, index)}
    monkeypatch.setattr(conversion, "_refs", refs)
    async def forbidden(*_):
        raise AssertionError("Cancelled collection must never adopt")
    try:
        with pytest.raises(InterruptedError, match="reference collection"):
            asyncio.run(conversion._adopt_existing_groups(
                Source(), 1, db, forbidden, "child", 1000, io.BytesIO(), lambda: seen > 0,
            ))
        assert seen == 1
        assert db.execute("select count(*) from transfer_refs").fetchone()[0] == 0
    finally:
        db.close()