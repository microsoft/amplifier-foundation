"""Synthetic prospective T2 source tests; no live history or second writer."""
import asyncio
from contextlib import closing, contextmanager
from dataclasses import asdict, replace
import hashlib
import io
import json
import sys

import pytest

from amplifier_foundation.session.checkpoint import verified_media_checkpoint_refs
from amplifier_foundation.session.media import SessionMediaStore
from amplifier_foundation.session.media_conversion import (
    Unsupported, build_candidate, candidate_facts, open_verified_candidate,
    preview, preview_committed, reconcile, verify_export,
    iter_serialized_candidate_refs,
)
from amplifier_foundation.session.transcript_lifetime import VersionedSessionBinding
from amplifier_foundation.session.transcript_source import open_committed_source

pytestmark = pytest.mark.skipif(sys.platform != 'linux', reason='Actual prospective custody is Linux qualified')


@contextmanager
def fixture(tmp_path):
    tmp_path.chmod(0o700)
    with closing(VersionedSessionBinding.create_new(
        tmp_path / 'session', session_id='session', operation_id='initial', admit=lambda: None,
    )) as binding:
        with closing(SessionMediaStore(binding.directory / 'media', binding.session_id,
                                       checkpoint_verifier=verified_media_checkpoint_refs)) as store:
            @contextmanager
            def fence(owner):
                assert owner == 'fixture'
                with binding.publication():
                    yield
            yield binding, store, fence


def png():
    from PIL import Image
    stream = io.BytesIO()
    Image.new('RGB', (2, 3), (12, 45, 96)).save(stream, 'PNG')
    return stream.getvalue()


def published(store):
    original = {'kind': 'tool', 'session_id': store.session_id, 'id': 'fixture'}
    ref = store.ingest_sync(png(), source_format='image-bytes', origin=original,
                            policy=None)['staged_refs'][0]
    store.publish_sync([ref], origin=original, disposition='retain')
    return ref


def build(plan, directory, store, fence, **kwargs):
    return asyncio.run(build_candidate(
        plan, directory, fence=fence, capability=store,
        origin_for=lambda row, block: (_ for _ in ()).throw(AssertionError('No guessed policy')),
        asset_reader=lambda ref: store.resolve_ref(ref, committed=False), **kwargs,
    ))


def test_actual_prospective_refs_first_preview_candidate_export(tmp_path):
    with fixture(tmp_path) as (binding, store, fence):
        ref = published(store)
        rows = [{'role': 'tool', 'content': 'owned reference', 'tool_call_id': 'call-1',
                 'status': 'complete', 'usage': {'input_tokens': 9},
                 'metadata': {'experimental.media.v1': {'refs': [ref]}, 'opaque': {'keep': True}}}]
        binding.append(rows, expected=binding.expected, operation_id='seed',
                       admit=lambda: None, media_store=store)
        expected = binding.expected
        with pytest.raises(IsADirectoryError):
            preview(binding.directory / 'transcript.jsonl', owner='fixture', session_id='session', fence=fence)
        original = (binding.directory / 'transcript.v2' / expected.body).read_bytes()
        # Public committed prefix excludes an uncommitted physical suffix.
        with (binding.directory / 'transcript.v2' / expected.body).open('ab') as stream:
            stream.write(b'PRIVATE UNCOMMITTED TAIL')
        plan = preview_committed(binding.directory, expected=expected, owner='fixture', fence=fence)
        assert (plan.records, plan.inline_images, plan.existing_refs) == (1, 0, 1)
        result = build(plan, tmp_path / 'operation', store, fence, export_assets=True)
        assert result.inline_removed_bytes == result.decoded_bytes == result.held_records == 0
        assert result.committed_prefix == asdict(expected)
        candidate = (tmp_path / 'operation/candidate.jsonl').read_bytes()
        assert candidate == original
        assert (tmp_path / 'operation/original-0').read_bytes() == original
        assert b'PRIVATE UNCOMMITTED TAIL' not in candidate
        assert json.loads(candidate.splitlines()[1]) == rows[0]
        assert reconcile(tmp_path / 'operation') == 'verified-candidate-not-applied'
        verify_export(tmp_path / 'operation/portable')
        with open_verified_candidate(tmp_path / 'operation', session_id='session',
                                     asset_reader=lambda ref: store.resolve_ref(ref, committed=False)) as (stream, prefix, check):
            assert stream.read() == original
            assert prefix == expected
            check()
        assert binding.expected == expected


def test_selected_prefix_keeps_header_excludes_suffix_asset(tmp_path):
    with fixture(tmp_path) as (binding, store, fence):
        ref = published(store)
        binding.append([
            {'role': 'user', 'content': 'selected'},
            {'role': 'tool', 'content': 'unselected', 'metadata': {'experimental.media.v1': {'refs': [ref]}}},
        ], expected=binding.expected, operation_id='seed', admit=lambda: None, media_store=store)
        plan = preview_committed(binding.directory, expected=binding.expected,
                                 owner='fixture', fence=fence, selected_records=1)
        result = build(plan, tmp_path / 'operation', store, fence, export_assets=True)
        assert result.records == result.committed_prefix['message_count'] == 1
        assert result.committed_prefix['sha256'] == result.sha256
        assert not list((tmp_path / 'operation/portable/assets').iterdir())
        assert len((tmp_path / 'operation/candidate.jsonl').read_bytes().splitlines()) == 2
        verify_export(tmp_path / 'operation/portable')


def test_unknown_inline_policy_held_per_record_not_invented(tmp_path):
    import base64
    with fixture(tmp_path) as (binding, store, fence):
        rows = [{'role': 'user', 'content': [{'type': 'image', 'source': {
            'type': 'base64', 'media_type': 'image/png', 'data': base64.b64encode(png()).decode(),
        }}]}, {'role': 'assistant', 'content': 'keep independently eligible text'}]
        binding.append(rows, expected=binding.expected, operation_id='seed', admit=lambda: None)
        original = (binding.directory / 'transcript.v2' / binding.expected.body).read_bytes()
        plan = preview_committed(binding.directory, expected=binding.expected, owner='fixture', fence=fence)
        result = build(plan, tmp_path / 'operation', store, fence)
        assert result.held_records == 1 and result.records == 2
        assert result.images == result.inline_removed_bytes == 0
        assert (tmp_path / 'operation/candidate.jsonl').read_bytes() == original
        assert candidate_facts(tmp_path / 'operation')['held_records'] == 1
        with pytest.raises(Unsupported, match='Held original policy'):
            build(plan, tmp_path / 'export-held', store, fence, export_assets=True)


def test_source_advance_after_preview_refuses_before_operation(tmp_path):
    with fixture(tmp_path) as (binding, store, fence):
        binding.append([{'role': 'user', 'content': 'first'}], expected=binding.expected,
                       operation_id='seed', admit=lambda: None)
        plan = preview_committed(binding.directory, expected=binding.expected, owner='fixture', fence=fence)
        binding.append([{'role': 'user', 'content': 'later'}], expected=binding.expected,
                       operation_id='later', admit=lambda: None)
        with pytest.raises(ValueError, match='Expected committed source differs'):
            build(plan, tmp_path / 'operation', store, fence)
        assert not (tmp_path / 'operation').exists()


def test_committed_huge_scalar_is_bounded_not_a_decoded_window(tmp_path):
    import tracemalloc
    with fixture(tmp_path) as (binding, store, fence):
        # Fixture construction is outside the measurement; subject is the public
        # source -> preview -> bounded candidate -> verified export pipeline.
        binding.append([{'role': 'user', 'content': 'x' * (16 * 1024 * 1024)}],
                       expected=binding.expected, operation_id='huge', admit=lambda: None)
        tracemalloc.start()
        try:
            plan = preview_committed(binding.directory, expected=binding.expected,
                                     owner='fixture', fence=fence)
            result = build(plan, tmp_path / 'operation', store, fence, export_assets=True)
            with open_verified_candidate(
                tmp_path / 'operation', session_id='session', asset_reader=store.resolve_ref,
            ) as (stream, prefix, check):
                assert list(iter_serialized_candidate_refs(stream, candidate_prefix=prefix)) == []
                check()
            verify_export(tmp_path / 'operation/portable')
            _, peak = tracemalloc.get_traced_memory()
        finally:
            tracemalloc.stop()
        assert result.bytes > 16 * 1024 * 1024
        assert result.inline_removed_bytes == 0
        assert peak < 6 * 1024 * 1024, peak


def test_empty_selected_prefix_is_header_only_and_pinned(tmp_path):
    with fixture(tmp_path) as (binding, store, fence):
        binding.append([{'role': 'user', 'content': 'not selected'}],
                       expected=binding.expected, operation_id='seed', admit=lambda: None)
        plan = preview_committed(binding.directory, expected=binding.expected,
                                 owner='fixture', fence=fence, selected_records=0)
        result = build(plan, tmp_path / 'operation', store, fence, export_assets=True)
        assert result.records == result.committed_prefix['message_count'] == 0
        assert len((tmp_path / 'operation/candidate.jsonl').read_bytes().splitlines()) == 1
        verify_export(tmp_path / 'operation/portable')


def test_prepared_ref_iterator_rejects_malformed_namespace_late(tmp_path):
    from amplifier_foundation.session.transcript_v2 import CommittedPrefix
    header = b'{"format":"amplifier.session.transcript","v":2,"generation":"g","min_reader":["committed-prefix-v2","session-media-v1"]}\n'
    for carrier in (False, None, [], {'refs': [] , 'extra': True}, {'v': True, 'refs': []}):
        raw = header + json.dumps({'role': 'user', 'content': 'keep',
                                  'metadata': {'experimental.media.v1': carrier}}).encode() + b'\n'
        prefix = CommittedPrefix('session', 'g', 'g-g.jsonl', 1, len(raw), 1,
                                 hashlib.sha256(raw).hexdigest(), 'fixture')
        with pytest.raises(ValueError):
            list(iter_serialized_candidate_refs(io.BytesIO(raw), candidate_prefix=prefix))


def test_expired_owned_ref_remains_metadata_no_pixels_no_new_ttl(tmp_path):
    from datetime import datetime as RealDatetime, timezone
    class Clock(RealDatetime):
        @classmethod
        def now(cls, tz=None):
            return RealDatetime(2020, 1, 1, tzinfo=timezone.utc)
    with fixture(tmp_path) as (binding, store, fence):
        origin = {'kind': 'tool', 'session_id': 'session', 'id': 'expired',
                  'created_at': '2020-01-01T00:00:00+00:00',
                  'expires_at': '2020-01-01T00:01:00+00:00'}
        with pytest.MonkeyPatch.context() as clock:
            clock.setattr('amplifier_foundation.session.media.datetime', Clock)
            ref = store.ingest_sync(png(), source_format='image-bytes', origin=origin,
                                    policy=None)['staged_refs'][0]
            store.publish_sync([ref], origin=origin, disposition='retain')
            binding.append([{'role': 'tool', 'content': 'original role position',
                             'metadata': {'experimental.media.v1': {'v': 1, 'refs': [ref]}}}],
                           expected=binding.expected, operation_id='expired', admit=lambda: None,
                           media_store=store)
        plan = preview_committed(binding.directory, expected=binding.expected,
                                 owner='fixture', fence=fence)
        with pytest.MonkeyPatch.context() as monkeypatch:
            def no_pixels(*args, **kwargs):
                raise AssertionError('Expired metadata must not read pixels')
            monkeypatch.setattr(store, 'resolve_ref', no_pixels)
            result = build(plan, tmp_path / 'operation', store, fence, export_assets=True)
            with open_verified_candidate(
                tmp_path / 'operation', session_id='session', asset_reader=no_pixels,
            ) as (stream, prefix, check):
                assert list(iter_serialized_candidate_refs(stream, candidate_prefix=prefix)) == [ref]
                check()
        assert result.images == result.inline_removed_bytes == 0
        assert not list((tmp_path / 'operation/portable/assets').iterdir())
        verify_export(tmp_path / 'operation/portable')
        assert ref['provenance'] == origin
