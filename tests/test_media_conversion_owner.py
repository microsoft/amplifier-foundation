"""Exact F1-candidate integration for canonical and CI protocol adapters."""

import asyncio
import base64
import hashlib
import io
import json
import os
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import pytest

pytestmark = pytest.mark.skipif(os.name != "posix", reason="POSIX F1 store integration")
pytest.importorskip("PIL")
from PIL import Image

from amplifier_foundation.session.media import MediaPolicy, MediaRefusal, SessionMediaStore
from amplifier_foundation.session.media_conversion import (
    NAMESPACE, Unsupported, adopt_selected_media, build_candidate, preview, transfer_prefix, verify_export,
)
from amplifier_foundation.session.media_conversion_owner import (
    BINDING, CaptureMediaOwner, diagnostic_image_ref,
)
from amplifier_foundation.session.checkpoint import (
    acknowledge_candidate_checkpoint, verified_checkpoint_refs,
)


def png():
    value = io.BytesIO()
    Image.new("RGB", (3, 2), "red").save(value, format="PNG")
    return value.getvalue()


def original(identity="historical-fixture"):
    return {"kind": "migration", "session_id": "source", "id": identity,
            "created_at": "2025-01-01T00:00:00+00:00", "expires_at": None}


@contextmanager
def fence(owner):
    assert owner == "fixture"
    yield


@pytest.fixture
def stores(tmp_path):
    policy = MediaPolicy(1024 * 1024, 10000, 10)
    source = SessionMediaStore(tmp_path / "source-store", "source", policy=policy,
                               checkpoint_verifier=lambda receipt: receipt["refs"])
    target = SessionMediaStore(tmp_path / "target-store", "target", policy=policy,
                               checkpoint_verifier=lambda receipt: receipt["refs"])
    try:
        yield source, target
    finally:
        target.close()
        source.close()


def test_real_f1_canonical_conversion_publish_ack_and_prefix(stores, tmp_path):
    source_store, target = stores
    pixels = png()
    row = {"role": "tool", "tool_call_id": "call", "status": "failed", "usage": {"input": 2},
           "content": [{"type": "image", "source": {"type": "base64", "media_type": "image/png",
                                                   "data": base64.b64encode(pixels).decode()}}]}
    path = tmp_path / "source.jsonl"
    path.write_text(json.dumps(row) + "\n")
    plan = preview(path, owner="fixture", session_id="source", fence=fence)
    asyncio.run(build_candidate(plan, tmp_path / "op", fence=fence, capability=source_store,
                                origin_for=lambda *_: original(),
                                asset_reader=lambda ref: source_store.resolve_ref(ref, committed=False),
                                export_assets=True))
    candidate = json.loads((tmp_path / "op/candidate.jsonl").read_bytes())
    ref = candidate["metadata"][NAMESPACE]["refs"][0]
    assert ref["sha256"] == hashlib.sha256(pixels).hexdigest()
    assert (ref["width"], ref["height"]) == (3, 2)
    with pytest.raises(MediaRefusal):
        source_store.resolve_ref(ref)  # Assets-only candidate never commits itself.
    source_store.acknowledge_checkpoint({"refs": [ref]})  # Explicit fixture host verifier.
    with source_store.resolve_ref(ref) as stream:
        assert stream.read() == pixels
    child = adopt_selected_media([candidate], source_store=source_store, target_store=target,
                                 target_session_id="target")
    adopted = child[0]["metadata"][NAMESPACE]["refs"][0]
    assert adopted == {**ref, "session_id": "target"}
    with pytest.raises(MediaRefusal):
        target.resolve_ref(adopted)
    target.acknowledge_checkpoint({"refs": [adopted]})
    with target.resolve_ref(adopted) as stream:
        assert stream.read() == pixels
    verify_export(tmp_path / "op/portable")


def test_real_owner_candidate_checkpoint_with_huge_opaque_field(stores, tmp_path):
    import tracemalloc
    store, _ = stores
    store.checkpoint_verifier = verified_checkpoint_refs
    session = tmp_path / "actual"
    session.mkdir()
    path = session / "transcript.jsonl"
    pixels = png()
    with path.open("wb") as output:
        output.write(b'{"role":"tool","tool_call_id":"huge","metadata":{"opaque":"')
        for _ in range(512):
            output.write(b"x" * 65536)
        output.write(b'"},"content":[{"type":"image","source":{"type":"base64","media_type":"image/png","data":"')
        output.write(base64.b64encode(pixels))
        output.write(b'"}}]}\n')
    tracemalloc.start()
    plan = preview(path, owner="fixture", session_id="source", fence=fence)
    asyncio.run(build_candidate(
        plan, tmp_path / "candidate-op", fence=fence, capability=store,
        origin_for=lambda *_: original(), asset_reader=lambda ref: store.resolve_ref(ref, committed=False),
    ))
    # Explicit synthetic caller apply, never a live migration.
    import shutil
    shutil.copyfile(tmp_path / "candidate-op/candidate.jsonl", path)
    receipt = acknowledge_candidate_checkpoint(
        tmp_path / "candidate-op", session, session_id="source",
        asset_reader=lambda ref: store.resolve_ref(ref, committed=False),
    )
    refs = list(verified_checkpoint_refs(receipt))
    assert len(refs) == 1
    store.acknowledge_checkpoint(receipt)
    with store.resolve_ref(refs[0]) as source:
        assert source.read() == pixels
    _, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    print(json.dumps({"actual_checkpoint_body_bytes": path.stat().st_size,
                      "actual_f1_ack_python_peak_bytes": peak}))
    assert path.stat().st_size > 32 * 1024 * 1024
    assert peak < 6 * 1024 * 1024


@dataclass
class Staged:
    replacement: dict
    receipt: dict
    new_asset_bytes: int


def owner(store, *, policy=None, reader_ready=lambda _: True, receipt=None, accounting=None,
          single_image_origin=lambda *_: True, factory=Staged):
    return CaptureMediaOwner(
        store, staged_factory=factory, origin_for=policy or (lambda _: original()),
        checkpoint_receipt_for=receipt or (lambda manifest, **kwargs: {
            "refs": [json.loads(line)["owner"]["ref"] for line in manifest.read_bytes().splitlines()]
        }),  # Small fixture verifier only; production must page/exhaust exact source.
        new_asset_bytes_for=accounting or (lambda ref: ref["bytes"]),
        reader_ready=reader_ready,
        single_image_origin=single_image_origin,
    )


def snapshot(pixels):
    return {"session_id": "source", "source_sha256": "b" * 64, "ordinal": 0,
            "event": "tool:post", "image_offset": 150, "sha256": hashlib.sha256(pixels).hexdigest(),
            "bytes": len(pixels), "timestamp": "2025-01-01T00:00:00+00:00", "ts": None,
            "tool_call_id": "call", "id": "evt", "event_id": "evt"}


def test_real_f1_ci_protocol_stage_publish_verify_ack(stores, tmp_path):
    store, _ = stores
    binding = owner(store)
    pixels = png()
    source = snapshot(pixels)
    staged = binding.stage_image(io.BytesIO(pixels), mime="image/png", origin=source)
    ref = diagnostic_image_ref(staged.replacement)
    assert ref["provenance"] == original()
    assert staged.new_asset_bytes == len(pixels)
    manifest = tmp_path / "assets.jsonl"
    manifest.write_text(json.dumps({"origin": source, "owner": staged.receipt}) + "\n")
    binding.publish(manifest)
    binding.publish(manifest)  # F1's exact saved publication reconciles no new observation.
    binding.verify(manifest)
    with pytest.raises(MediaRefusal):
        store.resolve_ref(ref)
    installed = tmp_path / "diagnostic.jsonl"
    installed.write_text(json.dumps({"image": staged.replacement}))
    binding.acknowledge(manifest, source=installed, sha256=hashlib.sha256(installed.read_bytes()).hexdigest())
    with store.resolve_ref(ref) as stream:
        assert stream.read() == pixels
    assert source == snapshot(pixels)


def test_binding_refuses_unknown_policy_reader_loop_and_source_drift(stores, tmp_path):
    store, _ = stores
    pixels = png()
    with pytest.raises(Unsupported):
        owner(store, policy=lambda _: {}).stage_image(io.BytesIO(pixels), mime="image/png", origin=snapshot(pixels))
    with pytest.raises(Unsupported):
        owner(store, reader_ready=lambda _: False).stage_image(io.BytesIO(pixels), mime="image/png", origin=snapshot(pixels))
    bad = {**snapshot(pixels), "bytes": 123}
    with pytest.raises(Unsupported):
        owner(store).stage_image(io.BytesIO(pixels), mime="image/png", origin=bad)
    async def on_loop():
        with pytest.raises(Unsupported, match="off the live"):
            owner(store).stage_image(io.BytesIO(pixels), mime="image/png", origin=snapshot(pixels))
    asyncio.run(on_loop())
    expired = {**original(), "expires_at": "2025-01-01T00:00:01+00:00"}
    with pytest.raises(MediaRefusal):
        owner(store, policy=lambda _: expired).stage_image(
            io.BytesIO(pixels), mime="image/png", origin=snapshot(pixels),
        )  # Unknown/expired ephemeral policy is refused, never renewed.


def test_ci_manifest_tamper_forgery_and_ack_hash(stores, tmp_path):
    store, _ = stores
    binding = owner(store)
    pixels, image_origin = png(), snapshot(png())
    staged = binding.stage_image(io.BytesIO(pixels), mime="image/png", origin=image_origin)
    manifest = tmp_path / "manifest"
    row = {"origin": image_origin, "owner": staged.receipt}
    manifest.write_text(json.dumps(row) + "\n")
    binding.publish(manifest)
    row["origin"]["ordinal"] = 2
    manifest.write_text(json.dumps(row) + "\n")
    with pytest.raises(Unsupported):
        binding.verify(manifest)
    row["origin"]["ordinal"] = 0
    row["owner"]["ref"]["provenance"]["id"] = "forged"
    manifest.write_text(json.dumps(row) + "\n")
    with pytest.raises((Unsupported, MediaRefusal)):
        binding.publish(manifest)


def test_sync_real_expired_committed_ref_no_pixel_copy(stores, tmp_path, monkeypatch):
    store, target = stores
    pixels = png()
    clock = datetime.now(timezone.utc)
    provenance = {**original(), "expires_at": (clock + timedelta(seconds=60)).isoformat()}
    ref = asyncio.run(store.ingest(pixels, source_format="image-bytes", origin=provenance, policy=None))["value"]
    asyncio.run(store.publish([ref], origin=provenance, disposition="retain"))
    store.acknowledge_checkpoint({"refs": [ref]})
    # The helper's clock can classify an original future expiry without rewriting
    # store bytes/records. Actual wallclock expiry source-store tests own that seam.
    def no_adopt(*_):
        raise AssertionError("Expired helper must not adopt/copy pixels")
    monkeypatch.setattr(target, "adopt_sync", no_adopt)
    result = adopt_selected_media(
        [{"role": "user", "content": "legacy", "metadata": {NAMESPACE: {"refs": [ref]}}}],
        source_store=store, target_store=target, target_session_id="target",
        now=clock + timedelta(seconds=120),
    )
    assert result[0]["metadata"][NAMESPACE]["refs"] == []
    assert "expired" in result[0]["content"]


def test_factory_mutation_and_retained_alias_are_not_returned(stores):
    store, _ = stores
    held = []
    def mutate(**values):
        values["replacement"]["metadata"][NAMESPACE]["refs"][0]["sha256"] = "0" * 64
        return Staged(**values)
    pixels = png()
    with pytest.raises(Unsupported, match="factory changed"):
        owner(store, factory=mutate).stage_image(io.BytesIO(pixels), mime="image/png", origin=snapshot(pixels))
    def retain(**values):
        held.append(values)
        return Staged(**values)
    result = owner(store, factory=retain).stage_image(io.BytesIO(pixels), mime="image/png", origin=snapshot(pixels))
    held[0]["replacement"]["metadata"][NAMESPACE]["refs"][0]["sha256"] = "0" * 64
    held[0]["receipt"]["ref"]["provenance"]["expires_at"] = "2099-01-01T00:00:00+00:00"
    assert diagnostic_image_ref(result.replacement)["sha256"] == hashlib.sha256(pixels).hexdigest()
    assert result.receipt["ref"]["provenance"]["expires_at"] is None


def test_grouped_origin_preflight_refuses_before_staging(stores, monkeypatch):
    store, _ = stores
    async def forbidden(*_, **__):
        raise AssertionError("grouped source must refuse before any ingest")
    monkeypatch.setattr(store, "ingest_sync", forbidden)
    pixels = png()
    with pytest.raises(Unsupported, match="cardinality"):
        owner(store, single_image_origin=lambda *_: False).stage_image(
            io.BytesIO(pixels), mime="image/png", origin=snapshot(pixels),
        )


def test_actual_allocated_bytes_after_publication_and_dedup(stores):
    store, _ = stores
    calls = 0
    def allocated(ref):
        nonlocal calls
        # Measurement is after publication, not guessed from staged length.
        with store.resolve_ref(ref, committed=False) as source:
            assert source.read() == png()
        calls += 1
        return 4096 if calls == 1 else 0
    pixels = png()
    binding = owner(store, accounting=allocated)
    first = binding.stage_image(io.BytesIO(pixels), mime="image/png", origin=snapshot(pixels))
    second = binding.stage_image(io.BytesIO(pixels), mime="image/png", origin=snapshot(pixels))
    assert first.new_asset_bytes == 4096 > len(pixels)
    assert second.new_asset_bytes == 0