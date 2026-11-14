"""Selected T2 fork/import closure through existing media/target writer owners."""
import asyncio
from contextlib import closing, contextmanager
import io
import json
import sys
import shutil

import pytest

from amplifier_foundation.session.checkpoint import verified_media_checkpoint_refs
from amplifier_foundation.session.media import SessionMediaStore
from amplifier_foundation.session.media_activation import apply_committed_candidate, resume_committed_candidate
from amplifier_foundation.session.media_conversion import (
    preview_committed, transfer_committed_prefix, verify_export, preview_import, import_committed_prefix,
)
from amplifier_foundation.session.transcript_lifetime import VersionedSessionBinding
from amplifier_foundation.session.transcript_source import open_committed_source
from tests.test_media_conversion_t2 import fixture

pytestmark = pytest.mark.skipif(sys.platform != 'linux', reason='Actual prospective custody is Linux qualified')


def pixels(color):
    from PIL import Image
    stream = io.BytesIO()
    Image.new('RGB', (3, 4), color).save(stream, 'PNG')
    return stream.getvalue()


@contextmanager
def owners(tmp_path):
    with fixture(tmp_path) as (source, source_store, source_fence):
        with closing(VersionedSessionBinding.create_new(
            tmp_path / 'target', session_id='target', operation_id='target-init', admit=lambda: None,
        )) as target:
            with closing(SessionMediaStore(target.directory / 'media', 'target',
                                           checkpoint_verifier=verified_media_checkpoint_refs)) as target_store:
                @contextmanager
                def both(owner):
                    with source_fence(owner), target.publication():
                        yield
                yield source, source_store, target, target_store, both


def seed(source, store, *, opaque_bytes=0):
    origin = {'kind': 'tool', 'session_id': source.session_id, 'id': 'two-images',
              'created_at': '2025-01-01T00:00:00+00:00', 'expires_at': '2035-01-01T00:00:00+00:00'}
    data = [pixels('red'), pixels('blue')]
    streams = [io.BytesIO(value) for value in data]
    refs = store.ingest_group_sync([{'stream': stream, 'mime': 'image/png'} for stream in streams],
                                   origin=origin, policy=None)['staged_refs']
    store.publish_sync(refs, origin=origin, disposition='retain')
    rows = [{'role': 'tool', 'tool_call_id': 'call', 'status': 'failed', 'usage': {'output_tokens': 8},
             'content': 'selected position', 'provider_state': {'opaque': 'preserved'},
             'metadata': {'experimental.media.v1': {'refs': refs}}},
            {'role': 'assistant', 'content': 'UNSELECTED PRIVATE SUFFIX'}]
    if opaque_bytes:
        rows[0]['provider_state']['huge_opaque'] = 'x' * opaque_bytes
    source.append(rows, expected=source.expected, operation_id='seed', admit=lambda: None, media_store=store)
    return rows, refs, data


def build(plan, path, source_store, target, target_store, fence):
    return asyncio.run(transfer_committed_prefix(
        plan, path, fence=fence, source_store=source_store, target_binding=target,
        target_store=target_store, expected_target=target.expected,
        asset_reader=lambda ref: target_store.resolve_ref(ref, committed=False),
    ))


def apply(path, target, store, expected, *, resume=False):
    function = resume_committed_candidate if resume else apply_committed_candidate
    return function(path, binding=target, expected=expected, operation_id='fork', owner='fixture',
                    media_store=store, admit=lambda: True)


def test_selected_group_fork_target_ack_originals_and_export_independent(tmp_path):
    with owners(tmp_path) as (source, source_store, target, target_store, fence):
        rows, refs, data = seed(source, source_store)
        source_expected, target_expected = source.expected, target.expected
        original = (source.directory / 'transcript.v2' / source_expected.body).read_bytes()
        plan = preview_committed(source.directory, expected=source_expected, owner='fixture',
                                 fence=fence, selected_records=1)
        candidate = build(plan, tmp_path / 'operation', source_store, target, target_store, fence)
        assert candidate.records == 1 and candidate.committed_prefix['session_id'] == 'target'
        assert candidate.inline_removed_bytes == 0
        portable = tmp_path / 'operation/portable'
        verify_export(portable)
        assert b'UNSELECTED PRIVATE SUFFIX' not in (portable / 'candidate.jsonl').read_bytes()
        assert len(list((portable / 'assets').iterdir())) == 2
        target_row = json.loads((portable / 'candidate.jsonl').read_bytes().splitlines()[1])
        adopted = target_row['metadata']['experimental.media.v1']['refs']
        assert [ref['session_id'] for ref in adopted] == ['target', 'target']
        assert [ref['provenance'] for ref in adopted] == [ref['provenance'] for ref in refs]
        for ref in adopted:
            with pytest.raises(ValueError, match='acknowledged canonical'):
                target_store.resolve_ref(ref)
        result = apply(tmp_path / 'operation', target, target_store, target_expected)
        assert result.state == 'acknowledged' and result.commit.message_count == 1
        with open_committed_source(target.directory, expected=target.expected) as snapshot:
            installed = snapshot.window(0, 1).messages[0]
        assert installed == {**rows[0], 'metadata': {'experimental.media.v1': {'refs': adopted}}}
        for ref, expected_bytes in zip(adopted, data):
            with target_store.resolve_ref(ref) as stream:
                assert stream.read() == expected_bytes
        assert source.expected == source_expected
        assert (source.directory / 'transcript.v2' / source_expected.body).read_bytes() == original
        assert (tmp_path / 'operation/original-0').read_bytes() == original
        assert (tmp_path / 'operation/original-target').exists()


def test_lost_target_reply_import_reconciles_without_readopting_or_rewrite(tmp_path, monkeypatch):
    with owners(tmp_path) as (source, source_store, target, target_store, fence):
        seed(source, source_store)
        plan = preview_committed(source.directory, expected=source.expected, owner='fixture',
                                 fence=fence, selected_records=1)
        build(plan, tmp_path / 'operation', source_store, target, target_store, fence)
        expected = target.expected
        actual = target.rewrite_serialized
        calls = []
        def lost(*args, **kwargs):
            calls.append(1)
            actual(*args, **kwargs)
            raise OSError('lost target reply')
        monkeypatch.setattr(target, 'rewrite_serialized', lost)
        with pytest.raises(OSError):
            apply(tmp_path / 'operation', target, target_store, expected)
        def forbidden(*args, **kwargs):
            raise AssertionError('Recovery cannot repeat adoption')
        monkeypatch.setattr(target_store, 'adopt', forbidden)
        result = apply(tmp_path / 'operation', target, target_store, expected, resume=True)
        assert result.state == 'acknowledged' and len(calls) == 1


def test_target_nonempty_refuses_before_adoption(tmp_path):
    with owners(tmp_path) as (source, source_store, target, target_store, fence):
        seed(source, source_store)
        target.append([{'role': 'user', 'content': 'preserve target'}], expected=target.expected,
                      operation_id='existing', admit=lambda: None)
        plan = preview_committed(source.directory, expected=source.expected, owner='fixture', fence=fence)
        with pytest.raises(ValueError, match='selection differs'):
            build(plan, tmp_path / 'operation', source_store, target, target_store, fence)
        assert not (tmp_path / 'operation').exists()


def test_prefix_splits_origin_group_without_copying_unselected_pixels(tmp_path):
    with owners(tmp_path) as (source, source_store, target, target_store, fence):
        _, refs, data = seed(source, source_store)
        source.rewrite([
            {'role': 'tool', 'content': 'selected', 'metadata': {'experimental.media.v1': {'refs': [refs[0]]}}},
            {'role': 'tool', 'content': 'PRIVATE SUFFIX', 'metadata': {'experimental.media.v1': {'refs': [refs[1]]}}},
        ], expected=source.expected, operation_id='split', admit=lambda: None, media_store=source_store)
        plan = preview_committed(source.directory, expected=source.expected, owner='fixture',
                                 fence=fence, selected_records=1)
        expected = target.expected
        build(plan, tmp_path / 'operation', source_store, target, target_store, fence)
        portable = tmp_path / 'operation/portable'
        assert {path.name for path in (portable / 'assets').iterdir()} == {refs[0]['sha256']}
        assert not (target_store.root / 'assets' / refs[1]['sha256']).exists()
        for path in portable.rglob('*'):
            if path.is_file():
                raw = path.read_bytes()
                assert data[1] not in raw and b'PRIVATE SUFFIX' not in raw
        assert apply(tmp_path / 'operation', target, target_store, expected).state == 'acknowledged'


def test_target_cas_race_after_adoption_preserves_unknown_effects(tmp_path, monkeypatch):
    with owners(tmp_path) as (source, source_store, target, target_store, fence):
        seed(source, source_store)
        plan = preview_committed(source.directory, expected=source.expected, owner='fixture',
                                 fence=fence, selected_records=1)
        actual = target_store.adopt
        async def moved(refs, source):
            accepted = await actual(refs, source)
            target.append([{'role': 'user', 'content': 'other legitimate target operation'}],
                          expected=target.expected, operation_id='raced', admit=lambda: None,
                          media_store=target_store)
            return accepted
        monkeypatch.setattr(target_store, 'adopt', moved)
        with pytest.raises(ValueError, match='Expected committed source differs'):
            build(plan, tmp_path / 'operation', source_store, target, target_store, fence)
        assert target.expected.message_count == 1
        assert not (tmp_path / 'operation/verified.json').exists()
        assert list((target_store.root / 'assets').iterdir())  # Preserved safe published-only effects.


def test_offline_portable_import_requires_exact_host_authorization_and_target_ack(tmp_path):
    with owners(tmp_path) as (source, source_store, target, target_store, fence):
        rows, refs, data = seed(source, source_store)
        plan = preview_committed(source.directory, expected=source.expected, owner='fixture',
                                 fence=fence, selected_records=1)
        build(plan, tmp_path / 'operation', source_store, target, target_store, fence)
        portable = tmp_path / 'delivery'
        shutil.copytree(tmp_path / 'operation/portable', portable)
    # Neither original source owner nor its media store remains open/available.
    with closing(VersionedSessionBinding.create_new(
        tmp_path / 'imported', session_id='imported', operation_id='import-init', admit=lambda: None,
    )) as imported:
        with closing(SessionMediaStore(imported.directory / 'media', 'imported',
                                       checkpoint_verifier=verified_media_checkpoint_refs)) as media:
            @contextmanager
            def fence(owner):
                with imported.publication():
                    yield
            with pytest.raises(ValueError, match='did not authorize'):
                preview_import(portable, owner='fixture', fence=fence, authorize=lambda _: False)
            plan = preview_import(portable, owner='fixture', fence=fence, authorize=lambda _: True)
            expected = imported.expected
            facts = json.loads((portable / 'export.json').read_bytes())
            calls = []
            def authorize(ref, *, receipt, target_session_id):
                calls.append(ref['sha256'])
                return (receipt == {'trusted-package': facts['candidate_sha256']}
                        and target_session_id == 'imported' and ref['sha256'] in {r['sha256'] for r in refs})
            candidate = asyncio.run(import_committed_prefix(
                plan, tmp_path / 'import-operation', fence=fence, target_binding=imported,
                target_store=media, expected_target=expected,
                asset_reader=lambda ref: media.resolve_ref(ref, committed=False),
                receipt={'trusted-package': facts['candidate_sha256']}, verifier=authorize,
            ))
            assert len(calls) == 2 and candidate.records == 1
            assert candidate.committed_prefix['session_id'] == 'imported'
            result = apply(tmp_path / 'import-operation', imported, media, expected)
            assert result.state == 'acknowledged'
            with open_committed_source(imported.directory, expected=imported.expected) as snapshot:
                row = snapshot.window(0, 1).messages[0]
            adopted = row['metadata']['experimental.media.v1']['refs']
            assert [ref['provenance'] for ref in adopted] == [ref['provenance'] for ref in refs]
            for ref, original in zip(adopted, data):
                with media.resolve_ref(ref) as stream:
                    assert stream.read() == original
            assert row['status'] == rows[0]['status'] and row['usage'] == rows[0]['usage']


def test_expired_transfer_keeps_original_metadata_without_any_pixel_copy(tmp_path, monkeypatch):
    from datetime import datetime as RealDatetime, timezone
    class Clock(RealDatetime):
        @classmethod
        def now(cls, tz=None):
            return RealDatetime(2020, 1, 1, tzinfo=timezone.utc)
    with owners(tmp_path) as (source, source_store, target, target_store, fence):
        origin = {'kind': 'tool', 'session_id': 'session', 'id': 'expired',
                  'created_at': '2020-01-01T00:00:00+00:00', 'expires_at': '2020-01-01T00:01:00+00:00'}
        with pytest.MonkeyPatch.context() as clock:
            clock.setattr('amplifier_foundation.session.media.datetime', Clock)
            ref = source_store.ingest_sync(pixels('red'), source_format='image-bytes',
                                           origin=origin, policy=None)['staged_refs'][0]
            source_store.publish_sync([ref], origin=origin, disposition='retain')
            source.append([{'role': 'tool', 'content': 'same expired observation',
                            'metadata': {'experimental.media.v1': {'refs': [ref]}}}],
                          expected=source.expected, operation_id='seed', admit=lambda: None,
                          media_store=source_store)
        plan = preview_committed(source.directory, expected=source.expected, owner='fixture', fence=fence)
        def forbidden(*args, **kwargs):
            raise AssertionError('Expired transfer must not access pixels')
        monkeypatch.setattr(source_store, 'resolve_ref', forbidden)
        monkeypatch.setattr(target_store, 'resolve_ref', forbidden)
        expected = target.expected
        build(plan, tmp_path / 'operation', source_store, target, target_store, fence)
        assert not list((tmp_path / 'operation/portable/assets').iterdir())
        result = apply(tmp_path / 'operation', target, target_store, expected)
        assert result.state == 'acknowledged'
        with open_committed_source(target.directory, expected=target.expected) as snapshot:
            accepted = snapshot.window(0, 1).messages[0]['metadata']['experimental.media.v1']['refs'][0]
        assert accepted == {**ref, 'session_id': 'target'} and accepted['provenance'] == origin


def test_cancelled_transfer_preserves_published_uncommitted_effect_no_replay(tmp_path, monkeypatch):
    with owners(tmp_path) as (source, source_store, target, target_store, fence):
        _, refs, _ = seed(source, source_store)
        plan = preview_committed(source.directory, expected=source.expected, owner='fixture', fence=fence)
        cancelled = [False]
        actual = target_store.adopt
        async def cancel_after(refs, source):
            result = await actual(refs, source)
            cancelled[0] = True
            return result
        monkeypatch.setattr(target_store, 'adopt', cancel_after)
        with pytest.raises(InterruptedError):
            asyncio.run(transfer_committed_prefix(
                plan, tmp_path / 'operation', fence=fence, source_store=source_store,
                target_binding=target, target_store=target_store, expected_target=target.expected,
                asset_reader=target_store.resolve_ref, cancelled=lambda: cancelled[0],
            ))
        assert target.expected.message_count == 0
        assert not (tmp_path / 'operation/verified.json').exists()
        for ref in refs:
            adopted = {**ref, 'session_id': 'target'}
            assert target_store.validate_published_ref(adopted, check_expiry=False) == adopted
            with pytest.raises(ValueError, match='acknowledged canonical'):
                target_store.resolve_ref(adopted)
        with pytest.raises(FileExistsError):
            build(plan, tmp_path / 'operation', source_store, target, target_store, fence)


def test_huge_single_record_fork_and_offline_import_stay_bounded(tmp_path, monkeypatch):
    import tracemalloc
    with owners(tmp_path) as (source, source_store, target, target_store, fence):
        _, refs, data = seed(source, source_store, opaque_bytes=8 * 1024 * 1024)
        def forbidden(*args, **kwargs):
            raise AssertionError('Transfer may not materialize rows through ordinary writer')
        monkeypatch.setattr(target, '_write', forbidden)
        expected = target.expected
        tracemalloc.start()
        plan = preview_committed(source.directory, expected=source.expected, owner='fixture',
                                 fence=fence, selected_records=1)
        candidate = build(plan, tmp_path / 'operation', source_store, target, target_store, fence)
        result = apply(tmp_path / 'operation', target, target_store, expected)
        assert result.state == 'acknowledged'
        print('fork-memory', tracemalloc.get_traced_memory())
        portable = tmp_path / 'operation/portable'
        verify_export(portable)
        with closing(VersionedSessionBinding.create_new(
            tmp_path / 'imported', session_id='imported', operation_id='initial', admit=lambda: None,
        )) as imported:
            with closing(SessionMediaStore(imported.directory / 'media', 'imported',
                                           checkpoint_verifier=verified_media_checkpoint_refs)) as media:
                @contextmanager
                def admission(owner):
                    with imported.publication():
                        yield
                monkeypatch.setattr(imported, '_write', forbidden)
                selected = preview_import(portable, owner='fixture', fence=admission, authorize=lambda _: True)
                before = imported.expected
                asyncio.run(import_committed_prefix(
                    selected, tmp_path / 'import', fence=admission, target_binding=imported,
                    target_store=media, expected_target=before,
                    asset_reader=lambda ref: media.resolve_ref(ref, committed=False),
                    receipt={'synthetic-authorized-package': candidate.sha256},
                    verifier=lambda ref, **kwargs: ref['sha256'] in {item['sha256'] for item in refs},
                ))
                outcome = apply(tmp_path / 'import', imported, media, before)
                assert outcome.state == 'acknowledged' and outcome.commit.message_count == 1
                print('import-memory', tracemalloc.get_traced_memory())
                verify_export(tmp_path / 'import/portable')
                with open_committed_source(imported.directory, expected=imported.expected) as snapshot:
                    assert sum(map(len, snapshot.iter_bytes())) > 8 * 1024 * 1024
        _, peak = tracemalloc.get_traced_memory()
        if peak >= 6 * 1024 * 1024:
            print(list(map(str, tracemalloc.take_snapshot().statistics('lineno')[:8])))
        tracemalloc.stop()
        assert peak < 6 * 1024 * 1024, peak
        print({'transferred_candidate_bytes': candidate.bytes, 'python_peak_bytes': peak})
