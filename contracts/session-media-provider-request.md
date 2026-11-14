# Private prepared-provider request boundary

This supplements `session-media-v1.md`; it changes no Core message or canonical
carrier. It qualifies no production provider or host installation by itself.

## Actual graph lifetime

`MediaPreparedProvider(..., request_scope=None)` optionally accepts an explicit
source-bound synchronous zero-argument context factory, for example
`request_scope=bridge.request_scope`. A boolean, a guessed discovery attribute,
or a scope entered around each individual method is not equivalent.

The returned context manager must yield a synchronous zero-argument validator
callable (or an object with `.validate()`), returning **literal `True`** only
while the captured graph receipt/generation remains valid. The wrapper enters
before host overlay, support qualification and projection. It holds that entered
scope across repeated count, fitted candidate retention, dispatch and provider
retry backoff. It validates before preparation/count/dispatch; the prepared
request's binding validator also checks it on each provider retry witness.

Candidate replacement/refit releases the old scope. Successful or failed send,
cancellation, stream close (including a never-started stream retaining a counted
candidate), preparation failure and `close`/`aclose` release scopes and buffers.
Private compaction has its own scope without displacing the fitted candidate.
Hosts must close abandoned wrappers/candidates; garbage collection is not a
lifetime protocol. The factory is optional for compatibility, not proof of graph
binding when omitted.

The actual host/bridge owns graph transitions. It must refuse transition while a
scope is active; any accepted same-session transition also needs store revocation,
identity-generation rotation and clearing old candidates. This wrapper neither
implements those host operations nor authorizes a transition.

## Optional exact indexed eligibility

An actual provider may implement this synchronous getter:

```python
def get_media_request_support_for_request(
    *, request, model, request_options=None
):
    return {
        "v": 2,
        "positions": [{"message_index": 3, "role": "user"}],
        "mime_types": ["image/png"],
    }
```

`request` is a detached Core `ChatRequest` representing the exact **post-overlay,
prehydration** source. It is read-only by contract; mutation of the snapshot or
options is detected and refused. The callback must neither retain input pixels
nor use this support query as permission to fetch them. Returned data must be
plain finite acyclic JSON: exactly the three top-level keys above, integer `v`
equal to 2 (not boolean), exact position keys, zero-based nonnegative in-range
integer indices (not boolean), unique indices, role `user` or `tool` matching the
message at that index, and unique nonempty string MIME types. No coercion,
wildcards, role expansion, extra keys, tuples or opaque objects are accepted.

Presence is authoritative. An empty positions/MIME list freezes truthful warnings
without asset reads. A malformed, asynchronous, noncallable or raising getter
refuses before preparation; it never falls back to broader v1 facts. Only absence
preserves the existing actual-provider
`get_media_request_support(*, model, request_options=None)` behavior unchanged.
Caller-supplied support in `provider_identity` is always overwritten. Restricted
providers should continue returning v1 `positions: []` for older hosts rather
than broadly advertising unqualified roles.

The index/role/MIME gate applies equally to durable refs and request-only inline
images, before asset reads or the inline owner verifier. There is no backward
image-bearing search, relocation, synthetic role, or history hydration when the
selected position contains no image, even if an earlier image has the same hash.

## Frozen identity and retries

The wrapper binds the original canonical request, a detached post-overlay source
digest, the actual provider object and provider/account/generation identity,
model, normalized effective options, execution scope, returned facts and graph
validator to one private prepared candidate. Count and send use that same private
request object and effective options.

Live support is re-queried against detached copies of the **same frozen
prehydration source**, never against hydrated output or original pre-overlay
indices. Revalidation does not rerun the overlay. Changed facts, source,
identity, graph, model, or options refuse dispatch/retry without rehydration.
Changed facts on repeated count also refuse rather than silently creating a new
candidate. Explicit refit/candidate replacement is a new preparation, not a retry
of a previously fitted request.

Providers still own final wire encoding and must invoke their installed prepared
request witness immediately before **every** real attempt, including after
backoff. The wrapper's pre-dispatch check cannot replace that provider obligation.
Pixels, callbacks and private request identities never become canonical metadata
or diagnostics. Numerical count/support/production facts are not invented here.