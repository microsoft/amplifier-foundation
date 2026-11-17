# Private same-session provider requests

`MediaPreparedProvider(provider, capability, *, provider_identity, model=None,
execution_scope=None, prepare_overlay=None, request_scope=None)` wraps an actual
provider without changing vendor encoding. The host owns selected provider/account
generation identity, current model/options, source policy and graph qualification.
The capability must be the actual session's media capability, not an inherited
parent instance. A readiness report is not authority to enable images.

## Indexed provider support

An actual provider may expose this synchronous, read-only method:

```python
def get_media_request_support_for_request(
    *, request, model, request_options=None,
):
    return {
        "v": 2,
        "positions": [{"message_index": 1, "role": "tool"}],
        "mime_types": ["image/png", "image/jpeg"],
    }
```

`request` is the frozen post-overlay, prehydration Core `ChatRequest`, supplied
as a detached snapshot. Indices address that request's exact `messages`; each
position has exactly `message_index` (non-Boolean integer) and `role` (`user` or
`tool`) matching that message. Indices must be in range and unique. MIME strings
must be nonempty and unique. The outer mapping has exactly the three shown keys.
An empty positions/MIME list authorizes no images.

The public `validate_media_request_support(value, request)` performs only strict
schema/index/role validation and returns detached JSON. It does not call a
provider, resolve pixels or attest source provenance. `_indexed_support` is only
a compatibility alias for this same parser.

Presence of the indexed method is authoritative: invalid data, a noncallable or
awaitable method, attribute lookup failure or mutated input refuses; there is no
broader-role fallback. The wrapper checks snapshot/model/options mutation and
wipes detached image-source copies in cleanup. The getter must not retain or log
its request/options snapshot.

Only when the indexed method is absent does the wrapper use the actual provider's
synchronous `get_media_request_support(model=..., request_options=...)` with v1
role-position facts. Missing support supplies an empty role list. Caller-supplied
`provider_identity["media_support"]` is discarded, never trusted over the actual
provider. This v2 schema is unrelated to transcript-v2 storage.

## Canonical and private identity

Canonical history remains ref-only. At preparation, original-position refs become
private `ImageBlock` values only for supported positions, admitted unexpired
assets and current source policy. The role, tool-call identity, surrounding text
and tool specs stay on the same canonical message. Unsupported, expired,
source-refused or missing assets become explicit no-pixels warnings before count.
Warnings are frozen with the fitted request; later availability cannot silently
replace them with pixels after count.

`request_scope`, when supplied, must be a synchronous context factory yielding a
callable literal-True validator. One lease spans preparation, repeated count and
send, rather than entering/exiting around each wrapper method. The participating
bridge's `request_scope` fences concurrent graph transitions.

Actual provider count/transport implementations must call
`capability.validate(private_request, purpose="count" | "transport",
request_options=effective_options)` for the exact private object and effective
options at every attempt, including retries after backoff. Equivalent explicit
model/reasoning defaults normalize; changed wire options do not. Live account,
model, policy epoch, source, canonical/private content, expiry, asset identity
and checkpoint checks can invalidate a candidate. Revalidation must refuse, not
silently reproject or recount after an uncertain send.

The wrapper exposes `request_budget`, `refit_request`, `stream` and
`compact_context` only when implemented by the underlying provider. It does not
invent provider capabilities or a measured budget. An optional actual
`get_stream_request_support` promise is forwarded only with an actual stream
method; `{"v": 1, "tools": "chat_request"}` means the stream consumes canonical
`ChatRequest.tools`, not duplicate runtime tool objects.

## Cleanup and limits

Use `release_media_request(canonical, request_options=...)` to abandon only that
exact cached canonical/options association. Equal copies cannot abandon it.
`close()` revokes candidates; `aclose()` also closes live stream iterators.
Completion/error/cancellation/abandonment releases private image-source buffers,
owned overlay copies and the graph lease. Hosts must close wrappers at teardown.

Do not persist, log, serialize into diagnostics, or expose private requests,
base64 pixels or exception tracebacks containing those buffers. Diagnostic
digests/scalar reports do not establish delivery. Synthetic tests qualify
identity and cleanup, not vendor network acceptance, paid inference or live host
adoption. Legacy checkpoint verification is O(history) and can repeat on each
ref/check; see `session-media-checkpoint.md`.