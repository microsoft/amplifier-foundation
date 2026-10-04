import json
import pytest
from amplifier_operations.quiescence import DurableIntakeFence
C=dict(fenceId='update-fence',commandId='update-command',purpose='distribution-update',instanceId='original-launch',dataScope='owned-data')
def proof(**changes):return dict(kind='distribution-admission-abort',verified=True,purpose='distribution-update',receiptId='authenticated-abort',**{k:C[k] for k in ('fenceId','commandId','instanceId','dataScope')},**changes)
def abort(g,**changes):return g.abort_admission({**C,'proof':proof(),**changes},owner_id='fixture-owner')
def test_acquired_commit_lost_reply_restart_has_exact_abort_receipt(tmp_path):
 p=tmp_path/'intake.sqlite';g=DurableIntakeFence(p);g.acquire(C);g.close()
 g=DurableIntakeFence(p);r=abort(g);assert r['status']=='released' and g.fence is None
 assert set(r)=={'commandId','fenceId','instanceId','dataScope','ownerId','status','receiptId'}
 g.close();g=DurableIntakeFence(p)
 assert abort(g)==r and g.admission_abort_receipt(C,owner_id='fixture-owner')==r
 with pytest.raises(ValueError):g.acquire(C)
 with pytest.raises(ValueError):g.abort_admission({**C,'proof':{**proof(),'receiptId':'changed'}},owner_id='fixture-owner')
 with pytest.raises(ValueError):g.abort_admission({**C,'proof':proof()},owner_id='other')
 g.close()
def test_busy_refusal_is_durable_not_acquired_and_cannot_later_acquire(tmp_path):
 p=tmp_path/'intake.sqlite';g=DurableIntakeFence(p);g.calls=1;r=g.acquire(C);g.calls=0
 assert g.acquire(C)==r and g.fence is None;g.close();g=DurableIntakeFence(p)
 assert g.acquire(C)==r and abort(g)['status']=='not-acquired';g.close()
def test_missing_or_legacy_held_evidence_never_means_not_acquired(tmp_path):
 g=DurableIntakeFence(tmp_path/'intake.sqlite')
 with pytest.raises(ValueError,match='original'):abort(g)
 g.db.execute('INSERT INTO fence VALUES(1,?)',(json.dumps(C),));g.db.commit();g.close()
 g=DurableIntakeFence(tmp_path/'intake.sqlite')
 with pytest.raises(ValueError,match='original'):abort(g)
 assert g.fence==C;g.close()
@pytest.mark.parametrize('change',[{'kind':'service-lifecycle'},{'verified':1},{'purpose':'recovery'},{'fenceId':'foreign'},{'commandId':'foreign'},{'instanceId':'replacement'},{'dataScope':'foreign'},{'outcome':'unchanged'},{'receiptId':''}])
def test_generic_changed_or_misbound_proof_refuses(tmp_path,change):
 g=DurableIntakeFence(tmp_path/'intake.sqlite');g.acquire(C)
 with pytest.raises(ValueError):g.abort_admission({**C,'proof':{**proof(),**change}},owner_id='fixture-owner')
 assert g.fence==C;g.close()
@pytest.mark.parametrize('counter',['calls','background','pending'])
def test_active_effects_block_without_settlement(tmp_path,counter):
 g=DurableIntakeFence(tmp_path/'intake.sqlite');g.acquire(C)
 args={}
 if counter=='pending':args['pending']=1
 else:setattr(g,counter,1)
 with pytest.raises(ValueError,match='in flight'):g.abort_admission({**C,'proof':proof()},owner_id='fixture-owner',**args)
 assert g.fence==C and g.admission_abort_receipt(C,owner_id='fixture-owner') is None
 g.close()
def test_pre_effect_rollback_receipt_can_settle_but_running_release_cannot(tmp_path):
 g=DurableIntakeFence(tmp_path/'before.sqlite');g.acquire(C)
 g.release({**C,'outcome':'unchanged','proof':{'kind':'admission-refused'}})
 assert abort(g)['status']=='released';g.close()
 g=DurableIntakeFence(tmp_path/'after.sqlite');g.acquire(C)
 generic={**{k:C[k] for k in ('commandId','fenceId','instanceId','dataScope')},'outcome':'unchanged','verified':True,'receiptId':'generic-release'}
 g.release({**C,'outcome':'unchanged','proof':generic})
 with pytest.raises(ValueError,match='pre-effect'):abort(g)
 g.close()
def test_original_abort_retry_never_clears_a_new_fence(tmp_path):
 g=DurableIntakeFence(tmp_path/'intake.sqlite');g.acquire(C);r=abort(g)
 new={**C,'fenceId':'new-fence','commandId':'new-command'};g.acquire(new)
 assert abort(g)==r and g.fence==new;g.close()
def test_refusal_cannot_release_a_different_current_fence(tmp_path):
 g=DurableIntakeFence(tmp_path/'intake.sqlite');g.acquire(C,pending=1)
 new={**C,'fenceId':'new-fence','commandId':'new-command'};g.acquire(new)
 with pytest.raises(ValueError,match='Another'):abort(g)
 assert g.fence==new;g.close()


def test_original_custom_refusal_reason_is_durable_and_detached(tmp_path):
    gate = DurableIntakeFence(tmp_path / 'intake.sqlite3')
    try:
        context = dict(fenceId='refusal', commandId='command', purpose='distribution-update', instanceId='launch', dataScope='scope')
        first = gate.acquire(context, pending=1, refusal_reason='Local listeners are still serving')
        first['reason'] = 'caller mutation'
        assert gate.acquire(context)['reason'] == 'Local listeners are still serving'
    finally:
        gate.close()
