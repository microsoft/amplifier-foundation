# Selected-prefix media fork handoff

`fork_session` keeps text-only legacy behavior unchanged. A selected prefix that
contains `metadata["experimental.media.v1"]["refs"]` requires explicit host-owned
adoption and actual checkpoint callbacks:

```python
fork_session(
    parent_dir,
    turn=selected_turn,
    media_adopter=adopt,
    media_checkpoint=acknowledge,
)

def adopt(messages, *, session_id, session_dir):
    # Only selected rows, in their original order. Resolve authorization through
    # the actual source owner. Publish independent target assets before return.
    return target_owned_messages

def acknowledge(messages, *, session_id, session_dir):
    # Runs after the actual canonical target transcript and metadata are written.
    # Verify that exact save; do not save a second transcript or report a commit.
    ...
```

Both callbacks are synchronous. Missing callbacks refuse **before** destination
creation or payload copying. Media forks reserve a new private destination
directory create-only: a failed adoption/save/acknowledgement retains its partial
evidence, never silently overwrites it, deletes it, or retries the tool. Successful
adoption alone is assets-only, not canonical commitment. A checkpoint failure
propagates instead of returning a successful fork.

The host chooses the source and target stores and policy. Reference possession,
a digest or a matching path is not authorization. Preserve original provenance,
creation time and expiry, role, call IDs, ordered positions and provider metadata.
Copy only the authorized selected prefix. Expired nontransferable observations
become truthful unavailable text without reading pixels or renewing their TTL.
Foreign-owner or unsupported references returned by an adopter are refused.
No parser searches strings or nested arbitrary tool dictionaries for references.

`fork_session_in_memory(..., media_adopter=adopt)` provides the same selected-prefix
adoption boundary with `session_dir=None`. It **does not** checkpoint or claim a
durable fork; the receiving host must persist and acknowledge its actual target
canonical rows before model delivery. The callback receives a deep copy so its
ordinary failure cannot alter the parent's in-memory rows.

The initial stored-media fork path is POSIX-only. Windows refuses this feature
until its assets/namespace/checkpoint durability is qualified. Existing text-only
Windows behavior remains unchanged. The caller still owns source/session write
exclusion and origin/policy verification; these callbacks do not establish a lock
against non-cooperating writers. This handoff does not activate a new transcript
layout or migrate historical inline images automatically.