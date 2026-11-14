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

`SessionMediaStore(root, session_id, *, policy, checkpoint_verifier=None,
origin_resolver=None, policy_verifier=None, nested_verifier=None, read_only=False,
source_read_verifier=None, request_only_verifier=None)` uses the
explicit private media root, never cwd. `MediaPolicy(max_bytes, max_pixels,
max_images, retention="durable", ttl_seconds=None)` is frozen host policy.
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
`adopt(refs, source_store)` is a host-only verified copy preserving original
provenance and expiry; target publication precedes target canonical checkpoint.
It is async and returns a list; synchronous `adopt_sync(refs, source_store)`
implements the same mechanism without running an event loop. Expired accepted
source refs adopt as metadata-only terminal observations, not refreshed pixels.
`import_verified(ref, stream, *, receipt, verifier)` is host-only: its verifier
must authorize the exact original ref/package/target, not just compare a supplied
hash. It revalidates original bytes and preserves provenance. An already expired
ref accepts `stream=None` only after that same host authorization; no bytes copied.
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
The installer dynamically consults trusted
`session.media.nested_verifier` when no explicit store callback was supplied.
Its dispatch-owned ledger is execution-bound, not a model-supplied declaration.

`validate_published_ref(ref, *, check_expiry=False)` is host-only acceptance
inspection for selected current/recovery history. Staged entries refuse.
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
Providers own per-attempt witness calls and final vendor encoding. Foundation
never traverses vendor wire payloads. Known diagnostic image sources must be
externalized by their owning adapter or omitted in a diagnostic copy **before**
`hooks.emit`, never logged as hydrated requests.