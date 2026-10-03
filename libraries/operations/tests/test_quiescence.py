from pathlib import Path
import pytest
from amplifier_operations.quiescence import DurableIntakeFence

CONTEXT = dict(fenceId='fence', commandId='command', purpose='operator-selected-purpose',
               instanceId='instance', dataScope='owner-data')
def proof(outcome='unchanged', instance='instance'):
    return {**CONTEXT, 'verified': True, 'outcome': outcome, 'instanceId': instance,
            'receiptId': 'authenticated-durable-receipt'}

def test_busy_refusal_is_no_effect_and_held_fence_survives_restart(tmp_path):
    path = tmp_path / 'intake.sqlite3'
    gate = DurableIntakeFence(path)
    gate.calls = 1
    assert gate.acquire(CONTEXT)['executed'] is False
    assert gate.fence is None
    gate.calls = 0
    gate.background = 1
    assert gate.acquire(CONTEXT)['acquired'] is False
    gate.background = 0
    assert gate.acquire(CONTEXT, pending=1)['acquired'] is False
    assert gate.acquire(CONTEXT)['acquired'] is True
    assert gate.acquire(CONTEXT)['acquired'] is True
    gate.close()
    gate = DurableIntakeFence(path)
    try:
        assert gate.fence == CONTEXT
        assert gate.release({**CONTEXT, 'outcome': 'unknown'})['intakeClosed'] is True
        with pytest.raises(ValueError):
            gate.acquire({**CONTEXT, 'commandId': 'different'})
    finally:
        gate.close()

@pytest.mark.parametrize('changed', [dict(dataScope='other'), dict(commandId='other'),
    dict(fenceId='other'), dict(verified=False), dict(outcome='ready'), dict(instanceId='new')])
def test_release_requires_exact_trusted_proof(tmp_path, changed):
    gate = DurableIntakeFence(tmp_path / 'gate.sqlite3')
    try:
        gate.acquire(CONTEXT)
        with pytest.raises(ValueError):
            gate.release({**CONTEXT, 'outcome': 'unchanged', 'proof': {**proof(), **changed}})
        assert gate.fence == CONTEXT
        assert gate.release({**CONTEXT, 'outcome': 'unchanged', 'proof': proof()})['released']
    finally:
        gate.close()

@pytest.mark.parametrize('outcome, instance', [('unchanged', 'instance'), ('ready', 'new-instance')])
def test_exact_release_idempotence_and_released_fence_cannot_be_reused(tmp_path, outcome, instance):
    gate = DurableIntakeFence(tmp_path / 'gate.sqlite3')
    release = {**CONTEXT, 'outcome': outcome, 'proof': proof(outcome, instance)}
    try:
        gate.acquire(CONTEXT)
        gate.calls = 1
        with pytest.raises(ValueError, match='in flight'):
            gate.release(release)
        gate.calls = 0
        assert gate.release(release)['released']
        assert gate.release(release)['released']
        with pytest.raises(ValueError):
            gate.release({**release, 'outcome': 'unknown'})
        with pytest.raises(ValueError):
            gate.acquire(CONTEXT)
    finally:
        gate.close()

def test_known_pre_effect_refusal_releases_without_guessing_process_outcome(tmp_path):
    gate = DurableIntakeFence(tmp_path / 'gate.sqlite3')
    try:
        gate.acquire(CONTEXT)
        assert gate.release({**CONTEXT, 'outcome': 'unchanged', 'proof': {'kind': 'admission-refused'}})['released']
        with pytest.raises(ValueError):
            gate.acquire({**CONTEXT, 'fenceId': 'bad\nvalue'})
    finally:
        gate.close()
