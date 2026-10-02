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
