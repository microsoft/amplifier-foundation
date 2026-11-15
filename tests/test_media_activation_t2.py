"""Actual prospective binding activation; synthetic temporary sessions only."""
import asyncio
import base64
from contextlib import closing, contextmanager
from dataclasses import asdict
import json
import sys

import pytest

from amplifier_foundation.session.media_activation import (
    ActivationRefused, apply_committed_candidate, resume_committed_candidate,
    reconcile_committed_candidate,
)
from amplifier_foundation.session.media_conversion import build_candidate, preview_committed
from amplifier_foundation.session.transcript_source import open_committed_source
from amplifier_foundation.session.versioned_checkpoint import VersionedCheckpointReceipt
from tests.test_media_conversion_t2 import fixture, png, published

pytestmark = pytest.mark.skipif(sys.platform != 'linux', reason='Actual prospective custody is Linux qualified')


def prepared(tmp_path, binding, store, fence, *, inline=False, jpeg=False):
    if inline:
        payload, mime = png(), 'image/png'
        if jpeg:
            import io
            from PIL import Image
            encoded = io.BytesIO()
            Image.new('RGB', (3, 4), (54, 98, 102)).save(encoded, 'JPEG')
            payload, mime = encoded.getvalue(), 'image/jpeg'
        row = {'role': 'tool', 'tool_call_id': 'actual-original', 'status': 'complete',
               'usage': {'output_tokens': 8}, 'content': [{'type': 'image', 'source': {
                   'type': 'base64', 'media_type': mime, 'data': base64.b64encode(payload).decode(),
               }}]}
        binding.append([row], expected=binding.expected, operation_id='seed', admit=lambda: None)
    else:
        ref = published(store)
        row = {'role': 'tool', 'content': 'existing owned ref', 'metadata': {'experimental.media.v1': {'refs': [ref]}}}
        binding.append([row], expected=binding.expected, operation_id='seed', admit=lambda: None, media_store=store)
    expected = binding.expected
    original = (binding.directory / 'transcript.v2' / expected.body).read_bytes()
    plan = preview_committed(binding.directory, expected=expected, owner='fixture', fence=fence)
    result = asyncio.run(build_candidate(
        plan, tmp_path / 'operation', fence=fence, capability=store,
        origin_for=lambda row, block: {'kind': 'migration', 'session_id': 'session', 'id': 'actual-original',
                                      'created_at': '2025-01-01T00:00:00+00:00', 'expires_at': '2035-01-01T00:00:00+00:00'},
        eligible_record=lambda row: True,
        asset_reader=lambda ref: store.resolve_ref(ref, committed=False),
    ))
    return expected, original, result


def arguments(binding, store, expected):
    return dict(binding=binding, expected=expected, operation_id='optimize', owner='fixture',
                media_store=store, admit=lambda: True)


def test_refs_first_noop_acks_without_rewrite_or_compression(tmp_path, monkeypatch):
    with fixture(tmp_path) as (binding, store, fence):
        expected, original, candidate = prepared(tmp_path, binding, store, fence)
        def forbidden(*args, **kwargs):
            raise AssertionError('Refs-first no-op must not rewrite')
        monkeypatch.setattr(binding, 'rewrite_serialized', forbidden, raising=False)
        result = apply_committed_candidate(tmp_path / 'operation', **arguments(binding, store, expected))
        assert result.state == 'acknowledged' and result.commit == expected
        assert type(result.checkpoint) is VersionedCheckpointReceipt
        assert result.inline_removed_bytes == candidate.inline_removed_bytes == 0
        assert binding.expected == expected
        assert (binding.directory / 'transcript.v2' / expected.body).read_bytes() == original
        observed = reconcile_committed_candidate(
            tmp_path / 'operation', binding=binding, expected=expected, operation_id='optimize', owner='fixture',
        )
        assert observed.state == 'installed-awaiting-versioned-ack' and observed.checkpoint is None


@pytest.mark.parametrize('jpeg', [False, True])
def test_inline_rewrite_real_binding_typed_ack_readback_and_original_retained(tmp_path, jpeg):
    with fixture(tmp_path) as (binding, store, fence):
        expected, original, candidate = prepared(tmp_path, binding, store, fence, inline=True, jpeg=jpeg)
        original_row = json.loads(original.splitlines()[1])
        original_pixels = base64.b64decode(original_row['content'][0]['source']['data'])
        result = apply_committed_candidate(tmp_path / 'operation', **arguments(binding, store, expected))
        assert result.state == 'acknowledged' and result.commit == binding.expected != expected
        assert type(result.checkpoint) is VersionedCheckpointReceipt
        assert result.inline_removed_bytes == candidate.inline_removed_bytes > 0
        assert (binding.directory / 'transcript.v2' / expected.body).read_bytes() == original
        with open_committed_source(binding.directory, expected=binding.expected) as source:
            row = source.window(0, 1).messages[0]
        assert row['tool_call_id'] == 'actual-original' and row['usage'] == {'output_tokens': 8}
        ref = row['metadata']['experimental.media.v1']['refs'][0]
        with store.resolve_ref(ref) as stream:
            assert stream.read() == original_pixels
        assert ref['mime'] == ('image/jpeg' if jpeg else 'image/png')
        assert ref['provenance']['expires_at'] == '2035-01-01T00:00:00+00:00'
        with pytest.raises(ActivationRefused, match='reconcile/resume'):
            apply_committed_candidate(tmp_path / 'operation', **arguments(binding, store, expected))


def test_lost_rewrite_reply_reconciles_without_second_rewrite(tmp_path, monkeypatch):
    with fixture(tmp_path) as (binding, store, fence):
        expected, original, _ = prepared(tmp_path, binding, store, fence, inline=True)
        actual = binding.rewrite_serialized
        calls = []
        def lost(*args, **kwargs):
            calls.append(1)
            actual(*args, **kwargs)
            raise OSError('lost response after successful actual source write')
        monkeypatch.setattr(binding, 'rewrite_serialized', lost)
        with pytest.raises(OSError, match='lost response'):
            apply_committed_candidate(tmp_path / 'operation', **arguments(binding, store, expected))
        installed = binding.expected
        result = resume_committed_candidate(tmp_path / 'operation', **arguments(binding, store, expected))
        assert result.state == 'acknowledged' and result.commit == installed
        assert len(calls) == 1
        assert (binding.directory / 'transcript.v2' / expected.body).read_bytes() == original


def test_cold_resume_uses_saved_original_selector_under_new_actual_owner(tmp_path, monkeypatch):
    from amplifier_foundation.session.checkpoint import verified_media_checkpoint_refs
    from amplifier_foundation.session.media import SessionMediaStore
    from amplifier_foundation.session.media_conversion import load_candidate_plan
    from amplifier_foundation.session.transcript_lifetime import VersionedSessionBinding
    from amplifier_foundation.session.transcript_v2 import CommittedPrefix
    with fixture(tmp_path) as (binding, store, fence):
        expected, original, _ = prepared(tmp_path, binding, store, fence, inline=True)
        actual = binding.rewrite_serialized
        def lost(*args, **kwargs):
            actual(*args, **kwargs)
            raise OSError('lost after actual commit')
        monkeypatch.setattr(binding, 'rewrite_serialized', lost)
        with pytest.raises(OSError):
            apply_committed_candidate(tmp_path / 'operation', **arguments(binding, store, expected))
        current, directory = binding.expected, binding.directory
    # Selector facts are reconstructed only after the host has independently
    # selected this operation and reacquired actual enrolled source custody.
    with closing(VersionedSessionBinding.resume(directory, session_id='session', expected=current)) as owner:
        with closing(SessionMediaStore(directory / 'media', 'session',
                                       checkpoint_verifier=verified_media_checkpoint_refs)) as media:
            saved_plan = load_candidate_plan(tmp_path / 'operation')
            predecessor = CommittedPrefix(**saved_plan.committed_prefix)
            assert predecessor == expected and predecessor != owner.expected
            result = resume_committed_candidate(tmp_path / 'operation', **arguments(owner, media, predecessor))
            assert result.state == 'acknowledged' and result.commit == current
            with open_committed_source(directory, expected=current) as source:
                ref = source.window(0, 1).messages[0]['metadata']['experimental.media.v1']['refs'][0]
            with media.resolve_ref(ref) as stream:
                assert stream.read() == png()
            assert (directory / 'transcript.v2' / expected.body).read_bytes() == original


def test_cancellation_before_intent_does_not_change_source(tmp_path):
    with fixture(tmp_path) as (binding, store, fence):
        expected, original, _ = prepared(tmp_path, binding, store, fence)
        with pytest.raises(InterruptedError):
            apply_committed_candidate(tmp_path / 'operation', cancelled=lambda: True,
                                      **arguments(binding, store, expected))
        assert binding.expected == expected
        assert not (tmp_path / 'operation/committed-activation.json').exists()
        assert (binding.directory / 'transcript.v2' / expected.body).read_bytes() == original
        observed = reconcile_committed_candidate(
            tmp_path / 'operation', binding=binding, expected=expected, operation_id='optimize', owner='fixture',
        )
        assert observed.state == 'not-started' and observed.checkpoint is None


def test_mixed_inline_policy_commits_eligible_row_preserves_held_original(tmp_path):
    with fixture(tmp_path) as (binding, store, fence):
        image = {'type': 'image', 'source': {
            'type': 'base64', 'media_type': 'image/png', 'data': base64.b64encode(png()).decode(),
        }}
        rows = [{'role': 'tool', 'tool_call_id': 'unknown', 'status': 'failed', 'content': [image]},
                {'role': 'tool', 'tool_call_id': 'known', 'status': 'complete', 'content': [image]}]
        binding.append(rows, expected=binding.expected, operation_id='seed', admit=lambda: None)
        expected = binding.expected
        plan = preview_committed(binding.directory, expected=expected, owner='fixture', fence=fence)
        origin = {'kind': 'migration', 'session_id': 'session', 'id': 'known',
                  'created_at': '2025-01-01T00:00:00+00:00', 'expires_at': '2035-01-01T00:00:00+00:00'}
        asyncio.run(build_candidate(
            plan, tmp_path / 'operation', fence=fence, capability=store,
            eligible_record=lambda row: row == 1, origin_for=lambda row, block: origin,
            asset_reader=lambda ref: store.resolve_ref(ref, committed=False),
        ))
        result = apply_committed_candidate(tmp_path / 'operation', **arguments(binding, store, expected))
        assert result.state == 'acknowledged' and result.held_records == 1
        assert result.inline_removed_bytes > 0
        with open_committed_source(binding.directory, expected=binding.expected) as source:
            actual = source.window(0, 2).messages
        assert actual[0] == rows[0]
        assert actual[1]['tool_call_id'] == 'known' and actual[1]['content'][0]['type'] == 'text'
        ref = actual[1]['metadata']['experimental.media.v1']['refs'][0]
        assert ref['provenance'] == origin
        with store.resolve_ref(ref) as pixels:
            assert pixels.read() == png()


def test_resume_refuses_journal_kind_substitution(tmp_path, monkeypatch):
    with fixture(tmp_path) as (binding, store, fence):
        expected, original, _ = prepared(tmp_path, binding, store, fence, inline=True)
        def before(*args, **kwargs):
            raise OSError('before actual rewrite')
        monkeypatch.setattr(binding, 'rewrite_serialized', before, raising=False)
        with pytest.raises(OSError, match='before actual'):
            apply_committed_candidate(tmp_path / 'operation', **arguments(binding, store, expected))
        journal = tmp_path / 'operation/committed-activation.json'
        value = json.loads(journal.read_bytes())
        assert value['kind'] == 'rewrite'
        value['kind'] = 'checkpoint'
        journal.write_text(json.dumps(value))
        with pytest.raises(ActivationRefused, match='intent differs'):
            resume_committed_candidate(tmp_path / 'operation', **arguments(binding, store, expected))
        assert binding.expected == expected
        assert (binding.directory / 'transcript.v2' / expected.body).read_bytes() == original


def test_cancel_during_intent_publication_stops_before_checkpoint(tmp_path, monkeypatch):
    from amplifier_foundation.session import media_activation
    with fixture(tmp_path) as (binding, store, fence):
        expected, original, _ = prepared(tmp_path, binding, store, fence)
        cancelled = [False]
        actual = media_activation._committed_journal
        def written(*args):
            actual(*args)
            cancelled[0] = True
        monkeypatch.setattr(media_activation, '_committed_journal', written)
        def forbidden(*args, **kwargs):
            raise AssertionError('Checkpoint must not start after cancellation')
        monkeypatch.setattr(binding, 'checkpoint', forbidden)
        with pytest.raises(InterruptedError):
            apply_committed_candidate(tmp_path / 'operation', cancelled=lambda: cancelled[0],
                                      **arguments(binding, store, expected))
        assert binding.expected == expected
        assert (binding.directory / 'transcript.v2' / expected.body).read_bytes() == original


def test_historical_reconcile_never_borrows_successor_receipt(tmp_path, monkeypatch):
    with fixture(tmp_path) as (binding, store, fence):
        expected, _, _ = prepared(tmp_path, binding, store, fence, inline=True)
        def before(*args, **kwargs):
            raise OSError('before actual rewrite')
        monkeypatch.setattr(binding, 'rewrite_serialized', before, raising=False)
        with pytest.raises(OSError):
            apply_committed_candidate(tmp_path / 'operation', **arguments(binding, store, expected))
        # Existing supported writer API is used only to construct a real
        # historical-reconcile fixture, never as a streaming implementation.
        row = json.loads((tmp_path / 'operation/candidate.jsonl').read_bytes().splitlines()[1])
        actual = binding.rewrite([row], expected=expected, operation_id='optimize',
                                 admit=lambda: None, media_store=store)
        binding.append([{'role': 'assistant', 'content': 'later successor'}],
                       expected=binding.expected, operation_id='successor',
                       admit=lambda: None, media_store=store)
        latest = binding.expected
        result = resume_committed_candidate(tmp_path / 'operation', **arguments(binding, store, expected))
        assert result.state == 'superseded-original-operation'
        assert result.checkpoint is None and result.commit == actual.commit
        assert binding.expected == latest != actual.commit


@pytest.mark.parametrize('size', [2 * 1024 * 1024, 8 * 1024 * 1024, 32 * 1024 * 1024])
def test_huge_record_full_streamed_activation_and_export_bound(tmp_path, monkeypatch, size):
    import tracemalloc
    from amplifier_foundation.session.media_conversion import verify_export
    with fixture(tmp_path) as (binding, store, fence):
        row = {'role': 'tool', 'tool_call_id': 'huge-original', 'status': 'complete',
               'provider_state': 'x' * size,
               'content': [{'type': 'image', 'source': {
                   'type': 'base64', 'media_type': 'image/png', 'data': base64.b64encode(png()).decode(),
               }}]}
        binding.append([row], expected=binding.expected, operation_id='seed', admit=lambda: None)
        del row
        expected = binding.expected
        origin = {'kind': 'migration', 'session_id': 'session', 'id': 'huge-original',
                  'created_at': '2025-01-01T00:00:00+00:00', 'expires_at': '2035-01-01T00:00:00+00:00'}
        def no_materializing_writer(*args, **kwargs):
            raise AssertionError('Huge candidate must not enter list/deepcopy writer')
        monkeypatch.setattr(binding, '_write', no_materializing_writer)
        tracemalloc.start()
        try:
            plan = preview_committed(binding.directory, expected=expected, owner='fixture', fence=fence)
            candidate = asyncio.run(build_candidate(
                plan, tmp_path / 'operation', fence=fence, capability=store,
                origin_for=lambda row, block: origin, eligible_record=lambda row: True,
                asset_reader=lambda ref: store.resolve_ref(ref, committed=False),
                export_assets=True,
            ))
            result = apply_committed_candidate(tmp_path / 'operation', **arguments(binding, store, expected))
            with open_committed_source(binding.directory, expected=binding.expected) as source:
                assert source.commit.message_count == 1
                assert sum(map(len, source.iter_bytes())) > size
            verify_export(tmp_path / 'operation/portable')
            _, peak = tracemalloc.get_traced_memory()
        finally:
            tracemalloc.stop()
        assert result.state == 'acknowledged' and result.inline_removed_bytes == candidate.inline_removed_bytes > 0
        assert type(result.checkpoint) is VersionedCheckpointReceipt
        assert peak < 6 * 1024 * 1024, peak
        print({'single_record_bytes': candidate.bytes, 'python_peak_bytes': peak,
               'installed_records': result.commit.message_count})
