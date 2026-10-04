"""Durable intake fences for one externally exclusive owner.

Callers own authentication, execution accounting and their lifetime process lock.
A stored fence never expires or causes work to replay. Trusted proof is supplied
by the coordinator, never accepted directly from an untrusted action request.
"""
import json
import os
import sqlite3
from pathlib import Path

_SERVICE_KEYS = ('installationId', 'dataScope', 'ownerId', 'instanceId', 'releaseDigest')

def _service_identity(value):
    if not isinstance(value, dict) or set(value) != set(_SERVICE_KEYS):
        raise ValueError('Exact service identity required')
    if any(not isinstance(value[key], str) or not 1 <= len(value[key]) <= 200
           or any(ord(c) < 32 for c in value[key]) for key in _SERVICE_KEYS):
        raise ValueError('Bounded service identity required')
    return {key: value[key] for key in _SERVICE_KEYS}

def _service_proof(proof, context, outcome):
    allowed = {'verified', 'fenceId', 'commandId', 'outcome', 'instanceId',
               'dataScope', 'receiptId', 'kind', 'serviceOutcome', 'expected',
               'observed', 'resumeCommandId', 'exitReceiptId', 'readyReceiptId',
               'refusalReceiptId'}
    if (not isinstance(proof, dict) or set(proof) - allowed
            or proof.get('verified') is not True
            or proof.get('kind') != 'service-lifecycle'):
        raise ValueError('Service-specific authenticated release proof required')
    expected = _service_identity(proof.get('expected'))
    observed = _service_identity(proof.get('observed'))
    if expected != context['serviceIdentity'] or any(
        observed[key] != expected[key] for key in _SERVICE_KEYS if key != 'instanceId'
    ):
        raise ValueError('Service release must retain installation, owner, scope and release')
    if proof.get('instanceId') != observed['instanceId']:
        raise ValueError('Service release running instance differs from observed identity')
    if outcome == 'ready':
        required = ('resumeCommandId', 'exitReceiptId', 'readyReceiptId')
        if (proof.get('serviceOutcome') != 'resumed'
                or observed['instanceId'] == expected['instanceId']
                or 'refusalReceiptId' in proof):
            raise ValueError('Service resume requires a distinct confirmed replacement')
    elif outcome == 'unchanged':
        required = ('refusalReceiptId',)
        if (proof.get('serviceOutcome') != 'stop-refused' or observed != expected
                or any(key in proof for key in ('resumeCommandId', 'exitReceiptId', 'readyReceiptId'))):
            raise ValueError('Refused service stop must retain the original instance')
    else:
        raise ValueError('Invalid service release outcome')
    for key in (*required, 'receiptId', 'instanceId'):
        if (not isinstance(proof.get(key), str) or not 1 <= len(proof[key]) <= 200
                or any(ord(c) < 32 for c in proof[key])):
            raise ValueError('Bounded service receipt identity required')
    # Detach nested values from the trusted caller. The exact complete proof is
    # durable, so a lost acknowledgement cannot later accept a changed receipt.
    return {**proof, 'expected': expected, 'observed': observed}

class DurableIntakeFence:
    # Consumers advertise service-stop only when this public contract is present.
    SERVICE_STOP_VERSION = 1
    def __init__(self,path):
        path = Path(path).absolute()
        # Callers already hold exclusive lifetime ownership of this partition.
        # Sidecars can retain committed authority after a crash. Never mistake
        # an absent main file with retained sidecars for a genuinely new store.
        new = not os.path.lexists(path)
        if new and any(os.path.lexists(str(path) + suffix)
                       for suffix in ('-wal', '-shm', '-journal')):
            raise ValueError('Intake store has retained sidecars without its main database')
        if not new:
            # A writable connection can checkpoint WAL when closed, including
            # after validation refuses startup. Validate with WAL-aware mode=ro
            # before opening any writer; immutable=1 would ignore retained WAL.
            reader = sqlite3.connect(path.as_uri() + '?mode=ro', uri=True)
            try:
                self._validate_schema(reader)
                row = reader.execute('SELECT value FROM fence WHERE id=1').fetchone()
                if row:
                    json.loads(row[0])
            finally:
                reader.close()
        else:
            # Exclusive creation avoids accidentally initializing a file that
            # appeared after classification. A failed startup retains evidence.
            fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            os.close(fd)
        db = sqlite3.connect(path.as_uri() + '?mode=rw', uri=True)
        try:
            path.chmod(0o600)
            if new:
                db.executescript("""CREATE TABLE fence(id INTEGER PRIMARY KEY CHECK(id=1),value TEXT NOT NULL);
                  CREATE TABLE releases(fence TEXT PRIMARY KEY,value TEXT NOT NULL);""")
            db.execute('PRAGMA journal_mode=WAL')
            db.execute('PRAGMA synchronous=FULL')
            row = db.execute('SELECT value FROM fence WHERE id=1').fetchone()
            self.fence = json.loads(row[0]) if row else None
        except BaseException:
            db.close()
            raise
        self.db = db
        self.calls=0;self.background=0;self._live_fence=None

    @staticmethod
    def _validate_schema(db):
        # These columns are shared by the original and service-stop profiles.
        # Existing authority is never repaired or inferred from an empty table.
        expected = {
            'fence': {'id': ('INTEGER', 0, 1), 'value': ('TEXT', 1, 0)},
            'releases': {'fence': ('TEXT', 0, 1), 'value': ('TEXT', 1, 0)},
        }
        for table, columns in expected.items():
            kind = db.execute('SELECT type FROM sqlite_master WHERE name=?', (table,)).fetchone()
            actual = {row[1]: (row[2].upper(), row[3], row[5])
                      for row in db.execute('PRAGMA table_info(' + table + ')')}
            if kind != ('table',) or any(actual.get(name) != definition
                                        for name, definition in columns.items()):
                raise ValueError('Existing intake store is missing required authority schema')
        # Ensure both retained tables are readable before a writable connection.
        db.execute('SELECT fence,value FROM releases LIMIT 1').fetchone()

    def context(self,value):
        if not isinstance(value,dict):raise ValueError('Trusted quiescence mapping required')
        required=('fenceId','commandId','purpose','instanceId','dataScope')
        if any(not isinstance(value.get(key),str) or not 1<=len(value[key])<=200 or any(ord(c)<32 for c in value[key]) for key in required):raise ValueError('Bounded trusted quiescence context required')
        context = {key:value[key] for key in required}
        if context['purpose'] == 'service-stop':
            service = _service_identity(value.get('serviceIdentity'))
            if any(service[key] != context[key] for key in ('instanceId', 'dataScope')):
                raise ValueError('Service identity differs from acquired host context')
            context['serviceIdentity'] = service
        return context
    def acquire(self,value,*,pending=0):
        context=self.context(value)
        if type(pending) is not int or pending<0:raise ValueError('Nonnegative active work count required')
        if self.fence:
            if self.fence!=context:raise ValueError('Owner intake already belongs to another exact fence')
            return {'acquired':True,'intakeClosed':True,'fenceId':context['fenceId']}
        if self.db.execute('SELECT 1 FROM releases WHERE fence=?',(context['fenceId'],)).fetchone():raise ValueError('A released fence cannot acquire owner intake again')
        if self.calls or self.background or pending:return {'acquired':False,'executed':False,'reason':'Owner has active admitted work'}
        with self.db:self.db.execute('INSERT INTO fence VALUES(1,?)',(json.dumps(context),))
        self.fence=context;self._live_fence=context
        return {'acquired':True,'intakeClosed':True,'fenceId':context['fenceId']}
    def release(self,value):
        context=self.context(value);outcome=value.get('outcome');proof=value.get('proof')
        if context['purpose'] == 'service-stop' and proof != {'kind':'admission-refused'} and outcome != 'unknown':
            proof = _service_proof(proof, context, outcome)
        elif isinstance(proof,dict) and proof.get('verified') is True:
            for key in ('instanceId','receiptId'):
                if not isinstance(proof.get(key),str) or not 1<=len(proof[key])<=200 or any(ord(c)<32 for c in proof[key]):raise ValueError('Bounded release proof identity required')
            proof={key:proof.get(key) for key in ('verified','fenceId','commandId','outcome','instanceId','dataScope','receiptId')}
        receipt={'context':context,'outcome':outcome,'proof':proof}
        if self.fence is None:
            row=self.db.execute('SELECT value FROM releases WHERE fence=?',(context['fenceId'],)).fetchone()
            if row and json.loads(row[0])==receipt:return {'released':True,'intakeClosed':False}
            raise ValueError('No exact retained owner release receipt')
        if self.fence!=context:raise ValueError('Owner release does not identify the current fence')
        if outcome=='unknown':
            self._live_fence=None
            return {'released':False,'intakeClosed':True}
        known=outcome=='unchanged' and proof=={'kind':'admission-refused'}
        if known and context['purpose'] == 'service-stop' and self._live_fence != context:
            raise ValueError('Service admission rollback requires its exact newly acquired live lease')
        if not known:
            if not isinstance(proof,dict) or proof.get('verified') is not True or outcome not in {'ready','unchanged'} or any(proof.get(key)!=context[key] for key in ('fenceId','commandId','dataScope')) or proof.get('outcome')!=outcome or not proof.get('receiptId'):raise ValueError('Exact host-verified release proof required')
            if not isinstance(proof.get('instanceId'),str) or (proof['instanceId']==context['instanceId'])!=(outcome=='unchanged'):raise ValueError('Release running instance does not match its outcome')
        if self.calls or self.background:raise ValueError('Owner work is still in flight')
        with self.db:
            self.db.execute('INSERT OR IGNORE INTO releases VALUES(?,?)',(context['fenceId'],json.dumps(receipt)))
            self.db.execute('DELETE FROM fence WHERE id=1')
        self.fence=None;self._live_fence=None;return {'released':True,'intakeClosed':False}
    def close(self):self.db.close()
