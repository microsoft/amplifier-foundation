"""SQLite authority for schedule revisions, due identities, leases and receipts."""
from contextlib import contextmanager
import copy
import hashlib
import json
from pathlib import Path
import sqlite3
import uuid

from .policy import due_occurrence

ACTIVE_RUNS = ('creating', 'claimed', 'submitting', 'accepted', 'running', 'unknown')
TERMINAL_RUNS = ('completed', 'failed', 'skipped', 'abandoned')


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False, allow_nan=False).encode()).hexdigest()


class ScheduleStore:
    def __init__(self, path, *, owner=None, history_limit=None):
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path, timeout=5, isolation_level=None)
        path.chmod(0o600)
        self.db.execute('PRAGMA journal_mode=WAL')
        self.db.execute('PRAGMA synchronous=FULL')
        self.db.executescript('''
            CREATE TABLE IF NOT EXISTS schedules(id TEXT PRIMARY KEY, session_id TEXT NOT NULL, value TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS schedule_runs(id TEXT PRIMARY KEY, schedule_id TEXT NOT NULL, session_id TEXT NOT NULL, due REAL NOT NULL, phase TEXT NOT NULL, value TEXT NOT NULL);
            CREATE UNIQUE INDEX IF NOT EXISTS schedule_due ON schedule_runs(schedule_id,due);
            CREATE TABLE IF NOT EXISTS schedule_commands(id TEXT PRIMARY KEY, fingerprint TEXT NOT NULL, result TEXT NOT NULL);
            CREATE INDEX IF NOT EXISTS schedules_session ON schedules(session_id);
            CREATE INDEX IF NOT EXISTS schedules_due ON schedules(json_extract(value,'$.status'),json_extract(value,'$.nextDue'));
            CREATE INDEX IF NOT EXISTS schedule_runs_session ON schedule_runs(session_id,schedule_id,due DESC);
            CREATE INDEX IF NOT EXISTS schedule_runs_phase ON schedule_runs(phase);
            CREATE INDEX IF NOT EXISTS schedule_runs_destination ON schedule_runs(json_extract(value,'$.destinationSessionId'),due DESC);
            CREATE TABLE IF NOT EXISTS scheduler_lease(id INTEGER PRIMARY KEY, owner TEXT NOT NULL, expires REAL NOT NULL);
        ''')
        self.owner = owner or str(uuid.uuid4())
        self.history_limit = history_limit
        self.recovered = []

    @contextmanager
    def transaction(self):
        self.db.execute('BEGIN IMMEDIATE')
        try:
            yield
            self.db.execute('COMMIT')
        except BaseException:
            self.db.execute('ROLLBACK')
            raise

    def close(self): self.db.close()

    def list(self, session_id=None):
        rows = self.db.execute('SELECT value FROM schedules' + (' WHERE session_id=?' if session_id else ''), (session_id,) if session_id else ())
        return [json.loads(row[0]) for row in rows]

    def page(self, session_id, *, limit=50, after=0):
        if not 1 <= limit <= 100 or after < 0:raise ValueError('Invalid schedule page bounds')
        rows=self.db.execute('SELECT rowid,value FROM schedules WHERE session_id=? AND rowid>? ORDER BY rowid LIMIT ?', (session_id,after,limit+1)).fetchall()
        return {'items':[json.loads(r[1]) for r in rows[:limit]],'nextCursor':rows[limit-1][0] if len(rows)>limit else None}

    def due(self, now, *, limit=50):
        if not 1 <= limit <= 100:raise ValueError('Invalid due page bound')
        return [json.loads(r[0]) for r in self.db.execute("SELECT value FROM schedules WHERE json_extract(value,'$.status')='active' AND json_extract(value,'$.nextDue')<=? ORDER BY json_extract(value,'$.nextDue') LIMIT ?",(now,limit))]

    def run_page(self, session_id, schedule_id=None, *, limit=50, before=None):
        if not 1 <= limit <= 100:raise ValueError('Invalid run page bound')
        where=['session_id=?'];args=[session_id]
        if schedule_id is not None:where.append('schedule_id=?');args.append(schedule_id)
        if before is not None:where.append('due<?');args.append(before)
        rows=self.db.execute('SELECT value FROM schedule_runs WHERE '+' AND '.join(where)+' ORDER BY due DESC LIMIT ?',(*args,limit+1)).fetchall()
        values=[json.loads(r[0]) for r in rows[:limit]]
        return {'items':values,'nextCursor':values[-1]['dueAt'] if len(rows)>limit else None}

    def projection(self):
        """Fresh display rows with the latest ten runs, in one read snapshot.

        Query schedules rather than every catalog session. The existing due
        index bounds each schedule's history read; commands and admission still
        use the authoritative get/run methods and their revision checks.
        """
        rows = self.db.execute('''
            SELECT s.session_id, s.value, (
                SELECT json_group_array(value) FROM (
                    SELECT value FROM schedule_runs
                    WHERE session_id=s.session_id AND schedule_id=s.id
                    ORDER BY due DESC LIMIT 10
                )
            ) FROM schedules AS s ORDER BY s.rowid
        ''')
        result = {}
        for session_id, value, runs in rows:
            result.setdefault(session_id, []).append({**json.loads(value), 'runs': [json.loads(run) for run in json.loads(runs)]})
        return result

    def get(self, session_id, identity):
        row = self.db.execute('SELECT value FROM schedules WHERE id=? AND session_id=?', (identity, session_id)).fetchone()
        if row is None: raise ValueError('This schedule does not belong to the conversation')
        return json.loads(row[0])

    def put(self, record):
        self.db.execute('INSERT OR REPLACE INTO schedules VALUES(?,?,?)', (record['id'], record['sessionId'], json.dumps(record)))

    def mutate(self, operation, args, command_id, build):
        """Pure builder runs inside a transaction; retries return original receipt."""
        if not isinstance(command_id, str) or not command_id: raise ValueError('A stable schedule command ID is required')
        signature = fingerprint([operation, args])
        with self.transaction():
            receipt = self.db.execute('SELECT fingerprint,result FROM schedule_commands WHERE id=?', (command_id,)).fetchone()
            if receipt:
                if receipt[0] != signature: raise ValueError('This command ID has different schedule contents')
                return {**json.loads(receipt[1]), 'duplicate': True}
            previous = self.get(args['sessionId'], args['id']) if args.get('id') else None
            revision = previous['revision'] if previous else 0
            if type(args.get('expectedRevision')) is not int or args['expectedRevision'] != revision: raise ValueError('The schedule revision changed; inspect it before editing')
            record = build(copy.deepcopy(previous))
            record['revision'] = revision + 1
            self.put(record)
            result = {'schedule': record}
            self.db.execute('INSERT INTO schedule_commands VALUES(?,?,?)', (command_id, signature, json.dumps(result)))
            return result

    def runs(self, session_id, schedule_id=None):
        suffix, args = (' AND schedule_id=?', (session_id, schedule_id)) if schedule_id else ('', (session_id,))
        return [json.loads(row[0]) for row in self.db.execute('SELECT value FROM schedule_runs WHERE session_id=?' + suffix + ' ORDER BY due DESC', args)]

    def run(self, session_id, identity):
        row = self.db.execute('SELECT value FROM schedule_runs WHERE session_id=? AND id=?', (session_id, identity)).fetchone()
        if row is None: raise ValueError('This run does not belong to the conversation')
        return json.loads(row[0])

    def execution_runs(self, session_id):
        return [json.loads(row[0]) for row in self.db.execute(
            "SELECT value FROM schedule_runs WHERE session_id=? OR json_extract(value,'$.destinationSessionId')=? ORDER BY due DESC", (session_id, session_id))]

    def incoming(self, session_id, schedule_id=None, *, limit=25):
        if not 1 <= limit <= 100:raise ValueError('Invalid incoming run limit')
        where="json_extract(value,'$.destinationSessionId')=?";args=[session_id]
        if schedule_id is not None:where+=' AND schedule_id=?';args.append(schedule_id)
        return [json.loads(r[0]) for r in self.db.execute('SELECT value FROM schedule_runs WHERE '+where+' ORDER BY due DESC LIMIT ?',(*args,limit))]

    def execution_run(self, session_id, identity):
        row = self.db.execute("SELECT value FROM schedule_runs WHERE id=?", (identity,)).fetchone()
        value = json.loads(row[0]) if row else None
        if value is None or session_id not in {value['sessionId'], value.get('destinationSessionId')}:
            raise ValueError('This run does not belong to the conversation')
        return value

    def put_run(self, record):
        self.db.execute('INSERT OR REPLACE INTO schedule_runs VALUES(?,?,?,?,?,?)', (record['id'], record['scheduleId'], record['sessionId'], record['dueAt'], record['phase'], json.dumps(record)))
        if self.history_limit is not None:
            self.db.execute("DELETE FROM schedule_runs WHERE id IN (SELECT id FROM schedule_runs WHERE session_id=? AND schedule_id=? AND phase IN ('completed','failed','skipped','abandoned') ORDER BY due DESC LIMIT -1 OFFSET ?)",(record['sessionId'],record['scheduleId'],self.history_limit))

    def acquire(self, now, ttl=30):
        self.recovered = []
        with self.transaction():
            lease = self.db.execute('SELECT owner,expires FROM scheduler_lease WHERE id=1').fetchone()
            if lease and lease[0] != self.owner and lease[1] > now: return False
            new_owner = lease is None or lease[0] != self.owner
            self.db.execute('INSERT OR REPLACE INTO scheduler_lease VALUES(1,?,?)', (self.owner, now + ttl))
            if new_owner:
                # Claims may have crossed an external input boundary. Never
                # infer from process death that submission did not happen.
                for row in self.db.execute("SELECT value FROM schedule_runs WHERE phase IN ('creating','claimed','submitting','accepted','running')").fetchall():
                    run = json.loads(row[0])
                    run.update(phase='unknown', revision=run['revision'] + 1, updatedAt=now, detail='Previous scheduler ownership ended; submission or outcome is uncertain. No replay was attempted.')
                    self.put_run(run)
                    self.recovered.append(run)
                    schedule = self.get(run['sessionId'], run['scheduleId'])
                    if schedule['status'] == 'active':
                        schedule.update(status='needs_review', revision=schedule['revision'] + 1, reviewReason=run['detail'])
                        self.put(schedule)
            return True

    def owns(self, now):
        row = self.db.execute('SELECT owner,expires FROM scheduler_lease WHERE id=1').fetchone()
        return bool(row and row[0] == self.owner and row[1] > now)

    def claim(self, session_id, identity, now):
        with self.transaction():
            if not self.owns(now): raise ValueError('Scheduler lease is no longer held')
            schedule = self.get(session_id, identity)
            if schedule['status'] != 'active' or self.db.execute("SELECT 1 FROM schedule_runs WHERE session_id=? AND schedule_id=? AND phase IN ('creating','claimed','submitting','accepted','running','unknown') LIMIT 1",(session_id,identity)).fetchone(): return None
            due = due_occurrence(schedule['spec'], schedule.get('nextDue'), now, schedule['missedRunPolicy'])
            if due is None: return None
            run_id = f"schedule:{identity}:run:{int(due['dueAt'])}"
            run = {'id': run_id, 'scheduleId': identity, 'sessionId': session_id, 'taskId': schedule.get('taskId'), 'taskRevision': schedule.get('taskRevision'), 'scheduleRevision': schedule['revision'], 'dueAt': due['dueAt'], 'phase': 'skipped' if due['skip'] else 'claimed', 'revision': 1, 'owner': self.owner, 'inputId': run_id, 'createdAt': now, 'updatedAt': now, 'detail': 'Missed occurrence skipped by the saved policy.' if due['skip'] else 'Due occurrence claimed.'}
            self.db.execute('INSERT INTO schedule_runs VALUES(?,?,?,?,?,?)', (run_id, identity, session_id, run['dueAt'], run['phase'], json.dumps(run)))
            schedule.update(nextDue=due['nextDue'])
            if due['skip'] and due['nextDue'] is None:
                schedule['status'] = 'completed'
                schedule['revision'] += 1
            self.put(schedule)
            self.put_run(run)
            return run

    def review(self, session_id, identity, reason, now):
        with self.transaction():
            record = self.get(session_id, identity)
            if record['status'] == 'active':
                record.update(status='needs_review', revision=record['revision'] + 1, reviewReason=reason, updatedAt=now)
                self.put(record)
            return record

    def transition(self, session_id, identity, phases, phase, now, **fields):
        with self.transaction():
            run = self.run(session_id, identity)
            if run['phase'] not in phases: return run
            if phase in {'creating', 'submitting'} and (run['owner'] != self.owner or not self.owns(now)): raise ValueError('Scheduler ownership changed before submission')
            run.update(fields, phase=phase, revision=run['revision'] + 1, updatedAt=now)
            self.put_run(run)
            return run

    def run_command(self, session_id, identity, revision, command_id, signature, edit):
        with self.transaction():
            previous = self.db.execute('SELECT fingerprint,result FROM schedule_commands WHERE id=?', (command_id,)).fetchone()
            if previous:
                if previous[0] != signature: raise ValueError('This command ID has different run contents')
                return {**json.loads(previous[1]), 'duplicate': True}
            run = self.run(session_id, identity)
            if type(revision) is not int or revision != run['revision']: raise ValueError('The scheduled run revision changed')
            edit(run)
            run['revision'] += 1
            self.put_run(run)
            result = {'run': run}
            self.db.execute('INSERT INTO schedule_commands VALUES(?,?,?)', (command_id, signature, json.dumps(result)))
            return result
