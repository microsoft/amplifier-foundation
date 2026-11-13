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
origin_resolver=None)` uses the explicit private media root, never cwd.
Host-only `acknowledge_checkpoint(receipt)` calls the injected synchronous
verifier, which must verify a durable settled canonical prefix/source identity
and return its exact references. The store checks those references were published.
Preparation and transport reverify the receipt; `report` is never an ACK.
This is legacy-checkpoint-backed, not a claim about transcript-v2 crash semantics.
The checkpoint owner supplies its own immutable receipt class.

Metadata-only staged/published journals remain enumerable for recovery. This
component does not delete orphan assets or authorize retention cleanup.
`adopt(refs, source_store)` is a host-only verified copy preserving original
provenance and expiry; target publication precedes target canonical checkpoint.

## Private projection and provider witnesses

Canonical requests remain refs-only. `prepare_request` deep-copies Core messages,
preserves all owner metadata/call identity, and freezes original-position images
or explicit unavailable text **before** count. It returns a request-scoped
`PreparedMediaRequestV1` with `.request`, `.projection_digest`, `.release()` and
`.validate(...)`; pixels and validation callbacks are never canonical metadata.

Provider identity is JSON `{provider, account, generation, media_support}`.
`media_support` is `{v:1, positions:[...]}` from the actual provider's
`get_media_support(model)`; unknown support is an empty list. A provider must
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

`MediaPreparedProvider` exposes optional operations only when its actual provider
implements them; it does not spoof feature detection through `__getattr__`.
Count and dispatch share one cached projection for the exact candidate/options.
Replacement/refit/release closes the old projection; completion, error,
cancellation, stream close and session close release request buffers.
Providers own per-attempt witness calls and final vendor encoding. Foundation
never traverses vendor wire payloads. Known diagnostic image sources must be
externalized by their owning adapter or omitted in a diagnostic copy **before**
`hooks.emit`, never logged as hydrated requests.