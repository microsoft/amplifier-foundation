"""Actual prospective binding activation; synthetic temporary sessions only."""
import asyncio
import base64
from contextlib import closing, contextmanager
from dataclasses import asdict
import json

import pytest

from amplifier_foundation.session.media_activation import (
    ActivationRefused, apply_committed_candidate, resume_committed_candidate,
    reconcile_committed_candidate,
)
from amplifier_foundation.session.media_conversion import build_candidate, preview_committed
from amplifier_foundation.session.transcript_source import open_committed_source
from amplifier_foundation.session.versioned_checkpoint import VersionedCheckpointReceipt
from tests.test_media_conversion_t2 import fixture, png, published


def prepared(tmp_path, binding, store, fence, *, inline=False):
    if inline:
        row = {'role': 'tool', 'tool_call_id': 'actual-original', 'status': 'complete',
               'usage': {'output_tokens': 8}, 'content': [{'type': 'image', 'source': {
                   'type': 'base64', 'media_type': 'image/png', 'data': base64.b64encode(png()).decode(),
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


def test_inline_rewrite_real_binding_typed_ack_readback_and_original_retained(tmp_path):
    with fixture(tmp_path) as (binding, store, fence):
        expected, original, candidate = prepared(tmp_path, binding, store, fence, inline=True)
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
            assert stream.read() == png()
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
