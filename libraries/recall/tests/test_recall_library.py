"""Retained standalone contract cases; host cases remain distribution acceptance."""
import json
from pathlib import Path
import pytest
from amplifier_recall import RecallStore


def test_fts_ranks_entire_5000_conversation_library_and_isolates_scopes(tmp_path):
    store=RecallStore(tmp_path/'recall.db')
    try:
        for n in range(5000):
            store.replace({'id':str(n),'title':f'Chat {n}','workspace':'a' if n%2 else 'b'},str(n),n,
                [{'id':f'm{n}','role':'assistant','text':'The old decision was cobalt.' if n==4999 else 'An unrelated note.'}])
        found=store.search('old cobalt',{str(n) for n in range(5000)})
        assert len(found['items'])==1 and found['items'][0]['sessionId']=='4999'
        assert found['items'][0]['reference']['sourceRevision']==4999
        assert not store.search('cobalt',{str(n) for n in range(0,5000,2)})['items']
        assert store.search('" OR *',{str(n) for n in range(5000)})['items']==[]
        assert len(store.search('unrelated',{str(n) for n in range(5000)},limit=20)['items'])==20
        store.prune({'0'})
        assert not store.search('cobalt',{'4999'})['items']
    finally:store.close()
