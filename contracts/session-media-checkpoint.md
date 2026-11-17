# Same-session image checkpoint contract

The Foundation image pipeline supports fresh root-session PNG/JPEG observations
and reopening that same session using the existing canonical `transcript.jsonl`.
It does not select or enable a host graph. The host remains responsible for real
tool-call identity, policy, original expiry and the final effective posthook result.

## Final-result and persistence order

1. Stage original bytes with `SessionMediaStore.ingest_sync` (or async `ingest`),
   using `image-bytes` or an actual Core `ToolResult` with `core-tool-result`.
   Original PNG/JPEG bytes are validated, not recompressed or resized.
2. Execute the actual participating Core `tool:post` chain through the installed
   `MediaHookBridge`. Only its complete final effective result can supply refs.
   Null, removed output, redaction, denial or an incomplete witness must not fall
   back to the original staged result. Do not rerun the tool.
3. Publish only the surviving final refs, or drop the staged observation. Asset
   publication is not canonical durability.
4. Save the effective filtered/sanitized canonical rows using the host's existing
   save. The reserved `experimental.media.v1` carrier contains only `refs`; the
   sanitizer preserves exact JSON data, including `expires_at: null`.
5. While holding actual session writer ownership, acknowledge the settled primary:

```python
from amplifier_foundation.session.checkpoint import (
    acknowledge_legacy_checkpoint,
    verified_checkpoint_refs,
)

# Construct the store with checkpoint_verifier=verified_checkpoint_refs.
receipt = acknowledge_legacy_checkpoint(
    session_dir, session_id=session_id, expected_messages=actual_saved_rows,
)
media_store.acknowledge_checkpoint(receipt)
```

The compatibility name `verified_media_checkpoint_refs` is also legacy-only.
It never loads another receipt format. A serialized kind string cannot select
different durability or reconstruct receipt authority.

## Source binding and reload

The receipt verifies the exact primary inode/content and canonical rows after
file and directory fsync. It refuses pending append, backup recovery, symlink or
parent traversal, changed source, ambiguous JSON and altered/forged receipts.
Iterators must be exhausted: a late source check can refuse after yielding a ref.
The store exhausts verification before binding any committed acceptance.

Only scalar `receipt.verify()` facts may be journaled; private paths, callbacks,
seals and live receipts are not durable authority. Reopening the same media store
requires a fresh ACK of the actual primary before model access. Current source
policy and original expiry remain separately checked.

`MediaPreparedProvider` binds the exact private count/send object and live
provider/model/options to one request lease. The host must abandon unused requests
with `release_media_request` and close/aclose the wrapper on teardown. Hydrated
pixels are private request data, never canonical history or diagnostic payloads.

## Deliberate limits

Legacy ACK and each source revalidation scan history: O(history), with one decoded
row resident, but no bound within one huge JSON row. Repeated refs and provider
checks can repeat that work. This is not a bounded-append implementation or a
latency SLA. Durability is POSIX-only; the participating positive bridge ABI is
exactly Core 2.0.2 and assumes a cooperative, explicitly owned host graph.

No transcript-v2, conversion, migration, activation, child forwarding, fork,
adoption or package-transfer API is added by this extraction. Indexed provider
support facts use their own v2 schema; that is not a transcript layout. Existing
text-only/unqualified routes must remain honest about unavailable images.