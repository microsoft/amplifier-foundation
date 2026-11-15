"""Synthetic trusted host producer fixtures, not a production historical cohort.

Every fixture origin is admitted/published/checkpointed by the actual F1 store;
only this test host owns its original raw-position and current-purpose truth.
"""
import asyncio
import base64
from contextlib import contextmanager
from copy import deepcopy
from dataclasses import asdict, replace
import hashlib
import io
import json
import sys

import pytest

from amplifier_foundation.session.media_conversion import (
    Unsupported, bind_committed_media_cohort, build_candidate, preview_committed,
)
from amplifier_foundation.session.media_activation import apply_committed_candidate
from amplifier_foundation.session.transcript_source import open_committed_source
from tests.test_media_conversion_t2 import fixture, png

pytestmark = pytest.mark.skipif(sys.platform != 'linux', reason='Actual prospective custody is Linux qualified')


def jpeg():
    from PIL import Image
    stream = io.BytesIO()
    Image.new('RGB', (3, 4), (23, 46, 69)).save(stream, 'JPEG')
    return stream.getvalue()


def inline(data, mime):
    return {'type': 'image', 'source': {'type': 'base64', 'media_type': mime,
                                       'data': base64.b64encode(data).decode()}}


class SyntheticProducer:
    """Trusted test host code under its OWN context, never a Foundation issuer.

    The original policy and occurrence map is fixture truth selected by the
    host, not derived/authorized by a candidate digest or current approval.
    """
    def __init__(self, expected, rows, mapping):
        self.expected, self.rows, self.mapping = expected, rows, mapping
        self.active = False
        self.revoked = False
        self.transform = None
        self.calls = 0

    @contextmanager
    def custody(self):
        assert not self.active
        self.active = True
        try:
            yield self
            if self.revoked:
                raise Unsupported('Synthetic host current purpose revoked on exit')
        finally:
            self.active = False

    def __call__(self, *, commit, rowfacts, images):
        if not self.active or self.revoked:
            raise Unsupported('Synthetic producer custody/current purpose unavailable')
        self.calls += 1
        assert commit == self.expected
        assert rowfacts['sha256'] == hashlib.sha256(self.rows[rowfacts['index']]).hexdigest()
        structural = list(images)
        assert all(set(image) == {'block_index', 'block_start', 'block_end', 'data_start', 'data_end', 'mime'}
                   for image in structural)
        assert all('sha256' not in image and 'bytes' not in image for image in structural)
        if structural:
            with pytest.raises(TypeError):
                structural[0]['mime'] = 'forged'
        groups = self.mapping.get(rowfacts['index'])
        if groups is None:
            return None
        value = {'source_commit': asdict(commit), 'row': deepcopy(rowfacts),
                 'projection': 'canonical-original', 'groups': deepcopy(groups)}
        return self.transform(value) if self.transform else value

    def admit(self):
        if not self.active or self.revoked:
            raise Unsupported('Synthetic producer current activation purpose refused')
        return True


def seed(binding, store, *, mime='image/png', duplicate=False, known_rows=1, opaque=0,
         subset=False, original=None, source_data=None):
    data = png() if mime == 'image/png' else jpeg()
    origin = original or {'kind': 'tool', 'session_id': 'session', 'id': 'synthetic-original',
              'created_at': '2025-01-01T00:00:00+00:00', 'expires_at': '2035-01-01T00:00:00+00:00'}
    count = 2 if duplicate else 1
    refs = store.ingest_group_sync([{'stream': io.BytesIO(data), 'mime': mime} for _ in range(count)],
                                   origin=origin, policy=None)['staged_refs']
    publication = refs[:1] if subset else refs
    store.publish_sync(publication, origin=origin, disposition='retain')
    # Unknown adjacent row deliberately carries the same pixels, NOT authority.
    rows = [{'role': 'tool', 'tool_call_id': 'unknown', 'content': [inline(data, mime)]}]
    for n in range(known_rows):
        rows.append({'role': 'tool', 'tool_call_id': f'known-{n}', 'status': 'failed',
                     'usage': {'output_tokens': 7}, 'metadata': {'opaque': 'keep'},
                     'content': [{'type': 'text', 'text': 'original position'},
                                 *[inline(source_data or data, mime) for _ in range(count)]]})
    if opaque:
        rows[1]['provider_state'] = {'large': 'x' * opaque}
    # Actual F1 checkpoint is an independent proof of the ORIGINAL published refs.
    rows.append({'role': 'assistant', 'content': 'original producer checkpoint',
                 'metadata': {'experimental.media.v1': {'refs': publication}}})
    binding.append(rows, expected=binding.expected, operation_id='fixture-source',
                   admit=lambda: None, media_store=store)
    for ref in publication:
        assert store.validate_ref(ref, check_expiry=False) == ref
    raw = (binding.directory / 'transcript.v2' / binding.expected.body).read_bytes().splitlines()[1:]
    groups = [{'origin': origin, 'retention': 'durable', 'members': [
        {'block_index': i + 1, 'occurrence_id': f'original-member-{i}', 'ref': ref}
        for i, ref in enumerate(refs)
    ]}]
    producer = SyntheticProducer(binding.expected, raw, {row: groups for row in range(1, known_rows + 1)})
    return producer, rows, refs, data


def build(plan, path, store, cohort):
    return asyncio.run(build_candidate(
        plan, path, fence=cohort.fence, capability=store, origin_for=cohort.origin_for,
        eligible_record=cohort.eligible_record,
        asset_reader=lambda ref: store.resolve_ref(ref, committed=False),
    ))


@pytest.mark.parametrize('mime', ['image/png', 'image/jpeg'])
def test_actual_producer_group_checked_inline_conversion_ack_and_adjacent_unknown(tmp_path, mime):
    with fixture(tmp_path) as (binding, store, fence):
        producer, rows, refs, data = seed(binding, store, mime=mime, duplicate=True)
        expected = binding.expected
        plan = preview_committed(binding.directory, expected=expected, owner='fixture', fence=fence)
        original = (binding.directory / 'transcript.v2' / expected.body).read_bytes()
        with producer.custody():
            with bind_committed_media_cohort(binding.directory, expected=expected, owner='fixture',
                                            fence=fence, producer=producer, store=store) as cohort:
                assert cohort.eligible_record(0) is False
                assert cohort.eligible_record(1) is True
                assert cohort.origin_for(1, 1) == refs[0]['provenance']
                candidate = build(plan, tmp_path / 'operation', store, cohort)
                assert candidate.images == 2 and candidate.inline_removed_bytes > 0
                assert candidate.held_records == 1
            with pytest.raises(Unsupported, match='closed'):
                cohort.eligible_record(1)
            # Original-source pin must end BEFORE legitimate new-generation CAS.
            result = apply_committed_candidate(
                tmp_path / 'operation', binding=binding, expected=expected, operation_id='cohort-optimize',
                owner='fixture', media_store=store, admit=producer.admit,
            )
            assert result.state == 'acknowledged'
        candidate_bytes = (tmp_path / 'operation/candidate.jsonl').read_bytes().splitlines()[1:]
        assert candidate_bytes[0] == original.splitlines()[1]
        with open_committed_source(binding.directory, expected=result.commit) as source:
            converted = source.window(1, 1).messages[0]
        assert converted['tool_call_id'] == rows[1]['tool_call_id']
        assert converted['status'] == 'failed' and converted['usage'] == {'output_tokens': 7}
        converted_refs = converted['metadata']['experimental.media.v1']['refs']
        assert converted_refs == refs and len(converted_refs) == 2
        assert converted_refs[0]['provenance']['expires_at'] == '2035-01-01T00:00:00+00:00'
        for ref in converted_refs:
            with store.resolve_ref(ref) as stream:
                assert stream.read() == data


def test_declined_unknown_is_not_decoded_staged_or_resolved(tmp_path, monkeypatch):
    with fixture(tmp_path) as (binding, store, fence):
        producer, _, _, _ = seed(binding, store)
        producer.mapping = {}
        def forbidden(*a, **k):
            raise AssertionError('Declined original must not decode/read/stage')
        monkeypatch.setattr('amplifier_foundation.session.media_conversion._decode', forbidden)
        monkeypatch.setattr(store, 'ingest_group_sync', forbidden)
        monkeypatch.setattr(store, 'resolve_ref', forbidden)
        with producer.custody(), bind_committed_media_cohort(
            binding.directory, expected=binding.expected, owner='fixture', fence=fence, producer=producer, store=store,
        ) as cohort:
            assert cohort.eligible_record(0) is cohort.eligible_record(1) is False


@pytest.mark.parametrize('change', ['prefix', 'row', 'block', 'mime', 'digest', 'bytes', 'order', 'occurrence'])
def test_contradictory_producer_source_identity_refuses_before_effects(tmp_path, monkeypatch, change):
    with fixture(tmp_path) as (binding, store, fence):
        producer, _, refs, _ = seed(binding, store, duplicate=True)
        def transform(value):
            members = value['groups'][0]['members']
            if change == 'prefix': value['source_commit']['sequence'] += 1
            elif change == 'row': value['row']['start'] += 1
            elif change == 'block': members[0]['block_index'] = True
            elif change == 'mime': members[0]['ref']['mime'] = 'image/jpeg'
            elif change == 'digest': members[0]['ref']['sha256'] = '0' * 64
            elif change == 'bytes': members[0]['ref']['bytes'] += 1
            elif change == 'order': members.reverse()
            elif change == 'occurrence': members[1]['occurrence_id'] = members[0]['occurrence_id']
            return value
        producer.transform = transform
        def forbidden(*a, **k):
            raise AssertionError('Source mismatch must not stage')
        monkeypatch.setattr(store, 'ingest_group_sync', forbidden)
        if change in ('digest', 'bytes'):
            # Unknown/malformed existing publication safely holds before decode.
            with producer.custody(), bind_committed_media_cohort(
                binding.directory, expected=binding.expected, owner='fixture', fence=fence, producer=producer, store=store,
            ) as cohort:
                assert cohort.eligible_record(1) is False
        else:
            with producer.custody(), pytest.raises((Unsupported, ValueError)), bind_committed_media_cohort(
                binding.directory, expected=binding.expected, owner='fixture', fence=fence, producer=producer, store=store,
            ):
                pass
        assert not (tmp_path / 'operation').exists()
        assert store.validate_published_group(refs, origin=refs[0]['provenance'])['valid']


@pytest.mark.parametrize('mode', ['partial', 'request-only', 'echo', 'cross-row', 'subset'])
def test_unsupported_complete_policy_or_projection_held_before_decode(tmp_path, monkeypatch, mode):
    with fixture(tmp_path) as (binding, store, fence):
        producer, _, refs, _ = seed(binding, store, duplicate=True, known_rows=2 if mode == 'cross-row' else 1,
                                    subset=mode == 'subset')
        def transform(value):
            if mode == 'partial': value['groups'][0]['members'].pop()
            elif mode == 'request-only': value['groups'][0]['retention'] = 'request-only'
            elif mode == 'echo': value['projection'] = 'diagnostic-echo'
            return value
        producer.transform = transform
        def forbidden(*a, **k):
            raise AssertionError('Unqualified group must HOLD before decode')
        monkeypatch.setattr('amplifier_foundation.session.media_conversion._decode', forbidden)
        with producer.custody(), bind_committed_media_cohort(
            binding.directory, expected=binding.expected, owner='fixture', fence=fence, producer=producer, store=store,
        ) as cohort:
            assert cohort.eligible_record(1) is False
            if mode == 'cross-row': assert cohort.eligible_record(2) is False


def test_revocation_and_cold_callback_reuse_refuse_before_new_operation(tmp_path):
    with fixture(tmp_path) as (binding, store, fence):
        producer, _, _, _ = seed(binding, store)
        expected = binding.expected
        plan = preview_committed(binding.directory, expected=expected, owner='fixture', fence=fence)
        with producer.custody():
            with pytest.raises(Unsupported, match='unavailable'), bind_committed_media_cohort(
                binding.directory, expected=expected, owner='fixture', fence=fence, producer=producer, store=store,
            ) as cohort:
                producer.revoked = True
                build(plan, tmp_path / 'operation', store, cohort)
            producer.revoked = False
        assert not (tmp_path / 'operation').exists()
        with pytest.raises(Unsupported, match='closed'):
            cohort.origin_for(1, 1)
        with pytest.raises(Unsupported, match='unavailable'), bind_committed_media_cohort(
            binding.directory, expected=expected, owner='fixture', fence=fence, producer=producer, store=store,
        ):
            pass


def test_current_store_drop_and_source_change_refuse(tmp_path):
    with fixture(tmp_path) as (binding, store, fence):
        producer, _, _, _ = seed(binding, store)
        expected = binding.expected
        with producer.custody():
            with pytest.raises(Unsupported, match='retention'), bind_committed_media_cohort(
                binding.directory, expected=expected, owner='fixture', fence=fence, producer=producer, store=store,
            ) as cohort:
                store.policy = replace(store.policy, retention='drop')
                cohort.eligible_record(1)
            store.policy = replace(store.policy, retention='durable')
        binding.append([{'role': 'user', 'content': 'later'}], expected=expected,
                       operation_id='later', admit=lambda: None, media_store=store)
        with producer.custody(), pytest.raises(ValueError), bind_committed_media_cohort(
            binding.directory, expected=expected, owner='fixture', fence=fence, producer=producer, store=store,
        ):
            pass


def test_actual_decoded_identity_mismatch_refuses_before_staging(tmp_path, monkeypatch):
    with fixture(tmp_path) as (binding, store, fence):
        producer, _, _, _ = seed(binding, store, source_data=png() + b'not-original')
        monkeypatch.setattr(store, 'ingest_group_sync', lambda *a, **k: pytest.fail('No staging on mismatch'))
        with producer.custody(), pytest.raises(Unsupported, match='decoded image identity'), bind_committed_media_cohort(
            binding.directory, expected=binding.expected, owner='fixture', fence=fence, producer=producer, store=store,
        ):
            pass


def test_expired_original_held_without_decode_or_asset_read(tmp_path, monkeypatch):
    from datetime import datetime, timezone
    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime(2020, 1, 1, tzinfo=timezone.utc)
    with fixture(tmp_path) as (binding, store, fence):
        with monkeypatch.context() as clock:
            clock.setattr('amplifier_foundation.session.media.datetime', Clock)
            producer, _, _, _ = seed(binding, store, original={
                'kind': 'tool', 'session_id': 'session', 'id': 'expired-original',
                'created_at': '2020-01-01T00:00:00+00:00', 'expires_at': '2020-01-01T00:01:00+00:00',
            })
        def forbidden(*a, **k):
            raise AssertionError('Expired original must not decode or read pixels')
        monkeypatch.setattr('amplifier_foundation.session.media_conversion._decode', forbidden)
        monkeypatch.setattr(store, 'resolve_ref', forbidden)
        with producer.custody(), bind_committed_media_cohort(
            binding.directory, expected=binding.expected, owner='fixture', fence=fence, producer=producer, store=store,
        ) as cohort:
            assert cohort.eligible_record(1) is False


def test_revocation_after_staging_preserves_uncertain_assets_without_publication(tmp_path, monkeypatch):
    with fixture(tmp_path) as (binding, store, fence):
        producer, _, _, _ = seed(binding, store)
        expected = binding.expected
        plan = preview_committed(binding.directory, expected=expected, owner='fixture', fence=fence)
        actual = store.ingest_group_sync
        def revoke(*args, **kwargs):
            result = actual(*args, **kwargs)
            producer.revoked = True
            return result
        monkeypatch.setattr(store, 'ingest_group_sync', revoke)
        monkeypatch.setattr(store, 'publish', lambda *a, **k: pytest.fail('No publication after revocation'))
        with producer.custody():
            with pytest.raises(Unsupported, match='unavailable'), bind_committed_media_cohort(
                binding.directory, expected=expected, owner='fixture', fence=fence, producer=producer, store=store,
            ) as cohort:
                build(plan, tmp_path / 'operation', store, cohort)
            producer.revoked = False
        assert binding.expected == expected
        assert not (tmp_path / 'operation/verified.json').exists()
        assert b'group-ingest-intent' in (tmp_path / 'operation/journal.jsonl').read_bytes()
        with producer.custody(), bind_committed_media_cohort(
            binding.directory, expected=expected, owner='fixture', fence=fence, producer=producer, store=store,
        ) as cohort, pytest.raises(FileExistsError):
            build(plan, tmp_path / 'operation', store, cohort)


def test_cancellation_no_staging_and_late_fence_error_no_success(tmp_path, monkeypatch):
    with fixture(tmp_path) as (binding, store, fence):
        producer, _, _, _ = seed(binding, store)
        expected = binding.expected
        plan = preview_committed(binding.directory, expected=expected, owner='fixture', fence=fence)
        def forbidden(*a, **k):
            raise AssertionError('Cancelled operation cannot stage')
        monkeypatch.setattr(store, 'ingest_group_sync', forbidden)
        with producer.custody(), bind_committed_media_cohort(
            binding.directory, expected=expected, owner='fixture', fence=fence, producer=producer, store=store,
        ) as cohort, pytest.raises(InterruptedError):
            asyncio.run(build_candidate(
                plan, tmp_path / 'cancelled', fence=cohort.fence, capability=store,
                origin_for=cohort.origin_for, eligible_record=cohort.eligible_record,
                asset_reader=lambda ref: store.resolve_ref(ref, committed=False), cancelled=lambda: True,
            ))
        @contextmanager
        def late(owner):
            with fence(owner):
                yield
                raise OSError('late actual fence exit')
        completed = False
        with producer.custody(), pytest.raises(OSError, match='late actual fence'), bind_committed_media_cohort(
            binding.directory, expected=expected, owner='fixture', fence=late, producer=producer, store=store,
        ):
            pass
        assert completed is False and binding.expected == expected


def test_cold_reopen_rebinds_original_source_and_real_store_not_cached_callbacks(tmp_path):
    from contextlib import closing
    from amplifier_foundation.session.checkpoint import verified_media_checkpoint_refs
    from amplifier_foundation.session.media import SessionMediaStore
    from amplifier_foundation.session.transcript_lifetime import VersionedSessionBinding
    with fixture(tmp_path) as (binding, store, fence):
        producer, _, _, _ = seed(binding, store)
        expected, directory = binding.expected, binding.directory
        with producer.custody(), bind_committed_media_cohort(
            directory, expected=expected, owner='fixture', fence=fence, producer=producer, store=store,
        ) as previous:
            assert previous.eligible_record(1) is True
    with closing(VersionedSessionBinding.resume(directory, session_id='session', expected=expected)) as binding:
        with closing(SessionMediaStore(directory / 'media', 'session',
                                       checkpoint_verifier=verified_media_checkpoint_refs)) as store:
            @contextmanager
            def fence(owner):
                with binding.publication():
                    yield
            with producer.custody(), bind_committed_media_cohort(
                directory, expected=expected, owner='fixture', fence=fence, producer=producer, store=store,
            ) as current:
                assert current.eligible_record(1) is True
                with pytest.raises(Unsupported, match='closed'):
                    previous.eligible_record(1)


@pytest.mark.parametrize('opaque', [2 * 1024 * 1024, 8 * 1024 * 1024])
def test_huge_checked_cohort_build_and_actual_ack_remain_bounded(tmp_path, monkeypatch, opaque):
    import tracemalloc
    with fixture(tmp_path) as (binding, store, fence):
        producer, _, _, _ = seed(binding, store, opaque=opaque)
        expected = binding.expected
        plan = preview_committed(binding.directory, expected=expected, owner='fixture', fence=fence)
        monkeypatch.setattr(binding, '_write',
                            lambda *a, **k: pytest.fail('Ordinary materializing writer path forbidden'))
        tracemalloc.start()
        try:
            with producer.custody():
                with bind_committed_media_cohort(
                    binding.directory, expected=expected, owner='fixture', fence=fence, producer=producer, store=store,
                ) as cohort:
                    candidate = build(plan, tmp_path / 'operation', store, cohort)
                    assert candidate.images == 1 and candidate.held_records == 1
                result = apply_committed_candidate(
                    tmp_path / 'operation', binding=binding, expected=expected, operation_id='cohort-optimize',
                    owner='fixture', media_store=store, admit=producer.admit,
                )
                assert result.state == 'acknowledged' and result.inline_removed_bytes > 0
            with open_committed_source(binding.directory, expected=result.commit) as source:
                assert source.commit.message_count == 3
                assert sum(len(chunk) for chunk in source.iter_bytes()) == result.commit.committed_bytes
            _, peak = tracemalloc.get_traced_memory()
        finally:
            tracemalloc.stop()
        print({'cohort_opaque_bytes': opaque, 'candidate_bytes': candidate.bytes, 'python_peak_bytes': peak})
        assert peak < 6 * 1024 * 1024
