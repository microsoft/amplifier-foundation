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


@pytest.mark.parametrize('reopen', [False, True])
def test_unknown_service_outcome_permanently_retires_live_rollback(tmp_path, reopen):
    path = tmp_path / 'gate.sqlite3'
    gate = DurableIntakeFence(path)
    gate.acquire(SERVICE)
    assert gate.release({**SERVICE, 'outcome': 'unknown'})['intakeClosed']
    if reopen:
        gate.close()
        gate = DurableIntakeFence(path)
    try:
        assert gate.acquire(SERVICE)['acquired']
        with pytest.raises(ValueError, match='newly acquired live lease'):
            gate.release({**SERVICE, 'outcome': 'unchanged',
                          'proof': {'kind': 'admission-refused'}})
        assert gate.fence == SERVICE
        assert gate.release({**SERVICE, 'outcome': 'unchanged',
                             'proof': service_proof('unchanged')})['released']
    finally:
        gate.close()


# Startup must preserve existing authority instead of manufacturing empty state.
import hashlib
import json
import sqlite3
import subprocess
import sys


def authority_hashes(path):
    return {suffix: hashlib.sha256(Path(str(path) + suffix).read_bytes()).hexdigest()
            for suffix in ('', '-wal')
            if Path(str(path) + suffix).exists() and Path(str(path) + suffix).stat().st_size}


@pytest.mark.parametrize('tables', [('fence',), ('releases',), ('fence', 'releases')])
@pytest.mark.parametrize('profile', ['generic-held', 'generic-released', 'service-held', 'service-released'])
def test_existing_missing_authority_never_reinitializes(tmp_path, tables, profile):
    path = tmp_path / 'intake.sqlite'
    context = SERVICE if profile.startswith('service') else CONTEXT
    gate = DurableIntakeFence(path)
    gate.acquire(context)
    if profile.endswith('released'):
        receipt = service_proof('unchanged') if profile.startswith('service') else proof()
        gate.release({**context, 'outcome': 'unchanged', 'proof': receipt})
    gate.close()
    with sqlite3.connect(path) as db:
        for table in tables:
            db.execute('DROP TABLE ' + table)
    before = authority_hashes(path)
    with pytest.raises(ValueError, match='authority schema'):
        DurableIntakeFence(path)
    assert authority_hashes(path) == before
    with sqlite3.connect(path) as db:
        names = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        assert not names.intersection(tables)


@pytest.mark.parametrize('suffix', ['-wal', '-shm', '-journal'])
@pytest.mark.parametrize('content', [b'', b'uncertain retained authority'])
def test_absent_main_with_any_sidecar_is_not_new(tmp_path, suffix, content):
    path = tmp_path / 'intake.sqlite'
    sidecar = Path(str(path) + suffix)
    sidecar.write_bytes(content)
    with pytest.raises(ValueError, match='retained sidecars'):
        DurableIntakeFence(path)
    assert not path.exists()
    assert sidecar.read_bytes() == content


def test_existing_empty_database_is_not_new(tmp_path):
    path = tmp_path / 'intake.sqlite'
    path.touch()
    with pytest.raises(ValueError, match='authority schema'):
        DurableIntakeFence(path)
    assert path.read_bytes() == b''


def crash_left_store(path, *, drop=()):
    # Real process exit leaves committed WAL in place without connection.close.
    program = """import json,os,sys
from amplifier_operations.quiescence import DurableIntakeFence
store=DurableIntakeFence(sys.argv[1]); store.acquire(json.loads(sys.argv[2]))
with store.db:
    for table in json.loads(sys.argv[3]): store.db.execute('DROP TABLE '+table)
os._exit(0)
"""
    subprocess.run([sys.executable, '-I', '-B', '-c', program,
                    str(path), json.dumps(CONTEXT),
                    json.dumps(drop)], check=True)
    assert Path(str(path) + '-wal').stat().st_size > 0


@pytest.mark.parametrize('tables', [('fence',), ('releases',), ('fence', 'releases')])
def test_readonly_refusal_preserves_crash_main_and_nonempty_wal(tmp_path, tables):
    path = tmp_path / 'intake.sqlite'
    crash_left_store(path, drop=tables)
    before = authority_hashes(path)
    assert set(before) == {'', '-wal'}
    with pytest.raises(ValueError, match='authority schema'):
        DurableIntakeFence(path)
    assert authority_hashes(path) == before


def test_valid_crash_wal_recovers_held_fence(tmp_path):
    path = tmp_path / 'intake.sqlite'
    crash_left_store(path)
    gate = DurableIntakeFence(path)
    try:
        assert gate.fence == CONTEXT
        assert gate.release({**CONTEXT, 'outcome': 'unknown'})['intakeClosed']
    finally:
        gate.close()


def test_missing_main_preserves_actual_crash_wal(tmp_path):
    path = tmp_path / 'intake.sqlite'
    crash_left_store(path)
    path.unlink()
    before = authority_hashes(path)
    with pytest.raises(ValueError, match='retained sidecars'):
        DurableIntakeFence(path)
    assert not path.exists()
    assert authority_hashes(path) == before


def test_legacy_two_table_schema_remains_compatible(tmp_path):
    path = tmp_path / 'legacy.sqlite'
    with sqlite3.connect(path) as db:
        db.executescript("""CREATE TABLE fence(id INTEGER PRIMARY KEY CHECK(id=1),value TEXT NOT NULL);
        CREATE TABLE releases(fence TEXT PRIMARY KEY,value TEXT NOT NULL);""")
    gate = DurableIntakeFence(path)
    try:
        assert gate.acquire(CONTEXT)['acquired']
        assert gate.release({**CONTEXT, 'outcome': 'unchanged', 'proof': proof()})['released']
    finally:
        gate.close()


@pytest.mark.parametrize('replacement', [
    'CREATE TABLE fence(id INTEGER PRIMARY KEY CHECK(id=1),wrong TEXT NOT NULL)',
    'CREATE VIEW fence AS SELECT 1 AS id, NULL AS value',
])
def test_existing_invalid_authority_shape_refuses_without_write(tmp_path, replacement):
    path = tmp_path / 'intake.sqlite'
    gate = DurableIntakeFence(path)
    gate.close()
    with sqlite3.connect(path) as db:
        db.execute('DROP TABLE fence')
        db.execute(replacement)
    before = authority_hashes(path)
    with pytest.raises(ValueError, match='authority schema'):
        DurableIntakeFence(path)
    assert authority_hashes(path) == before


def test_schema_refusal_opens_only_readonly_and_closes_reader(tmp_path, monkeypatch):
    path = tmp_path / 'intake.sqlite'
    gate = DurableIntakeFence(path)
    gate.close()
    with sqlite3.connect(path) as db:
        db.execute('DROP TABLE fence')
    connect = sqlite3.connect
    connections = []
    def tracked(database, **kwargs):
        assert str(database).endswith('?mode=ro')
        connection = connect(database, **kwargs)
        connections.append(connection)
        return connection
    monkeypatch.setattr(sqlite3, 'connect', tracked)
    with pytest.raises(ValueError, match='authority schema'):
        DurableIntakeFence(path)
    assert len(connections) == 1
    with pytest.raises(sqlite3.ProgrammingError, match='closed'):
        connections[0].execute('SELECT 1')


def test_writer_failure_closes_connection_and_retains_new_file(tmp_path, monkeypatch):
    path = tmp_path / 'intake.sqlite'
    connect = sqlite3.connect
    connections = []
    def tracked(database, **kwargs):
        connection = connect(database, **kwargs)
        connections.append(connection)
        return connection
    def refuse_chmod(self, mode):
        raise PermissionError('inert fixture permission refusal')
    monkeypatch.setattr(sqlite3, 'connect', tracked)
    monkeypatch.setattr(Path, 'chmod', refuse_chmod)
    with pytest.raises(PermissionError):
        DurableIntakeFence(path)
    assert path.exists()
    assert len(connections) == 1
    with pytest.raises(sqlite3.ProgrammingError, match='closed'):
        connections[0].execute('SELECT 1')
