import json
import sqlite3
import pytest
from amplifier_recall import RecallStore


def source(identity='one', workspace='/work', **extra):
    return {'id':identity,'title':identity,'workspace':workspace,**extra}


def message(identity='message', text='A cobalt decision.'):
    return {'id':identity,'role':'user','text':text}


def test_scoped_search_avoids_materialized_authority_lists_and_filters_children(tmp_path):
    store=RecallStore(tmp_path/'recall.db')
    try:
        for n in range(200):
            store.replace(source(str(n),'/work' if n<100 else '/else',kind='worker' if n<10 else 'root'),str(n),n,[message(str(n))])
        store.replace(source('internal',kind='internal'),'x','x',[message()])
        sql=[];store.db.set_trace_callback(sql.append)
        first=store.search_scope('cobalt',workspace='/work',limit=20)
        second=store.search_scope('cobalt',workspace='/work',limit=20,cursor=first['nextCursor'])
        assert len(first['items'])==len(second['items'])==20
        assert not ({row['sessionId'] for row in first['items']} & {row['sessionId'] for row in second['items']})
        assert all(row['session']['kind']=='root' and row['session']['workspace']=='/work' for row in first['items']+second['items'])
        assert not any('allowed_sources' in query for query in sql)
        assert store.search_scope('cobalt',session='1')['items']==[]
        assert len(store.search_scope('cobalt',session='1',include_children=True)['items'])==1
        with pytest.raises(ValueError,match='scope or index'):
            store.search_scope('cobalt',workspace='/else',cursor=first['nextCursor'])
        store.set_available('11',False)
        assert not store.search_scope('cobalt',session='11')['items']
        with pytest.raises(ValueError,match='index changed'):
            store.search_scope('cobalt',workspace='/work',cursor=first['nextCursor'])
    finally:store.close()


def test_partial_source_is_invisible_until_verified_complete_and_survives_reopen(tmp_path):
    path=tmp_path/'recall.db';store=RecallStore(path)
    try:
        store.replace(source(),'old','old',[message(text='Original cobalt statement.')])
        token=store.begin_source(source(),'new','new')
        store.append_source(token,[{**message('a','New violet source.'),'authorization':'unverified-native-history'}])
        store.close();store=RecallStore(path)
        assert store.search_scope('cobalt')['items']
        assert not store.search_scope('violet')['items']
        with pytest.raises(ValueError,match='revision changed'):
            store.commit_source(token,expected_revision='different')
        store.append_source(token,[message('b','New second page.')])
        store.commit_source(token,expected_revision='new')
        assert not store.search_scope('cobalt')['items']
        assert len(store.search_scope('new')['items'])==2
        assert store.signature('one')=='new'
        plan=store.db.execute('EXPLAIN QUERY PLAN SELECT text FROM documents WHERE source=? AND generation=? AND identity=?',('one',token,'a')).fetchall()
        assert any('INDEX' in row[-1] for row in plan)
        assert store.message('one','a',limit=3)['nextOffset']==3
        assert store.message('one','a')['authorization']=='unverified-native-history'
    finally:store.close()


def test_competing_ingestion_cannot_replace_newer_committed_source(tmp_path):
    store=RecallStore(tmp_path/'recall.db')
    try:
        a=store.begin_source(source(),'first',1);b=store.begin_source(source(),'second',2)
        store.append_source(a,[message(text='First statement.')]);store.append_source(b,[message(text='Second statement.')])
        store.commit_source(b,expected_revision=2)
        with pytest.raises(ValueError,match='Another ingestion'):
            store.commit_source(a,expected_revision=1)
        store.discard_source(a)
        assert store.search_scope('second')['items'] and not store.search_scope('first')['items']
    finally:store.close()


def test_explicit_retention_policy_and_deletion_receipts_do_not_retain_note_text(tmp_path):
    store=RecallStore(tmp_path/'recall.db',retain_versions=2,max_text_characters=40)
    try:
        record=store.mutate('memory.create',{'scope':'workspace','target':'/work','text':'Private note'},command_id='create',provenance={'origin':'test'})
        for revision in range(1,5):
            store.mutate('memory.update',{'id':record['id'],'expectedRevision':revision,'text':str(revision)},command_id='update'+str(revision),provenance={'origin':'test'})
        assert [row['revision'] for row in store.versions(record['id'])]==[5,4]
        with pytest.raises(ValueError,match='size limit'):
            store.mutate('memory.update',{'id':record['id'],'expectedRevision':5,'text':'x'*41},command_id='oversize',provenance={})
        deleted=store.mutate('memory.delete',{'id':record['id'],'expectedRevision':5},command_id='delete',provenance={})
        assert deleted['deleted'] and 'text' not in deleted
        assert not store.db.execute('SELECT 1 FROM memory_versions WHERE id=?',(record['id'],)).fetchone()
        assert all('Private note' not in row[0] for row in store.db.execute('SELECT result FROM memory_receipts'))
        assert not store.db.execute("SELECT name FROM sqlite_master WHERE name LIKE 'memory_settings' OR name LIKE 'memory_attempts'").fetchall()
    finally:store.close()


def test_import_and_legacy_database_open_do_not_start_a_full_reindex(tmp_path):
    path=tmp_path/'legacy.db';db=sqlite3.connect(path)
    db.executescript('CREATE TABLE sources(id TEXT PRIMARY KEY,signature TEXT,revision TEXT,value TEXT);CREATE VIRTUAL TABLE messages USING fts5(text,session_id UNINDEXED,identity UNINDEXED,metadata UNINDEXED);')
    db.execute('INSERT INTO sources VALUES(?,?,?,?)',('legacy','old','1',json.dumps(source('legacy'))));db.execute('INSERT INTO messages VALUES(?,?,?,?)',('preserved old text','legacy','message','{}'));db.commit();db.close()
    store=RecallStore(path)
    try:
        assert store.source_count()==0 and store.signature('legacy') is None
        assert store.db.execute('SELECT text FROM messages').fetchone()[0]=='preserved old text'
        assert not store.search_scope('preserved')['items']
        assert store.source_page()['items']==[]
    finally:store.close()


import hashlib
import subprocess
import sys
from pathlib import Path

AUTHORITY_LOSS = [('memories',), ('memory_versions',), ('memory_receipts',),
                  ('memories', 'memory_versions'), ('memories', 'memory_receipts'),
                  ('memory_versions', 'memory_receipts'),
                  ('memories', 'memory_versions', 'memory_receipts')]
NOTE = {'scope': 'task', 'target': 'inert-task', 'text': 'Retained explicit note.'}
PROVENANCE = {'origin': 'inert-fixture'}


def authority_hashes(path):
    return {suffix: hashlib.sha256(Path(str(path) + suffix).read_bytes()).hexdigest()
            for suffix in ('', '-wal') if Path(str(path) + suffix).exists()
            and Path(str(path) + suffix).stat().st_size}


@pytest.mark.parametrize('tables', AUTHORITY_LOSS)
def test_missing_memory_authority_refuses_instead_of_replaying_create(tmp_path, tables):
    path = tmp_path / 'recall.sqlite'
    store = RecallStore(path)
    original = store.mutate('memory.create', NOTE, command_id='original', provenance=PROVENANCE)
    store.close()
    store = RecallStore(path)
    assert store.mutate('memory.create', NOTE, command_id='original', provenance=PROVENANCE)['duplicate']
    assert store.memory(original['id'])['revision'] == 1
    assert len(store.list_memories(None)['items']) == 1
    store.close()
    with sqlite3.connect(path) as db:
        for table in tables: db.execute('DROP TABLE ' + table)
    before = authority_hashes(path)
    with pytest.raises(ValueError, match='memory authority schema'):
        RecallStore(path)
    assert authority_hashes(path) == before


def crash_store(path, drop=()):
    program = """import json,os,sys
from amplifier_recall import RecallStore
s=RecallStore(sys.argv[1]);s.mutate('memory.create',json.loads(sys.argv[2]),command_id='original',provenance=json.loads(sys.argv[3]))
with s.db:
    for table in json.loads(sys.argv[4]):s.db.execute('DROP TABLE '+table)
os._exit(0)
"""
    subprocess.run([sys.executable, '-I', '-B', '-c', program, str(path),
                    json.dumps(NOTE), json.dumps(PROVENANCE), json.dumps(drop)], check=True)
    assert Path(str(path) + '-wal').stat().st_size > 0


@pytest.mark.parametrize('tables', AUTHORITY_LOSS)
def test_crash_wal_refusal_preserves_main_and_nonempty_wal(tmp_path, tables):
    path = tmp_path / 'recall.sqlite'
    crash_store(path, tables)
    before = authority_hashes(path)
    assert set(before) == {'', '-wal'}
    for _ in range(2):
        with pytest.raises(ValueError, match='memory authority schema'):
            RecallStore(path)
        assert authority_hashes(path) == before


@pytest.mark.parametrize('suffix', ['-wal', '-shm', '-journal'])
@pytest.mark.parametrize('kind', ['empty', 'nonempty', 'dangling'])
def test_missing_main_with_sidecar_evidence_is_not_new(tmp_path, suffix, kind):
    path = tmp_path / 'recall.sqlite'
    sidecar = Path(str(path) + suffix)
    if kind == 'dangling':sidecar.symlink_to(tmp_path / 'absent-target')
    else:sidecar.write_bytes(b'' if kind == 'empty' else b'uncertain authority')
    with pytest.raises(ValueError, match='retained sidecars'):
        RecallStore(path)
    assert not path.exists()
    if kind == 'dangling':assert sidecar.is_symlink()
    else:assert sidecar.read_bytes() == (b'' if kind == 'empty' else b'uncertain authority')


def test_empty_existing_store_is_not_legacy_or_new(tmp_path):
    path = tmp_path / 'recall.sqlite'
    path.touch()
    with pytest.raises(ValueError, match='memory authority schema'):
        RecallStore(path)
    assert path.read_bytes() == b''


def test_dangling_main_is_not_initialized(tmp_path):
    path = tmp_path / 'recall.sqlite'
    target = tmp_path / 'absent-target'
    path.symlink_to(target)
    with pytest.raises(sqlite3.OperationalError):RecallStore(path)
    assert path.is_symlink() and not target.exists()


def test_missing_main_preserves_real_crash_wal(tmp_path):
    path = tmp_path / 'recall.sqlite'
    crash_store(path)
    path.unlink()
    before = authority_hashes(path)
    with pytest.raises(ValueError, match='retained sidecars'):RecallStore(path)
    assert not path.exists() and authority_hashes(path) == before


def test_healthy_crash_wal_preserves_note_versions_and_duplicate_receipt(tmp_path):
    path = tmp_path / 'recall.sqlite'
    crash_store(path)
    store = RecallStore(path)
    try:
        result = store.mutate('memory.create', NOTE, command_id='original', provenance=PROVENANCE)
        assert result['duplicate'] and len(store.list_memories(None)['items']) == 1
        assert [v['revision'] for v in store.versions(result['id'])] == [1]
    finally:store.close()


def test_derived_index_rebuild_preserves_memory_authority(tmp_path):
    path = tmp_path / 'recall.sqlite'
    store = RecallStore(path)
    original = store.mutate('memory.create', NOTE, command_id='original', provenance=PROVENANCE)
    store.close()
    with sqlite3.connect(path) as db:
        for table in ('documents_fts', 'documents', 'messages', 'sources', 'staged_sources', 'recall_meta'):
            db.execute('DROP TABLE ' + table)
    store = RecallStore(path)
    try:
        assert store.mutate('memory.create', NOTE, command_id='original', provenance=PROVENANCE)['duplicate']
        assert store.memory(original['id'])['text'] == NOTE['text']
        assert len(store.versions(original['id'])) == 1
        assert store.source_count() == 0 and not store.search_scope('cobalt')['items']
        store.replace(source(), 'sig', 1, [message()])
        assert len(store.search_scope('cobalt')['items']) == 1
    finally:store.close()


def test_derived_staging_column_migration_remains_supported(tmp_path):
    path = tmp_path / 'recall.sqlite'
    store = RecallStore(path);store.close()
    with sqlite3.connect(path) as db:
        db.execute('DROP TABLE staged_sources')
        db.execute('CREATE TABLE staged_sources(token TEXT PRIMARY KEY,source TEXT NOT NULL,signature TEXT,revision TEXT,metadata TEXT,created REAL NOT NULL)')
    store = RecallStore(path)
    try:
        assert 'base_generation' in {r[1] for r in store.db.execute('PRAGMA table_info(staged_sources)')}
        store.replace(source(), 'sig', 1, [message()])
        assert len(store.search_scope('cobalt')['items']) == 1
    finally:store.close()


def test_search_only_legacy_with_modern_marker_is_not_authority_free(tmp_path):
    path = tmp_path / 'recall.sqlite'
    with sqlite3.connect(path) as db:
        db.executescript('CREATE TABLE sources(id TEXT PRIMARY KEY,signature TEXT,revision TEXT,value TEXT);CREATE VIRTUAL TABLE messages USING fts5(text,session_id UNINDEXED,identity UNINDEXED,metadata UNINDEXED);CREATE TABLE recall_meta(key TEXT PRIMARY KEY,value INTEGER NOT NULL);')
    before = authority_hashes(path)
    with pytest.raises(ValueError, match='memory authority schema'):RecallStore(path)
    assert authority_hashes(path) == before


def test_refusal_is_readonly_and_closes_reader(tmp_path, monkeypatch):
    path = tmp_path / 'recall.sqlite'
    store = RecallStore(path);store.close()
    with sqlite3.connect(path) as db:db.execute('DROP TABLE memory_receipts')
    connect = sqlite3.connect
    connections = []
    def tracked(database, **kwargs):
        assert str(database).endswith('?mode=ro')
        connection = connect(database, **kwargs);connections.append(connection);return connection
    monkeypatch.setattr(sqlite3, 'connect', tracked)
    with pytest.raises(ValueError, match='memory authority schema'):RecallStore(path)
    assert len(connections) == 1
    with pytest.raises(sqlite3.ProgrammingError, match='closed'):connections[0].execute('SELECT 1')


def test_initialization_failure_closes_writer(tmp_path, monkeypatch):
    path = tmp_path / 'recall.sqlite'
    connect = sqlite3.connect
    connections = []
    def tracked(database, **kwargs):
        connection = connect(database, **kwargs);connections.append(connection);return connection
    def refuse_chmod(self, mode):raise PermissionError('inert startup refusal')
    monkeypatch.setattr(sqlite3, 'connect', tracked);monkeypatch.setattr(Path, 'chmod', refuse_chmod)
    with pytest.raises(PermissionError):RecallStore(path)
    assert path.exists() and len(connections) == 1
    with pytest.raises(sqlite3.ProgrammingError, match='closed'):connections[0].execute('SELECT 1')


@pytest.mark.parametrize('replacement', [
    'CREATE TABLE memory_receipts(id TEXT PRIMARY KEY,fingerprint TEXT,wrong TEXT)',
    'CREATE VIEW memory_receipts AS SELECT NULL AS id,NULL AS fingerprint,NULL AS result',
])
def test_changed_memory_authority_shape_refuses_without_write(tmp_path, replacement):
    path = tmp_path / 'recall.sqlite'
    store = RecallStore(path);store.close()
    with sqlite3.connect(path) as db:
        db.execute('DROP TABLE memory_receipts');db.execute(replacement)
    before = authority_hashes(path)
    with pytest.raises(ValueError, match='memory authority schema'):RecallStore(path)
    assert authority_hashes(path) == before


@pytest.mark.parametrize('schema', [
    'CREATE TABLE sources(id TEXT PRIMARY KEY,signature TEXT,revision TEXT,value TEXT)',
    'CREATE TABLE sources(id TEXT PRIMARY KEY,signature TEXT,revision TEXT,value TEXT,generation TEXT);CREATE VIRTUAL TABLE messages USING fts5(text,session_id UNINDEXED,identity UNINDEXED,metadata UNINDEXED)',
])
def test_incomplete_or_modernized_search_only_profile_is_not_legacy(tmp_path, schema):
    path = tmp_path / 'recall.sqlite'
    with sqlite3.connect(path) as db:db.executescript(schema)
    before = authority_hashes(path)
    with pytest.raises(ValueError, match='memory authority schema'):RecallStore(path)
    assert authority_hashes(path) == before
