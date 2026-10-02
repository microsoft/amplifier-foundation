"""SQLite owns derived search data; callers own source access and authorization."""
import copy
import base64
import hashlib
import json
from pathlib import Path
import re
import sqlite3
import threading
import time
import uuid
from contextlib import contextmanager


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


class RecallStore:
    def __init__(self, path, *, retain_versions=None, max_text_characters=None):
        path = Path(path)
        if retain_versions is not None and (isinstance(retain_versions, bool) or not isinstance(retain_versions, int) or retain_versions < 1):
            raise ValueError('retain_versions must be a positive explicit policy or None')
        if max_text_characters is not None and (isinstance(max_text_characters, bool) or not isinstance(max_text_characters, int) or max_text_characters < 1):
            raise ValueError('max_text_characters must be a positive explicit policy or None')
        self.retain_versions, self.max_text_characters = retain_versions, max_text_characters
        self.lock = threading.RLock()
        self.db = sqlite3.connect(path, check_same_thread=False)
        path.chmod(0o600)
        self.db.execute('PRAGMA journal_mode=WAL')
        self.db.execute('PRAGMA secure_delete=ON')
        self.db.executescript('''
          CREATE TABLE IF NOT EXISTS sources(id TEXT PRIMARY KEY, signature TEXT, revision TEXT, value TEXT);
          CREATE VIRTUAL TABLE IF NOT EXISTS messages USING fts5(text, session_id UNINDEXED, identity UNINDEXED, metadata UNINDEXED, tokenize='unicode61');
          CREATE TABLE IF NOT EXISTS memories(id TEXT PRIMARY KEY, scope TEXT, target TEXT, revision INTEGER, value TEXT);
          CREATE TABLE IF NOT EXISTS memory_versions(id TEXT, revision INTEGER, value TEXT, PRIMARY KEY(id,revision));
          CREATE TABLE IF NOT EXISTS memory_receipts(id TEXT PRIMARY KEY, fingerprint TEXT, result TEXT);
          CREATE INDEX IF NOT EXISTS memories_scope ON memories(scope,target,id);
          CREATE TABLE IF NOT EXISTS recall_meta(key TEXT PRIMARY KEY,value INTEGER NOT NULL);
          INSERT OR IGNORE INTO recall_meta VALUES('revision',0);
          CREATE TABLE IF NOT EXISTS staged_sources(token TEXT PRIMARY KEY,source TEXT NOT NULL,signature TEXT,revision TEXT,metadata TEXT,created REAL NOT NULL,base_generation TEXT);
          CREATE INDEX IF NOT EXISTS staged_by_source ON staged_sources(source);
          CREATE TABLE IF NOT EXISTS documents(rowid INTEGER PRIMARY KEY,source TEXT NOT NULL,generation TEXT NOT NULL,identity TEXT NOT NULL,text TEXT NOT NULL,metadata TEXT NOT NULL,UNIQUE(source,generation,identity));
          CREATE INDEX IF NOT EXISTS documents_generation ON documents(generation,rowid);
          CREATE VIRTUAL TABLE IF NOT EXISTS documents_fts USING fts5(text,content='documents',content_rowid='rowid',tokenize='unicode61');
          CREATE TRIGGER IF NOT EXISTS documents_insert AFTER INSERT ON documents BEGIN
            INSERT INTO documents_fts(rowid,text) VALUES(new.rowid,new.text);
          END;
          CREATE TRIGGER IF NOT EXISTS documents_delete AFTER DELETE ON documents BEGIN
            INSERT INTO documents_fts(documents_fts,rowid,text) VALUES('delete',old.rowid,old.text);
          END;
          CREATE TRIGGER IF NOT EXISTS documents_update AFTER UPDATE ON documents BEGIN
            INSERT INTO documents_fts(documents_fts,rowid,text) VALUES('delete',old.rowid,old.text);
            INSERT INTO documents_fts(rowid,text) VALUES(new.rowid,new.text);
          END;
        ''')
        columns = {row[1] for row in self.db.execute('PRAGMA table_info(sources)')}
        for name, sql in (('workspace', 'TEXT'), ('kind', 'TEXT'), ('parent', 'TEXT'), ('generation', 'TEXT'), ('available', 'INTEGER NOT NULL DEFAULT 1')):
            if name not in columns:
                self.db.execute('ALTER TABLE sources ADD COLUMN '+name+' '+sql)
        self.db.execute('CREATE INDEX IF NOT EXISTS sources_scope ON sources(workspace,available,kind,id)')
        self.db.execute('CREATE INDEX IF NOT EXISTS sources_available ON sources(available,id)')
        if 'base_generation' not in {row[1] for row in self.db.execute('PRAGMA table_info(staged_sources)')}:
            self.db.execute('ALTER TABLE staged_sources ADD COLUMN base_generation TEXT')
        self.db.commit()

    def close(self):
        with self.lock:
            self.db.close()

    @contextmanager
    def atomic(self):
        """Nestable memory transaction, including correction/supersession batches."""
        with self.lock:
            name = 'memory_' + uuid.uuid4().hex
            self.db.execute('SAVEPOINT '+name)
            try:
                yield
                self.db.execute('RELEASE SAVEPOINT '+name)
            except BaseException:
                self.db.execute('ROLLBACK TO SAVEPOINT '+name)
                self.db.execute('RELEASE SAVEPOINT '+name)
                raise

    def signatures(self):
        """Legacy explicit export. Online consumers should call signature(id)."""
        with self.lock:
            return dict(self.db.execute('SELECT id,signature FROM sources WHERE generation IS NOT NULL'))

    def signature(self, source):
        with self.lock:
            row = self.db.execute('SELECT signature FROM sources WHERE id=? AND generation IS NOT NULL', (source,)).fetchone()
        return row[0] if row else None

    def index_revision(self):
        with self.lock:
            return self.db.execute("SELECT value FROM recall_meta WHERE key='revision'").fetchone()[0]

    def source_count(self):
        with self.lock:
            return self.db.execute('SELECT COUNT(*) FROM sources WHERE available=1 AND generation IS NOT NULL').fetchone()[0]

    def source_page(self, *, after=None, limit=50):
        self._page(0, limit, 100)
        with self.lock:
            rows = self.db.execute('SELECT id,signature,revision,value FROM sources WHERE generation IS NOT NULL AND id>? ORDER BY id LIMIT ?', (after or '', limit+1)).fetchall()
        return {'items': [{'id': sid, 'signature': signature, 'revision': json.loads(revision), 'metadata': json.loads(value)} for sid,signature,revision,value in rows[:limit]], 'nextCursor': rows[limit-1][0] if len(rows)>limit else None}

    @staticmethod
    def _page(offset, limit, maximum):
        if isinstance(offset, bool) or not isinstance(offset, int) or offset < 0 or isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= maximum:
            raise ValueError('Invalid bounded page')

    def begin_source(self, session, signature, revision):
        """Stage one authorized source without replacing its readable generation."""
        if not isinstance(session.get('id'), str) or not session['id']:
            raise ValueError('An explicit source identity is required')
        token = uuid.uuid4().hex
        with self.lock, self.db:
            current = self.db.execute('SELECT generation FROM sources WHERE id=?', (session['id'],)).fetchone()
            self.db.execute('INSERT INTO staged_sources VALUES(?,?,?,?,?,?,?)', (token,session['id'],signature,json.dumps(revision),json.dumps(session),time.time(),current[0] if current else None))
        return token

    def append_source(self, token, rows):
        """Append at most 100 records. Callers retain only this page in memory."""
        if not isinstance(rows, (list, tuple)) or len(rows)>100:
            raise ValueError('Source ingestion requires pages of at most 100 records')
        with self.lock, self.db:
            stage = self.db.execute('SELECT source FROM staged_sources WHERE token=?', (token,)).fetchone()
            if not stage:
                raise ValueError('Source staging generation is unavailable')
            sid = stage[0]
            count = 0
            for row in rows:
                if row.get('role') not in {'user', 'assistant', 'task', 'artifact'} or not isinstance(row.get('text'), str):
                    continue
                identity = row.get('id')
                if not isinstance(identity, str) or not identity:
                    raise ValueError('Every paged source record needs a stable identity')
                metadata = {key: copy.deepcopy(row[key]) for key in ('role','via','createdAt','inputOrigin','questionId','scheduledRunId','sourceKind','recordId','recordRevision') if key in row}
                metadata.update(messageId=identity,sha256=hashlib.sha256(row['text'].encode()).hexdigest())
                metadata.setdefault('sourceKind','message')
                self.db.execute('INSERT INTO documents(source,generation,identity,text,metadata) VALUES(?,?,?,?,?) ON CONFLICT(source,generation,identity) DO UPDATE SET text=excluded.text,metadata=excluded.metadata', (sid,token,identity,row['text'],json.dumps(metadata)))
                count += 1
        return count

    def commit_source(self, token, *, expected_revision):
        """Publish only after the caller rechecks original source revision."""
        with self.atomic():
            stage = self.db.execute('SELECT source,signature,revision,metadata,base_generation FROM staged_sources WHERE token=?', (token,)).fetchone()
            if not stage or json.loads(stage[2]) != expected_revision:
                raise ValueError('Original source revision changed; incomplete staging was not published')
            sid,signature,revision,value,base = stage
            current = self.db.execute('SELECT generation FROM sources WHERE id=?', (sid,)).fetchone()
            if (current[0] if current else None) != base:
                raise ValueError('Another ingestion committed this source; the older staged generation was not published')
            metadata = json.loads(value)
            self.db.execute('INSERT INTO sources(id,signature,revision,value,workspace,kind,parent,generation,available) VALUES(?,?,?,?,?,?,?,?,1) ON CONFLICT(id) DO UPDATE SET signature=excluded.signature,revision=excluded.revision,value=excluded.value,workspace=excluded.workspace,kind=excluded.kind,parent=excluded.parent,generation=excluded.generation,available=1', (sid,signature,revision,value,metadata.get('workspace'),metadata.get('kind',metadata.get('sessionKind','root')) or 'root',metadata.get('parentId'),token))
            if base is not None:
                self.db.execute('DELETE FROM documents WHERE source=? AND generation=?', (sid,base))
            self.db.execute('DELETE FROM staged_sources WHERE token=?', (token,))
            self.db.execute("UPDATE recall_meta SET value=value+1 WHERE key='revision'")

    def discard_source(self, token):
        with self.atomic():
            if self.db.execute('SELECT 1 FROM staged_sources WHERE token=?', (token,)).fetchone():
                self.db.execute('DELETE FROM documents WHERE generation=?', (token,))
                self.db.execute('DELETE FROM staged_sources WHERE token=?', (token,))

    def set_available(self, source, available):
        with self.lock, self.db:
            self.db.execute('UPDATE sources SET available=? WHERE id=?', (bool(available),source))
            self.db.execute("UPDATE recall_meta SET value=value+1 WHERE key='revision'")

    def replace(self, session, signature, revision, rows):
        token = self.begin_source(session, signature, revision)
        count, batch = 0, []
        try:
            for index, row in enumerate(rows):
                batch.append({**row, 'id':row.get('id') or digest([session['id'],index,row.get('role'),row.get('text')])})
                if len(batch)==100:
                    count += self.append_source(token,batch);batch=[]
            if batch: count += self.append_source(token,batch)
            self.commit_source(token,expected_revision=revision)
            return count
        except BaseException:
            self.discard_source(token)
            raise

    def prune(self, retained):
        with self.lock, self.db:
            for sid, in self.db.execute('SELECT id FROM sources'):
                if sid not in retained:
                    self.db.execute('DELETE FROM documents WHERE source=?', (sid,))
                    self.db.execute('DELETE FROM sources WHERE id=?', (sid,))
            self.db.execute("UPDATE recall_meta SET value=value+1 WHERE key='revision'")

    def search(self, query, allowed, offset=0, limit=20):
        """Compatibility API for callers already holding a bounded authority set."""
        return self._search(query,offset,limit,allowed=allowed)

    def search_scope(self, query, *, session=None, workspace=None, include_children=False, include_internal=False, offset=0, limit=20, cursor=None):
        """Indexed scope predicates; never constructs an estate-sized allowlist."""
        fingerprint = digest([query,session,workspace,include_children,include_internal])
        with self.lock:
            revision = self.index_revision()
            if cursor:
                try: saved=json.loads(base64.urlsafe_b64decode(cursor))
                except Exception: raise ValueError('Invalid recall cursor') from None
                if saved.get('query')!=fingerprint or saved.get('revision')!=revision:
                    raise ValueError('Search scope or index changed; restart this result page')
                offset=saved['offset']
            result = self._search(query,offset,limit,session=session,workspace=workspace,include_children=include_children,include_internal=include_internal)
            result['indexRevision']=revision
            result['nextCursor']=base64.urlsafe_b64encode(json.dumps({'query':fingerprint,'revision':revision,'offset':result['nextOffset']}).encode()).decode() if result['nextOffset'] is not None else None
            return result

    def _search(self, query, offset, limit, *, allowed=None, session=None, workspace=None, include_children=True, include_internal=True):
        self._page(offset,limit,50)
        # User text is data, not an FTS expression. Limit term count and quote it.
        terms = re.findall(r'\w+', query, flags=re.UNICODE)[:20]
        if not terms:
            return {'items': [], 'nextOffset': None}
        match = ' AND '.join('"'+term.replace('"', '""')+'"' for term in terms)
        with self.lock, self.db:
            where, parameters = ['documents_fts MATCH ?', 'sources.available=1', 'sources.generation=documents.generation'], [match]
            if allowed is not None:
                self.db.execute('CREATE TEMP TABLE IF NOT EXISTS allowed_sources(id TEXT PRIMARY KEY)')
                self.db.execute('DELETE FROM allowed_sources')
                self.db.executemany('INSERT INTO allowed_sources VALUES (?)', ((sid,) for sid in allowed))
                where.append('sources.id IN (SELECT id FROM allowed_sources)')
            if session is not None: where.append('sources.id=?');parameters.append(session)
            if workspace is not None: where.append('sources.workspace=?');parameters.append(workspace)
            if not include_children: where.append("sources.parent IS NULL AND sources.kind NOT IN ('worker','child')")
            if not include_internal: where.append("sources.kind<>'internal'")
            values = self.db.execute('''SELECT documents.source,documents.identity,documents.metadata,
              snippet(documents_fts,0,'','',' … ',48),sources.value,sources.revision,bm25(documents_fts)
              FROM documents_fts JOIN documents ON documents.rowid=documents_fts.rowid
              JOIN sources ON sources.id=documents.source WHERE '''+' AND '.join(where)+'''
              ORDER BY bm25(documents_fts),documents.source,documents.identity LIMIT ? OFFSET ?''',
                (*parameters, limit+1, offset)).fetchall()
        items = [{'sessionId': sid, 'messageId': identity, **json.loads(metadata), 'snippet': snippet[:1000],
            'session': json.loads(session), 'sourceRevision': json.loads(revision), 'rank': score,
            'reference': {'sessionId': sid, 'messageId': identity, 'sourceRevision': json.loads(revision)}}
            for sid, identity, metadata, snippet, session, revision, score in values[:limit]]
        return {'items': items, 'nextOffset': offset+limit if len(values)>limit else None}

    def message(self, sid, identity, offset=0, limit=4000):
        self._page(offset,limit,4000)
        with self.lock:
            row = self.db.execute('''SELECT text,metadata,sources.revision FROM documents JOIN sources
                ON sources.id=documents.source AND sources.generation=documents.generation WHERE sources.id=? AND identity=? AND sources.available=1''', (sid, identity)).fetchone()
        if row is None:
            raise ValueError('This indexed source is unavailable; refresh the index and search again.')
        text, metadata, revision = row
        return {**json.loads(metadata), 'sessionId': sid, 'sourceRevision': json.loads(revision),
            'text': text[offset:offset+limit], 'offset': offset,
            'nextOffset': offset+limit if offset+limit<len(text) else None}

    def list_memories(self, scopes, offset=0, limit=20):
        self._page(offset,limit,100)
        with self.lock:
            # Scope count is bounded by host policy (task, workspace, global).
            where = '1' if scopes is None else ' OR '.join('(scope=? AND target=?)' for _ in scopes) or '0'
            rows = self.db.execute('SELECT value FROM memories WHERE '+where+' ORDER BY id LIMIT ? OFFSET ?',
                (*[v for pair in (scopes or []) for v in pair], limit+1, offset)).fetchall()
        return {'items': [json.loads(row[0]) for row in rows[:limit]],
            'nextOffset': offset+limit if len(rows)>limit else None}

    def memory(self, identity):
        with self.lock:
            row = self.db.execute('SELECT value FROM memories WHERE id=?', (identity,)).fetchone()
        if row is None:
            raise ValueError('This memory was deleted or is unavailable.')
        return json.loads(row[0])

    def versions(self, identity, *, before=None, limit=50):
        self._page(0,limit,100)
        self.memory(identity)
        with self.lock:
            return [json.loads(row[0]) for row in self.db.execute(
                'SELECT value FROM memory_versions WHERE id=? AND revision<? ORDER BY revision DESC LIMIT ?', (identity,before if before is not None else 2**63-1,limit))]

    def receipt(self, command_id, fingerprint):
        with self.lock:
            row = self.db.execute('SELECT fingerprint,result FROM memory_receipts WHERE id=?', (command_id,)).fetchone()
        if row is None:
            return None
        if row[0] != fingerprint:
            raise ValueError('This command ID was already used with different contents.')
        return {**json.loads(row[1]), 'duplicate': True}

    def mutate(self, action, args, *, command_id, provenance, request_fingerprint=None):
        fingerprint = request_fingerprint or digest([action,args,provenance])
        with self.atomic():
            old = self.db.execute('SELECT fingerprint,result FROM memory_receipts WHERE id=?', (command_id,)).fetchone()
            if old:
                if old[0] != fingerprint:
                    raise ValueError('This command ID was already used with different contents.')
                return {**json.loads(old[1]), 'duplicate': True}
            if action == 'memory.create':
                record = {'id': uuid.uuid4().hex, 'revision': 0, 'scope': args['scope'], 'target': args['target'],
                    'createdAt': time.time(), 'provenance': copy.deepcopy(provenance)}
            else:
                record = self.memory(args['id'])
                if record['revision'] != args['expectedRevision']:
                    raise ValueError('This memory changed. Read its current revision before editing.')
            if action == 'memory.delete':
                self.db.execute('DELETE FROM memories WHERE id=?', (record['id'],))
                self.db.execute('DELETE FROM memory_versions WHERE id=?', (record['id'],))
                result = {'id': record['id'], 'deleted': True, 'revision': record['revision']+1}
            else:
                text = args['text'].strip()
                if not text or self.max_text_characters is not None and len(text)>self.max_text_characters:
                    raise ValueError('Memory text is empty or exceeds the caller-configured size limit.')
                record.update(text=text, updatedAt=time.time(), revision=record['revision']+1,
                    provenance=copy.deepcopy(provenance), source=copy.deepcopy(args.get('source', record.get('source'))))
                for key in ('automationKey', 'automationSourceKey', 'supersedes', 'supersededBy'):
                    if key in args:
                        record[key] = copy.deepcopy(args[key])
                self.db.execute('INSERT OR REPLACE INTO memories VALUES (?,?,?,?,?)',
                    (record['id'],record['scope'],record['target'],record['revision'],json.dumps(record)))
                self.db.execute('INSERT INTO memory_versions VALUES (?,?,?)',
                    (record['id'],record['revision'],json.dumps(record)))
                if self.retain_versions is not None:
                    self.db.execute('DELETE FROM memory_versions WHERE id=? AND revision<=?', (record['id'],record['revision']-self.retain_versions))
                result = {'id': record['id'], 'revision': record['revision'], 'scope': record['scope'], 'target': record['target']}
            # Receipts never retain the deleted note text or prior versions.
            self.db.execute('INSERT INTO memory_receipts VALUES (?,?,?)', (command_id,fingerprint,json.dumps(result)))
            return result
