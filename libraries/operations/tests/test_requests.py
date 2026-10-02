import pytest
from amplifier_operations import OperationJournal
from amplifier_operations.requests import OperationRequests

def test_unknown_admission_recovers_without_replay_and_conflicting_actor_rejected(tmp_path):
    path=tmp_path/'ops.sqlite3';journal=OperationJournal(path);requests=OperationRequests(journal)
    args={'requestId':'stable','command':'fixture'}
    receipt,fresh=requests.begin('session','operations.submit',args,'ui');assert fresh
    journal.close();journal=OperationJournal(path);requests=OperationRequests(journal)
    recovered,fresh=requests.begin('session','operations.submit',args,'ui')
    assert not fresh and recovered['state']=='outcome_unknown'
    with pytest.raises(ValueError,match='different'):requests.begin('session','operations.submit',args,'agent')
    with pytest.raises(ValueError,match='not found'):requests.read('different','stable')
    journal.close()
