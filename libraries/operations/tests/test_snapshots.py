import hashlib,json,sqlite3,os
from pathlib import Path
import pytest
from amplifier_operations.snapshots import capture_snapshot,restore_snapshot
from amplifier_operations.quiescence import DurableIntakeFence

CONTEXT={'fenceId':'fence','commandId':'recovery','purpose':'recovery','instanceId':'source-instance','dataScope':'owner'}

def store(path,value='committed WAL evidence'):
    db=sqlite3.connect(path);db.execute('PRAGMA journal_mode=WAL');db.execute('PRAGMA wal_autocheckpoint=0')
    db.execute('CREATE TABLE records(id TEXT PRIMARY KEY,value TEXT)');db.execute('INSERT INTO records VALUES(?,?)',('unknown',value));db.commit();return db

def test_held_wal_capture_restore_preserves_exact_unknown_and_intake(tmp_path):
    source=tmp_path/'source';source.mkdir();db=store(source/'operations.sqlite3')
    fence=DurableIntakeFence(source/'intake.sqlite3');fence.acquire(CONTEXT)
    original={path:path.read_bytes() for path in source.iterdir() if path.is_file() and not path.name.endswith('-shm')}
    native=tmp_path/'native';native.mkdir();(native/'transcript.jsonl').write_bytes(b'original canonical history\n');history=(native/'transcript.jsonl').read_bytes()
    def held():assert fence.fence==CONTEXT
    captured=capture_snapshot({'operations':source/'operations.sqlite3','intake':source/'intake.sqlite3','optional':source/'missing.sqlite3'},tmp_path/'snapshot',assert_held=held)
    assert captured['activationProvided'] is False and captured['stores'][-1]['status']=='missing'
    restored=restore_snapshot(tmp_path/'snapshot',tmp_path/'inactive',manifest_sha256=captured['manifestSha256'],assert_held=held)
    assert restored['activationProvided'] is False
    with sqlite3.connect(tmp_path/'inactive/operations.sqlite3') as target:assert target.execute('SELECT value FROM records').fetchone()==('committed WAL evidence',)
    retained=DurableIntakeFence(tmp_path/'inactive/intake.sqlite3')
    assert retained.fence==CONTEXT;retained.close()
    assert all(path.read_bytes()==raw for path,raw in original.items())
    assert (native/'transcript.jsonl').read_bytes()==history
    fence.close();db.close()

@pytest.mark.parametrize('change',['image','manifest','existing-target'])
def test_reviewed_restore_refuses_tamper_or_overwrite(tmp_path,change):
    source=tmp_path/'source.sqlite3';db=store(source)
    result=capture_snapshot({'store':source},tmp_path/'snapshot',assert_held=lambda:None)
    target=tmp_path/'inactive'
    if change=='image':(tmp_path/'snapshot/store.sqlite3').write_bytes(b'corrupted')
    if change=='manifest':(tmp_path/'snapshot/manifest.json').write_text('{}')
    if change=='existing-target':target.mkdir();(target/'preserved').write_text('existing authority')
    with pytest.raises((ValueError,FileExistsError)):
        restore_snapshot(tmp_path/'snapshot',target,manifest_sha256=result['manifestSha256'],assert_held=lambda:None)
    if change=='existing-target':assert (target/'preserved').read_text()=='existing authority'
    else:assert not target.exists()
    db.close()

def test_census_change_during_capture_refuses_and_preserves_partial(tmp_path):
    first=tmp_path/'a.sqlite3';second=tmp_path/'b.sqlite3';a=store(first);b=store(second)
    changed=False
    def held():
        nonlocal changed
        if not changed and (tmp_path/'snapshot/a.sqlite3').exists():
            b.execute('UPDATE records SET value=?',('concurrent writer',));b.commit();changed=True
    with pytest.raises(ValueError,match='changed'):
        capture_snapshot({'a':first,'b':second},tmp_path/'snapshot',assert_held=held)
    assert not (tmp_path/'snapshot/manifest.json').exists()
    with pytest.raises(FileExistsError):capture_snapshot({'a':first,'b':second},tmp_path/'snapshot',assert_held=lambda:None)
    a.close();b.close()

def test_no_guard_or_link_or_unresolved_journal_can_capture(tmp_path):
    source=tmp_path/'source.sqlite3';db=store(source)
    def lost():raise ValueError('writer guard not held')
    with pytest.raises(ValueError,match='guard'):capture_snapshot({'a':source},tmp_path/'absent',assert_held=lost)
    assert not (tmp_path/'absent').exists()
    link=tmp_path/'linked.sqlite3';link.symlink_to(source)
    with pytest.raises(ValueError,match='symbolic'):capture_snapshot({'a':link},tmp_path/'linked',assert_held=lambda:None)
    Path(str(source)+'-journal').write_text('unresolved original')
    with pytest.raises(ValueError,match='journal'):capture_snapshot({'a':source},tmp_path/'journal',assert_held=lambda:None)
    db.close()

def test_budget_refusal_preserves_original_and_marks_no_complete_snapshot(tmp_path):
    source=tmp_path/'source.sqlite3';db=store(source);before=source.read_bytes()
    with pytest.raises(ValueError,match='budget'):capture_snapshot({'a':source},tmp_path/'partial',assert_held=lambda:None,max_bytes=1024)
    assert source.read_bytes()==before and not (tmp_path/'partial/manifest.json').exists()
    db.close()


def test_sqlite_shared_memory_link_refuses_before_capture(tmp_path):
    source=tmp_path/'source.sqlite3'
    with sqlite3.connect(source) as db:db.execute('CREATE TABLE records(value)')
    elsewhere=tmp_path/'coordination';elsewhere.write_bytes(b'preserved bytes')
    Path(str(source)+'-shm').symlink_to(elsewhere)
    with pytest.raises(ValueError,match='symbolic'):
        capture_snapshot({'store':source},tmp_path/'snapshot',assert_held=lambda:None)
    assert elsewhere.read_bytes()==b'preserved bytes' and not (tmp_path/'snapshot').exists()

def test_ctime_only_change_during_backup_is_not_a_writer(tmp_path):
    source=tmp_path/'source.sqlite3';db=store(source)
    wal=Path(str(source)+'-wal');original=wal.read_bytes();before=wal.stat();changed=False
    def held():
        nonlocal changed
        if not changed and (tmp_path/'snapshot/store.sqlite3').exists():
            wal.chmod(0o600 if before.st_mode&0o777!=0o600 else 0o640);changed=True
    result=capture_snapshot({'store':source},tmp_path/'snapshot',assert_held=held)
    assert changed and result['stores'][0]['status']=='captured'
    assert wal.read_bytes()==original and wal.stat().st_mtime_ns==before.st_mtime_ns
    assert wal.stat().st_ctime_ns!=before.st_ctime_ns
    db.close()

def test_content_change_with_preserved_size_and_mtime_refuses(tmp_path):
    from amplifier_operations.snapshots import revision
    source=tmp_path/'source.sqlite3';db=store(source);wal=Path(str(source)+'-wal')
    before=revision(source,[1024*1024],lambda:None);info=wal.stat();raw=wal.read_bytes()
    wal.write_bytes(raw[:-1]+bytes([raw[-1]^1]));os.utime(wal,ns=(info.st_atime_ns,info.st_mtime_ns))
    assert revision(source,[1024*1024],lambda:None)!=before
    wal.write_bytes(raw);db.close()

def test_source_census_has_aggregate_byte_bound_before_destination(tmp_path):
    first=tmp_path/'a.sqlite3';second=tmp_path/'b.sqlite3';a=store(first);b=store(second)
    one=first.stat().st_size+Path(str(first)+'-wal').stat().st_size
    with pytest.raises(ValueError,match='sources exceed byte budget'):
        capture_snapshot({'a':first,'b':second},tmp_path/'snapshot',assert_held=lambda:None,max_source_bytes=one)
    assert not (tmp_path/'snapshot').exists()
    a.close();b.close()

def test_source_hash_rechecks_guard_for_each_chunk(tmp_path):
    from amplifier_operations.snapshots import revision
    source=tmp_path/'source.sqlite3';source.write_bytes(b'x'*200000);calls=0
    def lost():
        nonlocal calls
        calls+=1
        if calls==3:raise ValueError('writer guard lost')
    with pytest.raises(ValueError,match='guard lost'):revision(source,[1024*1024],lost)
    assert calls==3
