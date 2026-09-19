# Portable session controls and recovery — proposal overview

This is a design proposal, not an implemented API or a cross-application
conformance claim. Its three bounded contracts are **DRAFT** for independent
review; adopting their shape does not impose a project-wide documentation method.

| Proposed boundary | Contract |
| --- | --- |
| Supported session selections and compatible receivers | [session-controls.v1](../proposals/session-controls.v1.md) |
| Submitted intent, steering, questions, and approvals | [session-intent.v1](../proposals/session-intent.v1.md) |
| Owner-bound execution and conservative recovery | [session-recovery.v1](../proposals/session-recovery.v1.md) |

## What already exists

[SessionHistoryStore](SESSION_HISTORY.md) reads/writes native `transcript.jsonl`
and `metadata.json`, supports strict backup recovery, and optionally enriches an
in-memory view with CI observations. Its JSON metadata can persist caller-owned
data; it defines no portable control schema or automatic control restoration.
`SharedSessionStore.acquire()` and `HeldSession.check()` / `release()` supply
cooperating same-host, same-user POSIX ownership. That capability is process-bound;
owner display information is advisory. It is not execution recovery or fencing
of independently running remote operations.

`SessionHistoryStore` callers still enforce ownership; writes are atomic per
file, not a transcript/metadata transaction. The checkpoint examples in
[SHARED_SESSION_STATE.md](SHARED_SESSION_STATE.md) describe a separate API.
Native-history participants use its ownership mechanism without writing another
checkpoint through `HeldSession.write()`.

There are no portable-control APIs, capability-negotiation protocol, generic job
recovery protocol, or proposed conformance results implied by these documents.
Existing history and ownership adoption can proceed independently of their review.

## Candidate representation, not a storage commitment

Prefer one bounded `portable_session` extension in existing `metadata.json` over
another journal. Transcript remains conversation authority; CI remains optional
observations. The following shape is schematic, and all example IDs are synthetic:

```json
{
  "portable_session": {
    "version": 1,
    "revision": 7,
    "profile": "portable-session-controls-v1",
    "controls": [{
      "id": "selection-example", "kind": "model-selection",
      "adapter": "example.model-selection", "adapter_version": 1,
      "required": true, "scope": "root", "state": "selected",
      "value": {"provider_instance": "configured-provider", "model": "configured-model"}
    }],
    "intents": [], "executions": [], "extensions": {}
  }
}
```

The revision supports expected-revision checking under ownership, not a lease or
an ordering scheme across clocks. Module-owned settings and detailed job receipts
stay with their owners; this envelope is not a second configuration or module-state
authority. Validated references avoid copying effective mount plans,
`config.md`, raw results, or event payloads. Reference formats and resolution are
adapter contracts, not permission to load code or read arbitrary paths.

Suggested initial limits are 256 KiB UTF-8 for the complete extension, 128 pending
intents, and 64 KiB per intent payload within that same total. These are review
inputs, not shipped behavior. Compaction needs a reviewed identity-retention rule
before claiming unbounded session lifetimes. Metadata backup rollback and
per-file atomicity are reasons to evaluate this location carefully, not to call
it a write-ahead log or a power-loss durability guarantee.

## Candidate library seams, not implemented APIs

- `parse_portable_state(metadata)` returns preserved data and payload-free
  diagnostics. It does not restore a runtime.
- `plan_continuation(history, state, capabilities)` structurally validates and
  aggregates adapter-produced applicability, confirmation, and recovery decisions
  as data. Registered adapters own those decisions; Foundation aggregates them
  without inventing policy. Saved capability claims do not establish support.
- `merge_portable_state(metadata, update, expected_revision=...)` validates an
  owned patch without replacing unknown metadata. An ownership-aware save seam
  would use `SessionHistoryStore` and the original `HeldSession`.
- Adapters expose reviewed validate/export/apply/verify and optional recovery-plan
  operations. Foundation does not own provider routing, mode policy, goal loops,
  tool effects, private context serialization, or user-interface design.

A save wrapper must specify synchronization with release: `check()` followed by
an arbitrary later write is not an atomic ownership-aware operation. No new
kernel interface, event database, logger, or universal serializer is proposed.

## Review and integration decisions

1. Agree on the extension location, transition graph, transcript anchors, bounds,
   and compaction rule. Preserve uncertainty across split saves and older backups.
2. Choose the smallest useful control profile and owning adapters. Similar field
   names in different hosts do not establish equivalent provider/mode/goal scope.
3. Define how a receiver proves profile/adapter support before writable handoff.
   Current applications may preserve unknown metadata while ignoring its rules;
   the existing lock cannot make those applications enforce required controls.
4. Agree on setting precedence and consent when a required control is unsupported.
   Do not expose an application-private implementation as an accepted common API.

Each contract names its observable gates. Test actual entrypoints in both
directions, not only library units: history-only sessions; supported and missing
adapters; drafts/queue/steering; stale approvals; split saves; crash around
dispatch; nested stop; ownership contention; unknown versions; and unsupported
receivers. Use deterministic providers and isolated synthetic session data.
Reports identify exact participating versions and tested adapter profiles.

The first delivery can therefore adopt shared native history and ownership now,
while these proposed control boundaries receive separate review. No current host
should claim full portable-control conformance merely because it shares messages.
