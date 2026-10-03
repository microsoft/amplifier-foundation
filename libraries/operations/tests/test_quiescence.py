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

SERVICE_IDENTITY = dict(installationId='installation', dataScope=CONTEXT['dataScope'],
    ownerId='retained-process-owner', instanceId=CONTEXT['instanceId'], releaseDigest='sha256:signed-release')
SERVICE = {**CONTEXT, 'purpose': 'service-stop', 'serviceIdentity': SERVICE_IDENTITY}

def service_proof(outcome='ready'):
    observed = {**SERVICE_IDENTITY, 'instanceId': 'replacement' if outcome == 'ready' else CONTEXT['instanceId']}
    return {'verified': True, 'fenceId': CONTEXT['fenceId'], 'commandId': CONTEXT['commandId'],
        'outcome': outcome, 'instanceId': observed['instanceId'], 'dataScope': observed['dataScope'],
        'receiptId': 'service-settlement', 'kind': 'service-lifecycle',
        'serviceOutcome': 'resumed' if outcome == 'ready' else 'stop-refused',
        'expected': dict(SERVICE_IDENTITY), 'observed': observed,
        **({'resumeCommandId': 'resume', 'exitReceiptId': 'owned-exit', 'readyReceiptId': 'authenticated-ready'}
           if outcome == 'ready' else {'refusalReceiptId': 'confirmed-no-signal'})}

def test_service_identity_is_required_bound_and_detached_from_caller(tmp_path):
    gate = DurableIntakeFence(tmp_path / 'gate.sqlite3')
    try:
        for invalid in [None, {}, {**SERVICE_IDENTITY, 'instanceId': 'foreign'},
                        {**SERVICE_IDENTITY, 'dataScope': 'foreign'},
                        {**SERVICE_IDENTITY, 'releaseDigest': 'bad\nvalue'}]:
            with pytest.raises(ValueError):
                gate.acquire({**SERVICE, 'serviceIdentity': invalid})
        caller = {**SERVICE, 'serviceIdentity': dict(SERVICE_IDENTITY)}
        gate.acquire(caller)
        caller['serviceIdentity']['ownerId'] = 'changed'
        assert gate.fence == SERVICE
        with pytest.raises(ValueError):
            gate.release({**SERVICE, 'outcome': 'ready', 'proof': proof('ready', 'replacement')})
        assert gate.fence == SERVICE
    finally:
        gate.close()

@pytest.mark.parametrize('field', ['installationId', 'dataScope', 'ownerId', 'releaseDigest', 'instanceId'])
@pytest.mark.parametrize('side', ['expected', 'observed'])
def test_service_release_rejects_cross_identity_proof(tmp_path, field, side):
    gate = DurableIntakeFence(tmp_path / 'gate.sqlite3')
    try:
        gate.acquire(SERVICE)
        candidate = service_proof()
        candidate[side][field] = 'foreign'
        with pytest.raises(ValueError):
            gate.release({**SERVICE, 'outcome': 'ready', 'proof': candidate})
        assert gate.fence == SERVICE
    finally:
        gate.close()

@pytest.mark.parametrize('field,value', [('kind', 'distribution-update'), ('exitReceiptId', None),
    ('readyReceiptId', None), ('resumeCommandId', ''), ('serviceOutcome', 'stop-refused'),
    ('receiptId', 'bad\nreceipt'), ('unexpected', True)])
def test_service_release_requires_complete_exact_receipt_shape(tmp_path, field, value):
    gate = DurableIntakeFence(tmp_path / 'gate.sqlite3')
    try:
        gate.acquire(SERVICE)
        with pytest.raises(ValueError):
            gate.release({**SERVICE, 'outcome': 'ready', 'proof': {**service_proof(), field: value}})
        assert gate.fence == SERVICE
    finally:
        gate.close()

@pytest.mark.parametrize('outcome', ['ready', 'unchanged'])
def test_service_release_lost_ack_restart_preserves_full_exact_proof(tmp_path, outcome):
    path = tmp_path / 'gate.sqlite3'
    gate = DurableIntakeFence(path)
    gate.acquire(SERVICE)
    gate.close()
    gate = DurableIntakeFence(path)
    release = {**SERVICE, 'outcome': outcome, 'proof': service_proof(outcome)}
    assert gate.release(release)['released']
    gate.close()
    gate = DurableIntakeFence(path)
    try:
        assert gate.release(release)['released']
        field = 'readyReceiptId' if outcome == 'ready' else 'refusalReceiptId'
        with pytest.raises(ValueError, match='exact retained'):
            gate.release({**release, 'proof': {**release['proof'], field: 'changed-proof'}})
        with pytest.raises(ValueError):
            gate.release({**release, 'outcome': 'unknown'})
        with pytest.raises(ValueError):
            gate.acquire(SERVICE)
    finally:
        gate.close()

def test_service_partial_acquisition_rollback_is_same_live_lease_only(tmp_path):
    path = tmp_path / 'gate.sqlite3'
    gate = DurableIntakeFence(path)
    rollback = {**SERVICE, 'outcome': 'unchanged', 'proof': {'kind': 'admission-refused'}}
    gate.acquire(SERVICE)
    assert gate.release(rollback)['released']
    next_context = {**SERVICE, 'fenceId': 'second-fence'}
    gate.acquire(next_context)
    gate.close()
    gate = DurableIntakeFence(path)
    try:
        assert gate.fence == next_context
        assert gate.acquire(next_context)['acquired']  # Inspection does not reacquire ownership.
        with pytest.raises(ValueError, match='newly acquired live lease'):
            gate.release({**rollback, 'fenceId': 'second-fence'})
        assert gate.release({**next_context, 'outcome': 'unknown'})['intakeClosed']
        candidate = {**service_proof('unchanged'), 'fenceId': 'second-fence'}
        assert gate.release({**next_context, 'outcome': 'unchanged', 'proof': candidate})['released']
    finally:
        gate.close()
