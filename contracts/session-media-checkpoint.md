# Legacy canonical media checkpoint acknowledgements

Forward media does not require enabling the versioned transcript writer.
After the actual settled canonical save, while holding real session writer
ownership, the host calls:

```python
from amplifier_foundation.session.checkpoint import (
    acknowledge_legacy_checkpoint,
    acknowledge_candidate_checkpoint,
    verified_checkpoint_refs,
)

receipt = acknowledge_legacy_checkpoint(
    session_dir, session_id=session_id, expected_messages=actual_saved_rows,
)
media_store.acknowledge_checkpoint(receipt)
```

Configure the media store with `checkpoint_verifier=verified_checkpoint_refs`.
The expected rows are the final effective, filtered/sanitized rows from the
existing save, not pre-hook results or an earlier autosave. Do not save a second
body to manufacture an acknowledgement. A failure blocks dependent model count
and send; it never repeats the tool.

The receipt verifies exact primary identity, bytes/hash and canonical reachability
after file and directory fsync. It refuses pending append, backup recovery,
symlink/traversal, changed source and invalid rows. The in-memory attestation binds
all identity/count/hash fields; a dictionary/report or altered copy is not commit
authority. Only `receipt.verify()` scalar proof may be journaled. Live receipts,
private callbacks and paths/seals are never serialized. Resume obtains a fresh
receipt from the actual saved canonical source.

Ordinary legacy acknowledgement is O(history), with one decoded message resident.
It is **not bounded within a huge individual JSON record** and is not the future
warm bounded-append implementation.

## Historical huge-record candidate

The format-owner adapter has a separate bounded checkpoint path:

```python
receipt = acknowledge_candidate_checkpoint(
    operation_dir, session_dir,
    session_id=session_id,
    asset_reader=authorized_owner_asset_reader,
)
media_store.acknowledge_checkpoint(receipt)
```

The candidate must already be installed under the caller's actual writer/source/
recovery fence. This function neither installs a candidate nor performs a second
save. It uses the existing canonical conversion owner's
`iter_verified_candidate_refs` to verify saved candidate/manifest/plan/original
hashes, actual primary hash/size, canonical record/ref/slot order and authorized
binary closure. All reference enumeration must be **fully exhausted** before
binding; late source/manifest races raise. The store spools and exhausts rather
than committing references incrementally as they are yielded.

The small `operation_dir/verified.json` supplies count/hash/size data only after
owner verification; it is not independent authority. Ref enumeration reuses the
bounded owner parser, so huge non-image owner strings need not materialize. Pixel
availability/retention policy remains distinct from canonical metadata reachability:
expired accepted observations retain their provenance without renewing consent or
TTL. The asset reader must use the actual owner-authorized inspection path, not
model delivery, arbitrary path lookup or a digest-only grant.

Both paths are POSIX-only pending native qualification. No Windows durability,
new transcript layout, migration, deletion or exclusion of non-cooperating writers
is claimed by an acknowledgement.