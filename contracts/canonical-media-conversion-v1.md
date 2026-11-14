# Canonical historical media conversion v1

Status: additive, opt-in **candidate-only** library. This is not a live migration,
an enabled transcript writer, or a replacement for application operation policy.
The frozen image-storage contract takes precedence over this implementation
contract. No Core, application or vendor dependency is introduced.

## Ownership and explicit source selection

`amplifier_foundation.session.media_conversion` owns only canonical v1 JSONL
messages with top-level nonempty string `role` and `content`. It accepts static
PNG/JPEG Core ImageBlocks at `message.content[index]`, with exactly:

```json
{"type":"image","source":{"type":"base64","media_type":"image/png","data":"..."}}
```

The optional Core `visibility` field is retained. The actual F1 media store
verifies raster bytes, dimensions, selected host limits and original observation
admission; the converter independently verifies strict base64 spelling and
SHA-256/length over the original encoded file bytes. It never re-encodes pixels.

Unknown images, URL sources, animated formats, image extensions, nested
ToolResultBlocks, unknown media-namespace shapes, malformed/duplicate inspected
members, or unknown transcript versions are refused. Source and backups stay
intact. There is no recursive JSON-text promotion, discovery of screenshot marker
paths, or parsing of provider continuation/reasoning/signature strings. Other
owners' JSON-in-string formats require their own adapters. Opaque unknown fields
remain opaque; a successful candidate means closure only for this explicitly
qualified canonical image/ref surface, **not** that arbitrary opaque provider
fields contain no image-looking bytes.

V2 layout/writer activation is independently gated. This module does not select
that layout, write role-less envelopes, or edit existing history/repair/fork
entrypoints. It does not read events.jsonl, crawl a root, change retention policy,
pause a live app, or delete originals, assets, staging, backups or recovery holds.

## Entry points

* `preview(source, owner=..., session_id=..., fence=..., backups=(...), selected_records=None)`
  is read-only. Paths must be absolute, regular, non-symlink paths. The explicit
  backup inventory is preserved, not guessed from names. It inventories file
  identity/hash, selected image/ref counts and record count.
* `await build_candidate(plan, new_private_directory, fence=..., capability=...,
  origin_for=..., asset_reader=..., cancelled=..., export_assets=False)` writes
  only the new operation directory and media-store staging/publication requested
  through the injected owner. An existing operation directory is never replayed.
* `reconcile(directory, installed=None)` verifies saved candidate, manifest, plan
  and preserved originals. It never repeats ingest/publish/replace. Missing
  verification means interrupted, not resumable permission to repeat an unknown
  effect. Matching installed bytes are explicitly **checkpoint unconfirmed**.
* `verify_export(private_directory / "portable")` checks portable candidate/manifest/reference and
  independent binary asset closure. Integrity is not authentication or authority.
* `await transfer_prefix(plan, new_directory, source_store=..., target_store=...,
  target_session_id=..., asset_reader=..., fence=...)` supports import/fork only
  through the actual media owner's accepted-commit `adopt` seam. Inline images
  must first be externalized. It changes only the owning `session_id`, refusing
  changes to every other ref field including original provenance/expiry.
* `adopt_selected_media(messages, source_store=..., target_store=...,
  target_session_id=..., now=None)` is the synchronous closure callback for
  existing public fork callers. The caller filters the prefix first and writes
  only after the whole returned list succeeds. It uses F1 `adopt_sync`; failures
  return no partially transformed snapshot. Original messages are not mutated.
  Expiry-neutral identity uses
  `source_store.validate_ref(ref, committed=True, check_expiry=False)`, never a
  pixel read or new retention grant. This already-materialized list API does
  not claim bounded huge-record memory.
* `iter_verified_candidate_refs(operation_dir, installed=..., session_id=...,
  asset_reader=...)` is the **bounded installed-body checkpoint enumeration**
  seam for the owning checkpoint module. It verifies saved evidence, actual
  installed primary exact hash/bytes, bounded record/ref/slot/binary closure,
  yields one small ref at a time and rechecks installed identity/hash and saved
  manifest after exhaustion. The checkpoint owner must fully exhaust before
  binding a receipt. This helper is not an ACK/receipt constructor or authority,
  does not use ordinary whole-record checkpoint readers and never enables T2.
  Yielded refs come from that **same canonical parsing/validation pass**, never a
  second SQLite query. Only the generated plain closure table/schema objects
  qualify; unexpected objects, views, triggers or virtual/dynamic replacements
  refuse, even if someone recomputes an integrity hash over a forged manifest.

`selected_records` is a prefix selection, including zero. Selection precedes
asset enumeration/adoption; suffix images are not included in **portable
delivery** or adopted/granted. Private recovery evidence intentionally retains
the complete original source and explicitly inventoried backups. Never deliver
that private operation directory as an export.
Preview validates the source structure as a whole, but does not decode/copy a
suffix image. Canonical call/result identities, status, usage, arbitrary metadata,
provider state, role and order are copied from original spans, not reconstructed.
The final selected record terminator is normalized to one LF; other nontarget
spans are copied unchanged, including opaque huge strings.

## Caller-owned fence and apply

The required `fence(owner)` context manager must hold the application's **real
exclusive writer/execution exclusion** throughout preview/build. An advisory
filesystem lock or a nonempty owner string is not proof of that exclusion.
Applications must skip active/protected/unknown ownership sources unless they
actually own/quiesce the writer; a writer retaining an open descriptor must not
continue after an out-of-band replacement. File identity/hash rechecks are race
detection, not a substitute for exclusion.

Source device/inode/size/mtime/ctime plus exact SHA-256 are checked before and
after preparation; backups are independently bound. Any mismatch refuses a
verified candidate. Preview is not an apply grant.

Caller apply must independently:

1. retain operation, source/backup identities and approved source selection;
2. own the writer fence, honor Stop/cancellation/recovery/retention policy, and
   recheck exact source/companion identity and candidate/ref/binary closure;
3. journal the exact intended replacement under its existing operation identity;
4. publish durable assets before the canonical replacement/checkpoint;
5. use its qualified native atomic/durable replacement and settled checkpoint
   machinery; retain the original and backups under their independent policy;
6. reconcile unknown replacement/checkpoint outcomes by saved exact hashes and
   the authoritative owner receipt, never by replaying ingest, tools or replace;
7. acknowledge F1 reachability only from the exact canonical checkpoint verifier.

This library supplies **no apply function** and never claims committed
reachability, reclaimed space or successful resume from an assets-only receipt.
POSIX file/directory fsync is exercised; native Windows durability is not claimed.
The preview/build entrypoints explicitly refuse non-POSIX platforms; corresponding
tests skip there rather than claiming native parity.

## F1 seam and canonical slots

The narrow injected capability uses:

```python
await capability.ingest(
    {"stream": binary_file, "mime": "image/png"},
    source_format="image-bytes", origin=original_owner_receipt, policy=None,
)
await capability.publish([ref], origin=original_owner_receipt, disposition="retain")
```

The origin callback receives canonical record/content indices and must return a
real host-admitted migration receipt with the original creation and expiry.
Neither indices nor this callback create permission. If original screenshot
policy/identity cannot be established, refuse through the owner: do not substitute
`now`, a fresh TTL, or durable expiry `null`.

The F1 host-only inspection callback is equivalent to
`store.resolve_ref(ref, committed=False)`, returning a context-managed binary
handle and validating the owner record. Ordinary model `cap.resolve` requires
committed refs and is not used to bypass that gate here.

With the actual host store (candidate
`bbfd427670b6c43b8c4f018fb6ebf8f73c3ef3fd`), canonical per-record image groups use
`store.ingest_group_sync(binary_sources_iterable, origin=..., policy=None)`:
images are decoded to private disk spools, grouped in disk SQLite by the complete
unchanged original provenance, and admitted as the whole ordered true-origin set.
Only ref metadata bounded by host `max_images` is retained in memory. The
complete set is published together; per-image fabricated original IDs are never
introduced. Spool bytes are private operation work/recovery, never portable
delivery. A minimal injected capability without the host group seam supports
independently attested single-image origins only; unsupported grouped inputs
must refuse, not be labeled fully converted.

Each converted inline image becomes exactly the F1 Core text slot
`[Session image <sha256>]` at the same content index. Its ordered ref is inserted
into `message.metadata["experimental.media.v1"]["refs"]`. F1 recognizes these
slots only with an independently validated owned namespace; it consumes
same-digest observations in order. Multiple equal-byte observations retain
distinct original provenance/expiry.

Existing metadata object bytes survive except the explicit namespace insertion;
`metadata:null` may become an object when adding media, and other scalar metadata
is refused. Existing refs must be fully owner-validated through the reader;
foreign refs require explicit adoption. Mixed existing refs and inline images in
one message, or an existing exact slot colliding with an inline conversion, are
refused pending owner-reviewed ordering rather than guessed. Plain JSON-looking
text or a plain slot-looking string without a namespace is never promoted.
The streaming historical adapter requires explicit slot arrays for existing refs
and refuses nonempty refs on legacy string content. The synchronous existing-fork
helper separately retains F1's known legacy string-content convention (refs
project at the same original role position), without inspecting arbitrary text.
Its expired committed refs become explicit unavailable text at the original
array slot, or a same-message suffix for that known legacy string convention,
and are removed from target reachability. Source committed-origin validation is
required before even this no-pixel outcome; expiration never enables a copy or
new screenshot TTL.

F1 host adoption is `await target_store.adopt([ref], source_store)` returning a
one-element ref list. It verifies source accepted+committed state and copies
immutable bytes before returning target published refs; this is **not** a target
checkpoint. A portable export cannot itself instantiate that trusted source
owner. Applications importing an archive must authenticate/authorize its
provenance and provide a qualified source owner; this library intentionally has
no import-by-hash/path or cross-session lookup grant.

## Bounded work and recovery evidence

The binary scanner uses 64 KiB reads and validates/decodes JSON string escapes,
UTF-8 and surrogate pairs incrementally. It never uses `readline()` or
`json.loads()` for a record, huge string, huge image or arbitrary text. Huge
opaque fields are copied by offsets. Strict base64 is decoded in bounded
quartets to a private temporary file. Raster verification remains F1's bounded
policy responsibility.

Limits are adapter schema bounds, not corpus/image quotas: depth 128, 256 members
per **inspected** object, 4 KiB inspected keys, 128-byte numeric primitives,
16 KiB inspected refs/edits. Unsupported limits are explicit refusals; arbitrary
large opaque strings/arrays and arbitrary record counts remain streamable.
Canonical JSONL permits CRLF and a missing last LF, but not blank lines, leading
record whitespace, interior LF/pretty-printed records or adjacent JSON objects.

Patches, per-observation refs/origins and ordered closure are held in disk SQLite
with a 512 KiB cache and file-backed temporary sort. No corpus-sized Python
list/set or per-message ref list is accumulated. Candidate output is compared
against a second streaming exact allowed-span hash, then independently reparsed
for record/ref/slot order and binary closure.

Each operation contains:

* exact source/backup plan with source hashes;
* `original-0`, `original-1`, ... verified durable recovery copies;
* append-only fsynced `journal.jsonl`, including intent **before** each uncertain
  ingest, publish and adoption; records contain refs/origins, never image bytes;
* `manifest.sqlite` with bounded rows for edits and all staged/closure refs;
* durable `candidate.jsonl`;
* `verified.json` binding candidate, manifest and plan hashes after readback.

With `export_assets=True`, only the separate `portable/` child is eligible for
delivery: selected `candidate.jsonl`, selected-only `manifest.sqlite` closure,
deduplicated `assets/<sha256>`, and a small `export.json` hash receipt. It contains
no source plan/path, recovery original, backup, mutation journal or unselected
record/ref. `verify_export` rejects a private operation directory or unexpected
files/assets. It verifies each portable asset independently of the source media
store. This separation is a privacy boundary, not merely an output naming rule.

The journal/manifest enumerate staged and published-but-uncommitted effects.
Cancellation/failure can leave those assets and a partial operation; this is a
safe leak, not rollback or a committed message. Unknown acknowledgement requires
the real media owner's saved origin/publication record to reconcile. This module
does not remove shared deduplicated bytes, auto-retry unknown effects, or resume a
partial mutation under a new identity. Cancellation is checked at record/image,
post-staging and operation-safe checkpoints, not promised interruptible inside
every OS fsync.

Source inline bytes, decoded bytes and candidate logical sizes are distinct
values. Recovery copies can outweigh logical savings. No figure here means
allocated blocks were reclaimed; actual storage accounting/cleanup is a separate
application-owned operation.
`Candidate.inline_removed_bytes` (also in `verified.json`) is the sum of exact
original replaced **image-block spans**, including their JSON wrapper/base64
spelling. It excludes metadata:null or other edits, is not net transcript delta,
and is not decoded asset bytes or allocated savings.

## Qualification

`tests/test_media_conversion*.py` use isolated synthetic sources only: strict
decode, metadata/provider/call/status/usage preservation, original backups,
unknown/malformed/refused formats, source races, cancellation and lost publish
acknowledgements, artifact corruption, portable closure and prefix adoption with
unchanged original expiry. The huge-record fixture contains a 64 MiB opaque
string and a roughly 66 MiB base64 image in one record, using a disk media fixture.
It asserts Python tracked peak below 6 MiB and prints measured fixture/decoded/
peak values. This is bounded converter evidence, not raster-decoder process RSS,
live migration, real screenshots, native-platform parity or accepted cross-repo
DTU qualification. Exact F1 integration evidence is recorded in the delivery PR.

## CI Optimize sync owner binding

`media_conversion_owner.CaptureMediaOwner` is the concrete offthread synchronous
bridge to F1 ingest/publish/resolve/ACK. It implements the exact CI
`ImageOwner`/`StagedImage` protocol at
`4459d56cf6c870dd8d047eea62162e7fb4d78a4f` without importing CI or adding an
application dependency:

```python
CaptureMediaOwner(
    diagnostic_store,
    staged_factory=StagedImage,
    origin_for=authorize_original_provenance,
    checkpoint_receipt_for=verify_installed_diagnostic_operation,
    new_asset_bytes_for=measured_unique_asset_bytes,
    reader_ready=qualified_diagnostic_reader_and_store_scope,
    single_image_origin=attest_real_original_origin_cardinality,
)
```

All callbacks are mandatory, synchronous settled results. No running live event
loop is borrowed: the binding uses F1's actual host-only `ingest_sync` and
`publish_sync` aliases (candidate `0cf79691a54879d884b44acb954199d4fb3d5b05`),
with no private coroutine loop needed. CI's host must call sequentially under its stopped-writer/flush
boundary. The temporary image stream is not retained after `stage_image`.

* `stage_image(BinaryIO, mime=..., origin=...)` hashes decoded original bytes,
  binds CI's exact source digest/record/offset facts to a real host-attested
  original provenance callback, then stages through F1. A missing/unknown policy,
  unestablished screenshot expiry or unsupported provenance refuses explicitly.
  A generic event timestamp is **not** expiry/retention authority. Original TTL
  is never derived from migration time, renewed or replaced with durable null.
  F1 byte ingest binds a real origin to its complete ref-set. This initial sync
  bridge therefore requires the host to **preflight/attest one image per real
  origin** before any staging effect. Grouped/unknown-cardinality sources are
  explicitly held pending an owner-qualified group adapter; no per-image
  fictitious origin IDs are created to work around that boundary.
* Its factory returns `StagedImage(replacement=..., receipt=...,
  new_asset_bytes=...)`. Factory-returned fields must equal the verified expected
  fields; mutable aliases are detached. The binding stages **and assets-only
  publishes** before measurement so added bytes reflect actual final unique
  asset allocation growth, not decoded size/theoretical savings. Filesystem
  rounding can legitimately exceed logical image bytes; dedup can add zero.
  Staging/recovery overhead is separately held, never mislabeled final asset
  allocation. The host tracks exact before/after publication allocation through
  its required measurement callback; missing accounting refuses rather than
  inventing a claim. This earlier asset publication is still uncommitted:
  no diagnostic/canonical reachability exists until the actual file ACK.
* `publish(manifest)` and `verify(manifest)` page bounded owner manifest rows
  `{origin: <CI source snapshot>, owner: <binding receipt>}`. They reauthorize
  original provenance, validate F1 owner records and original byte closure.
  Publication reconciles F1's actual saved origin receipt; no new observation or
  historical ingestion replay is manufactured. Owner receipts are at most16KiB.
* `acknowledge(manifest, source=..., sha256=...)` verifies the installed diagnostic
  file hash and requires a **host-issued exact diagnostic operation receipt**.
  The store's independent checkpoint verifier must exhaust/verify installed event
  file SHA, preserved event/mapping identity and all published refs. A manifest or
  ordinary self-declared `{refs:[...]}` is not that production authority.

The diagnostic store uses `SessionMediaStore(explicit_private_root, session_id,
policy=MediaPolicy(host_limits...), checkpoint_verifier=host_verifier)`. It must
be **distinct from canonical session stores and never mounted as a live media
capability**. The reader-ready callback qualifies that scope as well as the exact
diagnostic replacement shape. Diagnostic checkpointing is local capture-file
reachability, not a canonical transcript checkpoint, ingestion replay, provider
delivery or model-image grant.

The entire diagnostic image position becomes a Core TextBlock with explicit
no-pixels text and `metadata["experimental.media.v1"]["refs"]=[ref]`; CI preserves
visibility. No reference is stored in `ImageBlock.source`. Its exact label is
`[Session image <sha256>; diagnostic reference only; no pixels supplied]`.
`diagnostic_image_ref(block)` is a strict metadata-only reader for an already
selected CI-owned image position. It does not hunt arbitrary strings, hydrate
pixels, promote diagnostic refs into canonical history, or claim vision.

Exact F1 candidate used for initial integration:
`8475bca3c17a374b8bcaf0a63974e7b82edc1041`. It is candidate-graph evidence, not
accepted upstream, publication, installed live adoption or a DTU qualification.

### Complete CI original groups

`CaptureGroupMediaOwner` implements the **same** CI ImageOwner methods, removing
the earlier single-image-only coverage boundary when the CI source owner can
attest/enumerate the complete original group:

```python
CaptureGroupMediaOwner(
    diagnostic_store,
    group_sources_for=source_owner_complete_group,
    staged_factory=StagedImage,
    origin_for=authorize_original_provenance,
    checkpoint_receipt_for=verify_installed_diagnostic_operation,
    new_asset_bytes_for=measured_unique_asset_bytes,
    reader_ready=qualified_group_reader_and_store_scope,
)
```

`group_sources_for(exact_ci_snapshot, original_provenance)` returns a context
manager over an ordered iterable of `{stream: BinaryIO, mime: str, origin:
exact_ci_snapshot}`. The stream owner keeps one source valid until advancing
the iterator; Foundation neither searches a capture pathname nor borrows
authority from its bytes. Source enumeration must be bound to the existing
stopped-writer/source-hash/record-mapping fence. Every member must independently
map to the same unchanged attested original policy. A source position appears
exactly once and the currently selected image must be present with matching
digest/length/MIME. No group completion is inferred from callback timing.

The binding uses actual F1 `ingest_group_sync` at `bbfd427...`, verifies the
complete ordered result, then publishes that complete set. Repeat image-position
callbacks reconcile the same original set; no new original IDs or renewed
timestamps are generated. Only host-max-images-bounded ref/source metadata is
accumulated; pixel streams remain bounded/disk-backed. The group owner receipt is
still bounded by CI's16KiB manifest schema; unsupported metadata is refused.

The distinct group reader gate is `foundation.capture-image-owner-group.v1`.
Receipt fields are `binding`, `origin`, complete ordered `refs`,
`source_origins`, and the selected integer index. Only the selected ref goes
into this diagnostic image replacement. **The host's installed-file verifier
must enumerate selected refs at actual installed positions, not blindly commit
every ref in a publication group receipt.** Other group members can be
published-uncommitted until their own canonical diagnostic positions are
verified. Group publication/verification alone never acknowledges reachability.
The single-image adapter remains an explicitly preflighted compatibility option;
unknown original provenance remains held in both paths.