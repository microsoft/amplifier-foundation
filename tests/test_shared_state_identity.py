"""Historical addresses retain the original public ownership protocol."""
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from amplifier_foundation.session import (
    SharedSessionStore, SessionBusyError, SessionTransferFencedError,
)

pytestmark = pytest.mark.skipif(os.name != 'posix', reason='shared writer leases use POSIX flock')


def store_at(tmp_path, monkeypatch):
    monkeypatch.setenv('AMPLIFIER_HOME', str(tmp_path/'native'))
    workspace=tmp_path/'allocation'/'files';workspace.mkdir(parents=True)
    return SharedSessionStore(workspace, 'retained-1', root=tmp_path/'state')


def test_retained_identity_has_same_checkpoint_and_real_process_lock(tmp_path, monkeypatch):
    store=store_at(tmp_path,monkeypatch);identity=store.identity()
    held=store.acquire(app='original-writer')
    held.write([{'role':'user','content':'canonical'}],bundle='original')
    original=store.checkpoint_path.read_bytes();stamp=store.stamp()
    store.workspace.rmdir();store.workspace.parent.rmdir()
    restored=SharedSessionStore.from_identity(json.loads(json.dumps(identity)),root=store.root)
    assert restored.checkpoint_path==store.checkpoint_path
    assert restored.stamp()==stamp and restored.read()==store.read()
    assert not store.workspace.exists()
    with pytest.raises(SessionBusyError):restored.acquire(app='same-process')
    script='''import json,sys
from amplifier_foundation.session import SharedSessionStore,SessionBusyError
store=SharedSessionStore.from_identity(json.loads(sys.argv[1]),root=sys.argv[2])
try:
 held=store.acquire(app="competing-process")
except SessionBusyError:
 sys.exit(23)
else:
 held.release()
'''
    try:
        busy=subprocess.run([sys.executable,'-I','-c',script,json.dumps(identity),str(store.root)],capture_output=True,text=True,timeout=10)
        assert busy.returncode==23,busy.stderr
        assert store.checkpoint_path.read_bytes()==original
    finally:held.release()
    available=subprocess.run([sys.executable,'-I','-c',script,json.dumps(identity),str(store.root)],capture_output=True,text=True,timeout=10)
    assert available.returncode==0,available.stderr
    assert not store.workspace.exists() and store.checkpoint_path.read_bytes()==original
    with pytest.raises(ValueError):SharedSessionStore(store.workspace,store.session_id,root=store.root)
    with pytest.raises(ValueError):SharedSessionStore.list_ids(store.workspace,root=store.root)


def test_retained_transfer_fence_cannot_be_bypassed(tmp_path,monkeypatch):
    store=store_at(tmp_path,monkeypatch);identity=store.identity()
    held=store.acquire(app='source');held.fence_transfer('transfer-1','target-host');held.release()
    path=store.transfer_fence_path;before=path.read_bytes();store.workspace.rmdir()
    restored=SharedSessionStore.from_identity(identity,root=store.root)
    assert restored.transfer_fence_path==path and restored.transfer_fence()==store.transfer_fence()
    with pytest.raises(SessionTransferFencedError):restored.acquire(app='must-not-execute')
    with pytest.raises(Exception,match='exact staged'):restored.acquire_transfer('wrong-transfer',app='wrong')
    transfer=restored.acquire_transfer('transfer-1',app='authenticated-owner')
    committed=transfer.commit_transfer();transfer.release()
    assert committed['phase']=='committed' and before!=path.read_bytes()
    proof=restored.confirm_transfer_commit('transfer-1',app='confirm-original')
    assert proof==committed
    with pytest.raises(SessionTransferFencedError):restored.acquire(app='still-fenced')
    with pytest.raises(Exception):restored.acquire_transfer('transfer-1',app='cannot-clear-committed')
    assert not store.workspace.exists()


def test_identity_is_an_exact_address_not_a_path_alias(tmp_path,monkeypatch):
    store=store_at(tmp_path,monkeypatch);identity=store.identity()
    assert identity=={'version':1,'workspace':str(store.workspace),'sessionId':'retained-1'}
    changed=store.identity();changed['sessionId']='another';assert store.session_id=='retained-1'
    invalid=[{},identity|{'version':True},identity|{'version':2},identity|{'sessionId':'../escape'},identity|{'extra':'no'},identity|{'workspace':'relative'},identity|{'workspace':str(store.workspace)+'/../files'},identity|{'workspace':str(store.workspace)+'/'},identity|{'workspace':'/'+'x'*4097}]
    for value in invalid:
        with pytest.raises(ValueError):SharedSessionStore.from_identity(value,root=store.root)
    store.workspace.rmdir();store.workspace.parent.rmdir()
    other=tmp_path/'foreign';other.mkdir();store.workspace.parent.symlink_to(other,target_is_directory=True)
    with pytest.raises(ValueError,match='canonical'):SharedSessionStore.from_identity(identity,root=store.root)
    assert not store.root.exists()


def test_historical_checkpoint_writes_still_require_live_held_handle(tmp_path,monkeypatch):
    store=store_at(tmp_path,monkeypatch);identity=store.identity();store.workspace.rmdir()
    restored=SharedSessionStore.from_identity(identity,root=store.root);held=restored.acquire(app='historical-owner')
    held.write([{'role':'assistant','content':'preserved'}],bundle='historical')
    assert restored.read()['messages'][0]['content']=='preserved'
    held.release()
    with pytest.raises(Exception):held.delete_checkpoint()
    new=restored.acquire(app='later-historical-owner');new.delete_checkpoint();new.release()
    assert restored.read() is None and not store.workspace.exists()
