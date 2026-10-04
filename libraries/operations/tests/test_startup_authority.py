from amplifier_operations import OperationJournal
from amplifier_operations.requests import OperationRequests
PROFILES=[('standalone',('operations','operation_events','operation_output')),('combined',('operations','operation_events','operation_output','operation_requests'))]
def make(profile,path):return OperationJournal(path,require_requests=profile=='combined')

import hashlib
import itertools
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import pytest


def retained(path):
    return {suffix: hashlib.sha256(Path(str(path)+suffix).read_bytes()).hexdigest()
            for suffix in ('', '-wal') if Path(str(path)+suffix).exists() and (suffix == '' or Path(str(path)+suffix).stat().st_size > 0)}


def close(store):
    store.db.close()


@pytest.mark.parametrize('profile,tables', PROFILES)
def test_every_missing_authority_subset_refuses_without_recreation(tmp_path, profile, tables):
    for count in range(1, len(tables)+1):
        for missing in itertools.combinations(tables, count):
            path=tmp_path/('-'.join(missing)+'.db')
            store=make(profile,path);close(store)
            with sqlite3.connect(path) as db:
                for table in missing:db.execute('DROP TABLE '+table)
            before=retained(path)
            with pytest.raises(ValueError):make(profile,path)
            assert retained(path)==before
            with sqlite3.connect(path) as db:
                names={r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            assert names.isdisjoint(missing)


@pytest.mark.parametrize('profile,tables', PROFILES)
@pytest.mark.parametrize('suffix', ['-wal','-shm','-journal'])
@pytest.mark.parametrize('kind', ['empty','nonempty','dangling'])
def test_orphan_sidecars_never_initialize(tmp_path, profile, tables, suffix, kind):
    path=tmp_path/'state.db';side=Path(str(path)+suffix)
    if kind=='dangling':side.symlink_to(tmp_path/'absent')
    else:side.write_bytes(b'' if kind=='empty' else b'retained uncertain authority')
    with pytest.raises(ValueError):make(profile,path)
    assert not os.path.lexists(path)
    assert os.path.lexists(side)
    if kind!='dangling':assert side.read_bytes()==(b'' if kind=='empty' else b'retained uncertain authority')


@pytest.mark.parametrize('profile,tables', PROFILES)
def test_crash_wal_schema_loss_preserves_main_and_nonempty_wal(tmp_path, profile, tables):
    for table in tables:
        path=tmp_path/(table+'.db');store=make(profile,path);close(store)
        program="import os,sqlite3;d=sqlite3.connect("+repr(str(path))+");d.execute('PRAGMA journal_mode=WAL');d.execute('PRAGMA wal_autocheckpoint=0');d.execute("+repr('DROP TABLE '+table)+");d.commit();os._exit(0)"
        subprocess.run([sys.executable,'-I','-B','-c',program],check=True)
        assert Path(str(path)+'-wal').stat().st_size>0
        before=retained(path)
        with pytest.raises(ValueError):make(profile,path)
        assert retained(path)==before


@pytest.mark.parametrize('profile,tables', PROFILES)
def test_empty_existing_main_is_not_fresh(tmp_path, profile, tables):
    path=tmp_path/'state.db';path.touch()
    with pytest.raises(ValueError):make(profile,path)
    assert path.read_bytes()==b''


@pytest.mark.parametrize('profile,tables', PROFILES)
def test_healthy_reopen_and_derived_indexes(tmp_path, profile, tables):
    path=tmp_path/'state.db';store=make(profile,path);close(store)
    with sqlite3.connect(path) as db:
        names=[r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='index' AND sql IS NOT NULL")]
        for name in names:db.execute('DROP INDEX '+name)
    store=make(profile,path)
    assert set(tables)<={r[0] for r in store.db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    close(store)


def test_existing_standalone_never_guesses_requests_migration(tmp_path):
    path=tmp_path/'standalone.db';journal=OperationJournal(path);journal.close()
    before=retained(path)
    with pytest.raises(ValueError):OperationJournal(path,require_requests=True)
    assert retained(path)==before
    journal=OperationJournal(path)
    with pytest.raises(ValueError):OperationRequests(journal)
    assert journal.db.execute("SELECT 1 FROM sqlite_master WHERE name='operation_requests'").fetchone() is None
    journal.close()


def test_fresh_optional_attachment_is_consumed_and_same_id_stays_unknown(tmp_path):
    path=tmp_path/'combined.db';journal=OperationJournal(path);requests=OperationRequests(journal)
    args={'requestId':'original','command':'inert'}
    receipt,fresh=requests.begin('session','operations.submit',args,'ui');assert fresh
    requests.finish(receipt,'outcome_unknown',message='original uncertainty')
    journal.close();journal=OperationJournal(path,require_requests=True);requests=OperationRequests(journal)
    original,fresh=requests.begin('session','operations.submit',args,'ui')
    assert not fresh and original['state']=='outcome_unknown' and original['message']=='original uncertainty'
    with journal.db:journal.db.execute('DROP TABLE operation_requests')
    with pytest.raises(ValueError):OperationRequests(journal)
    journal.close()
    before=retained(path)
    with pytest.raises(ValueError):OperationJournal(path,require_requests=True)
    assert retained(path)==before


@pytest.mark.parametrize('profile,tables', PROFILES)
def test_unsupported_table_shape_and_overlong_ddl_refuse_before_writer(tmp_path, profile, tables, monkeypatch):
    path=tmp_path/'unsupported.db';store=make(profile,path);close(store)
    with sqlite3.connect(path) as db:
        db.execute('PRAGMA writable_schema=ON')
        db.execute("UPDATE sqlite_master SET sql=sql||? WHERE name=?",(' '*4097,tables[0]))
        db.execute('PRAGMA schema_version=100')
    before=retained(path);connect=sqlite3.connect;opened=[]
    def traced(*args,**kwargs):
        opened.append(str(args[0]));return connect(*args,**kwargs)
    monkeypatch.setattr(sqlite3,'connect',traced)
    with pytest.raises(ValueError):make(profile,path)
    assert len(opened)==1 and opened[0].endswith('?mode=ro')
    assert retained(path)==before


@pytest.mark.parametrize('profile,tables', PROFILES)
@pytest.mark.parametrize('suffix',['','-wal','-shm','-journal'])
@pytest.mark.parametrize('kind',['fifo','symlink'])
def test_nonregular_main_or_sidecar_refuses_before_sqlite(tmp_path, profile, tables, suffix, kind, monkeypatch):
    path=tmp_path/'authority.db'
    if suffix:
        store=make(profile,path);close(store)
    candidate=Path(str(path)+suffix)
    if candidate.exists():candidate.unlink()
    if kind=='fifo':os.mkfifo(candidate)
    else:
        target=tmp_path/'regular';target.write_bytes(b'retained');candidate.symlink_to(target)
    def forbidden(*args,**kwargs):raise AssertionError('Nonregular authority must be rejected before SQLite')
    monkeypatch.setattr(sqlite3,'connect',forbidden)
    with pytest.raises(ValueError):make(profile,path)
    assert os.path.lexists(candidate)
