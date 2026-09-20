# Shared execution and recovery Contract — v1 (DRAFT)

Proposed for independent review, not an implemented API or ratified obligation.
Parent boundary: [portable controls](session-controls.v1.md).

## Who builds against this

Session hosts, owning job/recovery adapters, and people returning after interruption.
Foundation supplies mechanism; module owners interpret their own effects and receipts.

## What it is

```text
acquire shared owner -> stable native history read -> assess -> explicit admission
receipt = session + unique run/attempt + intent + adapter + transcript anchor + outcome
persist unresolved -> dispatch -> save result/state -> verify quiescence -> settle
new owner + incomplete/conflicting receipt -> recovery plan, never automatic replay
```

The native transcript/metadata and optional CI observation roles remain as defined
in [SESSION_HISTORY](../docs/SESSION_HISTORY.md); the proposal adds no conversation copy.

## The promises

1. **Hold the original owner.** Acquire before writable loading; every mutation or
   dispatch captures that acquisition, checked and synchronized against release.
   Broken: An old callback borrows a new owner or writes after its acquisition ends.
   Affected: competing hosts and people relying on one execution owner.

2. **Record uncertainty before effects.** Persist admission/attempt identity and
   transcript anchors before dispatch; detailed jobs stay with their owning adapters.
   Broken: Work begins without a receipt, or reused tool-call IDs identify the wrong run.
   Affected: people investigating interrupted work and recovery implementers.

3. **Distinguish outcome from quiescence.** Returned, failed, cancelled, and unknown
   outcomes differ; settlement awaits owned work/children, not merely a cancel request.
   Broken: A parent appears settled while its jobs still act, or cancellation implies undo.
   Affected: people stopping nested work and adapters responsible for its lifecycle.

4. **Plan without replay.** Inspection and recovery planning invoke no effects;
   authorized reconciliation/retry uses a live owner and retry creates a new attempt.
   Broken: Startup repeats a tool/delegate/recipe or treats telemetry as approval/result authority.
   Affected: people recovering sessions and external systems affected by tools.

5. **Respect split saves and backups.** Mark transitions unresolved before writes,
   settle only after required persistence/verification; mismatches require assessment.
   Broken: An older metadata backup resurrects input/approval, or missing state proves "safe".
   Affected: people recovering crashes and hosts writing transcript plus metadata.

6. **Require owning evidence.** A free lock proves no cooperating owner, not stopped
   remote effects; missing/incompatible job/private-state adapters block unsafe continuation.
   Broken: Lock acquisition invents completion, or a generic state dump substitutes for recovery.
   Affected: people continuing work and modules with private operational state.

7. **Preserve unresolved identity.** Bounds cannot evict active/unknown receipts;
   fork/repair never silently transfers job authority, and no retry claims generic idempotency.
   Broken: Compaction permits duplicate execution or a fork reactivates parent work.
   Affected: long-running sessions, their users, and independent child-session owners.

## Not in v1

Atomic transactions across transcript/metadata, power-loss durability guarantees,
remote job fencing, force-unlock, or recovery of arbitrary module internals.
The existing POSIX/local shared-owner boundary is unchanged; independently writable
child sessions need their own ownership. [Intent](session-intent.v1.md) owns decision scope.

## How the kit checks it

Proposed actual-entrypoint gates contend across hosts; fault-inject around admission,
dispatch, result persistence, split saves, and backup recovery; interrupt nested jobs;
deliver late callbacks after reacquisition; test missing adapters and reused call IDs.
Assert no dispatch from loading, inspection, or planning; retries are explicitly
authorized new attempts. Check retained uncertainty, no invented success, and
inspection without source mutation. No formal verdicts precede ratification.

## Open questions

Which anchors and receipt transition graph tolerate provider-specific transcripts?
How can metadata backup recovery preserve uncertainty without another authority log?
Which owner-aware save seam synchronizes native-file mutation with handle release?
