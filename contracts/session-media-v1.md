# Session media v1 (experimental)

Foundation owns the mechanism. Hosts select the storage root, limits, retention,
checkpoint verifier, origin resolver and enabled consumers. No Core changes or
provider wire formats are part of this component.

## Structural capability

Register a fresh session-bound object at `experimental.media.v1` before module
initialization. `version == 1`, `session_id` identifies its owner. The six
operations are:

```python
await ingest(value, *, source_format, origin, policy)
await publish(refs, *, origin, disposition)
validate(value, *, purpose, request_options=None)
resolve(ref, *, purpose)
await prepare_request(request, *, provider_identity, model, request_options,
                      purpose, execution_scope)
report(origin_or_prepared, *, event, outcome, details=None)
```

`origin` is a trusted runtime mapping `{kind, session_id, id}`. Tool `id` is the
actual call ID, never a tool argument. Complete original provenance adds
`created_at` and `expires_at` (timezone-qualified ISO timestamps, expiry nullable).
Only host/owner adapters supply original timestamps. `origin=None` requires the
host's trusted `origin_resolver`; absent binding refuses rather than fabricating
a call ID. Producers that cannot obtain the real call identity return canonical
Core rich content for the loop to ingest at its existing call-bound boundary.

`policy=None` selects the bound host policy. A producer policy mapping may only
restrict `max_bytes`, `max_pixels`, `max_images`, or `retention` (`durable`,
`request-only`, `drop`). It cannot extend the bound TTL or retention permission.

`image-bytes` accepts binary bytes, a binary `read(n)` stream,
`{stream: binary_stream, mime?: str}`, or explicitly tagged
`{encoding: "base64", data: str, mime?: str}`. Bounded reads enforce host limits;
strict base64 is decoded incrementally. Only static PNG/JPEG are initially
accepted. Pillow verifies actual format, dimensions and decode without rewriting
the original. Missing decoder support means unavailable, never trusted metadata.
Result: `{value: ref, staged_refs: [ref]}`.

`core-tool-result` accepts an actual Core ToolResult, preserves success/error and
useful output, externalizes canonical image content, and returns a new result
with no image bytes in `content`. It does not scan arbitrary JSON-looking text.
Result: `{value: sanitized_result, staged_refs: [...]}`.

`publish(..., disposition="retain"|"drop")` matches the exact staged origin.
Retain durably publishes immutable assets before returning
`{refs, publication: {state: "published", durability: "assets-only"}}`.
It does **not** establish canonical reachability. Drop returns no refs. Conflicting
re-use refuses; repeated identical publication reconciles saved state.

Validation is synchronous and raises `MediaRefusal` (a ValueError) on refusal;
success returns a small `{valid: True}` mapping. `resolve` returns a verified,
caller-owned binary handle. It never accepts paths, URLs or a digest alone.

`report(..., event="consumer-ready", outcome="ready",
details={contract: 1, role: "loop"|"producer"|"reader"|"provider"})` records
compatibility observations, not authority. Reports cannot create publication,
checkpoint acceptance or successful transport. Hosts explicitly enable only a
qualified graph; absent/incompatible handling yields useful text plus
`[Image unavailable: compatible media handling is not installed; no pixels supplied]`.

## Reference and host-only acknowledgment

Exact fields: `v:1, session_id, sha256, mime, bytes, width, height, provenance`.
Exact provenance: `kind, session_id, id, created_at, expires_at`. Booleans are not
integers. Session IDs are bounded safe identifiers. MIME, length and dimensions
match the original file; SHA-256 hashes original bytes. Observations with identical
bytes retain separate provenance/expiry. No resume, copy or fork renews expiry.

Tool output carrier: `output["experimental.media.v1"] = {"refs": [...]}`.
Canonical message carrier: `metadata["experimental.media.v1"] = {"refs": [...]}`.
Do not put references in ImageBlock.source or parse strings to discover them.

`SessionMediaStore(root, session_id, *, policy=None, checkpoint_verifier=None,
origin_resolver=None, policy_verifier=None, nested_verifier=None, read_only=False,
source_read_verifier=None, request_only_verifier=None)` uses the
explicit private media root, never cwd. `MediaPolicy(max_bytes=None, max_pixels=None,
max_images=None, retention="durable", ttl_seconds=None)` is frozen host policy.
Existing positional numeric construction remains supported. `None` means no
optional quota at this layer, **not** unlimited resource use or delivery authority.
There is no package-default image count, capture quota, expiry or storage reserve.
An omitted store policy selects this shared default; it does not disable images.
Producer limits must be positive integers and may only narrow: an unbounded host
quota can be narrowed to a finite integer, never the reverse. Explicit producer
`None`, booleans, nonpositive values and TTL overrides refuse.

Host-only `media_policy_for_root(existing_private_root, *, host_limits=None,
provider_limits=None, retention="durable", ttl_seconds=None)` performs the same
no-follow/private-owner directory validation without creating a path or taking
writer ownership. It returns a `MediaPolicy` whose optional positive integer
`max_bytes`, `max_pixels`, `max_images` facts are the minimum of applicable
explicit limits; absent/None facts remain absent. Root validation is not root
authorization. Limits must be actual trusted owner facts in compatible units:
original-image bytes/pixels and the selected origin's complete group count.
A provider's base64/wire-body or whole-request limit is not automatically an
original-image/per-origin quota. Provider owners must enforce final whole-request
limits on the exact selected projection; missing facts are never guessed.

For each staged image, the store freezes a finite operation byte ceiling from
the actual held destination's `fstatvfs().f_bavail * f_frsize`, any finite process
`RLIMIT_FSIZE`, and any tighter byte quota. Reads remain chunk-bounded, cannot
exceed requested size, and stop before writing an overflow probe. Capacity growth
does not replenish the operation ceiling; capacity shrinkage is checked before
chunk writes. Unavailable, malformed or exhausted resource bounds refuse rather
than substituting an arbitrary byte cap. These are advisory resource checks,
not space reservations: ordinary allocation/write/fsync errors still refuse,
and successful staging does not promise space for publication/snapshot copies.

Decoder dimensions are locally bounded by the actual installed Pillow
`Image.MAX_IMAGE_PIXELS` threshold and any tighter explicit pixel limit.
Foundation does not modify Pillow's process-global threshold or set it to None.
If that threshold is disabled and no finite explicit pixel limit exists, decoding
refuses, not unlimited fallback. Metadata/ref readers remain decoder-independent.
These I/O and decoder boundaries are not a hard process-memory reservation;
private provider encoding can expand bytes and the provider/host retains its
real request/resource admission checks. Exact original bytes, provenance,
request-only policy, original expiry and canonical-checkpoint rules are unchanged.
The store holds an exclusive POSIX lifetime writer lock; close abandoned session
stores before rebinding. Text/ref readers import without Pillow or POSIX support;
unqualified platforms refuse opening the durable store rather than claim parity.
`read_only=True` opens an existing private store without creating files or
rewriting journals. Accepted transfer requires a host-supplied
`source_read_verifier(ref, *, purpose="transfer") -> True` callback, before and
after asset snapshot read. It verifies the exact selected canonical/recovery
source and host revision/fences; observer reads never acquire writer authority
or mutate the running source. This permits guarded idle-source fork/export
without stopping its retained runtime. No guard means transfer refusal.
Host-only `acknowledge_checkpoint(receipt)` calls the injected synchronous
verifier, which must verify a durable settled canonical prefix/source identity
and return its exact references. The store checks those references were published.
Preparation and transport reverify the receipt; `report` is never an ACK.
This is legacy-checkpoint-backed, not a claim about transcript-v2 crash semantics.
The checkpoint owner supplies its own immutable receipt class. The actual
`checkpoint.acknowledge_legacy_checkpoint(session_dir, *, session_id,
expected_messages)` returns a local source-sealed receipt; inject
`checkpoint.verified_checkpoint_refs` as verifier. Exhaust it before binding.
Only `receipt.verify()` scalar proof is journaled; live receipt/seal/private path
never persist. Resume must acknowledge the actual current saved source again,
not reconstruct authority from the scalar proof. ACK returns a small
`{state, durability, ref_count}` result; references are not materialized as a
corpus-sized second list. Legacy checkpoint verification is O(history), with
one row resident; this is not hot transcript-v2 bounded-append performance.

Metadata-only staged/published journals remain enumerable for recovery. This
component does not delete orphan assets or authorize retention cleanup.
Host-only `ingest_sync` and `publish_sync` have the same arguments/results as the
async methods; they do not run an event loop and do not add capability operations.
Host-only `ingest_group_sync(binary_sources, *, origin, policy)` accepts the
complete finite ordered source iterable for one real original event/call, applies
any explicit max_images quota, and freezes one exact staged ref set. It returns
`{value: [refs], staged_refs: [refs]}`. Do not synthesize per-image operation IDs
or sequentially replace one origin's acceptance. Whole-group identical retries
reconcile; order/content changes refuse. Publication still uses the final set.
`adopt(refs, source_store)` is a host-only verified copy preserving original
provenance and expiry; target publication precedes target canonical checkpoint.
It is async and returns a list; synchronous `adopt_sync(refs, source_store)`
implements the same mechanism without running an event loop. Expired accepted
source refs adopt as metadata-only terminal observations, not refreshed pixels.
`import_verified(ref, stream, *, receipt, verifier)` is host-only: its verifier
must authorize the exact original ref/package/target, not just compare a supplied
hash. It revalidates original bytes and preserves provenance. An already expired
ref accepts metadata-only import after that same host authorization, whether the
earlier caller supplied `stream=None` or a stream whose precheck crossed the
original TTL. Once expired at the owner boundary the stream is not read or retried.
The owner rechecks after staging and publication as well: crossing expiry becomes
terminal metadata-only acceptance with exact original provenance/expiry, never a
fresh observation. Any bytes staged/published before that crossing remain private
recovery evidence under their owner's cleanup policy; import never deletes them.
Malformed refs, failed package authorization and mismatched verified bytes still
refuse rather than being reclassified as an expiry success.
Packages never supply media journals. Every target still requires canonical ACK.

`validate_ref(ref, committed=True, check_expiry=False)` is the host identity-only
path: it verifies schema, owner acceptance and checkpoint, never grants delivery.
`resolve_ref(ref, committed=False)` is the narrow host staging/published readback;
ordinary capability `resolve(..., purpose="model")` requires committed refs.
The returned private file handle is a verified snapshot, not a mutable asset FD.

The host's synchronous `policy_verifier(ref, *, purpose="model")` must return
exactly True or refuse. It owns source-specific consent/Stop/binding checks;
host adapters map known missing source-owner state to
`MediaRefusal(code="source_policy")`. Initial source-unavailable becomes a fitted
warning; available-to-denied after fit refuses. A screenshot producer additionally
requires host `computer_use.media_policy` binding, not generic `cap.enabled` alone.
Do not infer screenshot source class from a generic ref: hosts retain an original
session/call source index and consult the actual owner's issuance validator.
Host-only `classify_origin(origin_or_ref, *, classification="computer-use")`
records immutable scalar source routing in the indexed origin journal.
`get_origin_classification(ref)` reads it; adoption copies classification with
original provenance. Classification alone is never consent or delivery authority.

Nested refs keep their actual inner provenance. The host installs synchronous
`nested_verifier(parent_origin, ref) -> True` only where the dispatch owner attests
that exact surviving structured child output belongs to this outer operation.
Absent attestation refuses; same-session possession is not a relationship.
No report or extra method on the six-operation capability can create it.
The installer dynamically consults the trusted dispatch-owner callable
`tools.dispatch.media_refs` (legacy host alias `session.media.nested_verifier`)
when no explicit store callback was supplied.
Its dispatch-owned ledger is execution-bound, not a model-supplied declaration.

`validate_published_ref(ref, *, check_expiry=False)` is host-only acceptance
inspection for selected current/recovery history. Staged entries refuse.
`validate_published_group(refs, *, origin)` verifies an exact ordered **final
retained publication**, not merely ownership of each member. Supply a list
subject to any explicit host `max_images`, plus `{kind, session_id, id}` or
complete original provenance. It returns `{valid: True, ref_count: int}` or raises
`MediaRefusal`. Empty groups (including all-redacted retain results), truncation,
duplication, reordering, substituted refs, altered
original timestamps, absent/unfinished/dropped publication and malformed refs
refuse. A legitimately redacted subset succeeds only when that exact subset is
the saved final publication. This inspection never publishes, resolves pixels,
renews expiry, changes journals or creates checkpoint/model authority. It works
on guarded read-only observer stores and repeats their selected-source checks;
the caller still owns the original manifest/source authorization.
`resolve_published_ref(ref)` verifies accepted original bytes without requiring
current-model reachability; expired observations remain metadata-only. The host
must independently authorize the exact selected canonical/recovery source and
export/transfer. Neither method grants model delivery or imports a ref by hash.
`report(component, event="readiness", outcome="inspect")` returns a copy of
consumer observations plus enabled/authoritative:false; hosts need not inspect
private state and still own readiness policy.

## Private projection and provider witnesses

Canonical requests remain refs-only. `prepare_request` deep-copies Core messages,
preserves all owner metadata/call identity, and freezes original-position images
or explicit unavailable text **before** count. It returns a request-scoped
`PreparedMediaRequestV1` with `.request`, `.projection_digest`, `.release()` and
`.validate(...)`; pixels and validation callbacks are never canonical metadata.
An existing host-private Surface/Voice/Output image overlay is retained only if
`request_only_verifier(original_request, *, message_index, block_index, purpose)`
returns exactly True for that exact original owner-issued block. This callback
rechecks original owner freshness/Stop/consent on every witness; default inline
images become warnings. Nested/text-only/unsupported routes do not call it to
acquire new pixels. This is private request admission, not durable publication.

Provider identity is JSON `{provider, account, generation, media_support}`.
`media_support` is `{v:1, positions:[...], mime_types:[...]}` from the actual
provider's synchronous
`get_media_request_support(*, model, request_options=None)`;
optional scalar reason/evidence is allowed. Unknown support is an empty list. A provider must
actually qualify each claimed role/model. Nested/text-only scopes refuse hydration
and freeze an unavailable warning. Malformed or forged refs fail, not fallback.

Structural retry contract (no Foundation import): provider calls
`cap.validate(projected_request, purpose="count"|"transport",
request_options=effective_options)` immediately before **each** real attempt,
including after backoff. Only the exact in-memory prepared request association
is accepted. Canonical/projection digest, identity, model, options, scope, policy,
expiry, checkpoint and asset identity are rechecked. Hosts/providers must normalize
effective options consistently; transport-only flags cannot silently change a
fitted candidate. The host may instead give the provider an equivalent bound
`prepared.validate` callback through its explicit provider installation seam.
`.validate(*, request_options=...)` distinguishes omission from explicit None.
Equivalent redundant model/reasoning options matching the frozen request normalize;
different options never do. Optional host-only `.set_binding_validator(callback)`
rechecks live selected provider/account/model after awaits and retry backoff.
All ref-bearing and text-only warning requests use a private association on a
qualified path. Scope dictionaries with `nested: True` or `text_only: True` never
read image bytes; they freeze unavailable text. Purpose is host-selected JSON,
normally `model` or `context-compaction`. Provider purposes are `count`/`transport`
(`provider-count`/`provider-send` aliases accepted).

`MediaPreparedProvider` exposes optional operations only when its actual provider
implements them; it does not spoof feature detection through `__getattr__`.
Count and dispatch share one cached projection for the exact candidate/options.
Replacement/refit/release closes the old projection; completion, error,
cancellation, stream close and session close release request buffers.
Import it from `amplifier_foundation.session.media_provider`.
Optional synchronous `get_stream_request_support(*, model, request_options=None)`
is forwarded unchanged only if the actual provider implements both a callable
public `stream` and that callable support method. It is not inferred from SDK
streaming, `get_info`, signatures, media support or host wrappers.
Only an exact JSON `{v:1, tools:"chat_request"}` result, with integer `v` (not a
boolean) and no other fields, claims that this model/options route consumes
canonical `ChatRequest.tools` as the sole tool-schema input. The caller must
validate the current actual-provider claim before fitting and dispatch, and omit
the duplicate runtime `tools=` argument only on that qualified route. Missing,
malformed, declined or changed claims do not authorize that route. This forwarding
does not qualify any production provider, create a stream implementation, strip
options, serialize mounted runtime tools or relax count/dispatch identity.
`MediaPreparedProvider(provider, capability, *, provider_identity, model=None,
execution_scope=None, prepare_overlay=None)` accepts live zero-argument
identity/model getters. Optional host `prepare_overlay(private_canonical_copy, *,
purpose, request_options)` returns exactly `{request, validate}`; its synchronous
validate callable must return True and is repeated before every count/attempt.
It runs before generic projection and never mutates the canonical request.
An overlay containing inline pixels additionally needs `request_only_verifier`;
neither an overlay dict nor its metadata grants admission by itself.
Hosts mount each actual wrapped provider after initialization; existing host
wrappers may use the same projector internally instead of stacking wrappers.
Optional `media_budget_witness(canonical_request, *, request_options=None)` gives
`{v:1, projection_digest, validated:True}` only for the exact cached candidate
with a valid actual provider_count association, never text estimates or unavailable
counts. No witness invents a numeric budget or inference success.
Async `media_text_fallback_witness(canonical_request, *, request_options=None)`
can freeze an exact warning-only candidate without a provider call/count.
It returns `{v:1, validated:True, projection_digest, messages}` with detached safe
Core text dictionaries only; it provides **no numeric budget**. It requires
actual canonical media input, a prepared unavailable warning, and a wholly
text-only private projection. Unknown extras, opaque request/message/provider
metadata and non-text blocks refuse rather than being omitted from a local
estimate. Only the recognized reference carrier is removed from this detached
view; canonical references remain unchanged. Dispatch must use the same candidate
and effective options, and still revalidates every normal owner witness. This
does not turn estimated/unavailable usage into `provider_count`.
Providers own per-attempt witness calls and final vendor encoding. Foundation
never traverses vendor wire payloads. Known diagnostic image sources must be
externalized by their owning adapter or omitted in a diagnostic copy **before**
`hooks.emit`, never logged as hydrated requests.