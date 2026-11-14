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


def seed(source, store):
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
