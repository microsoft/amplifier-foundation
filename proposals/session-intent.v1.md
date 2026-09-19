# Pending intent and decisions Contract — v1 (DRAFT)

Proposed for independent review, not an implemented API or ratified obligation.
Parent boundary: [portable controls](session-controls.v1.md), including schema participation.

## Who builds against this

Host admission controllers, composers/queues, question and approval adapters, and users.
The artifact retains intent; it does not make a historical answer executable.

## What it is

```text
draft -> presentation only
submitted intent = intent_id + queue/steer + exact target + payload/reference + disposition
pending -> admitted -> settled; pending -> discarded
approval = live owner + run + request_id + exact operation scope + current decision
```

These are proposed transitions, not permission to transfer an existing runtime callback.
Execution receipts and admission ordering belong to [recovery](session-recovery.v1.md).

## The promises

1. **Separate drafts from submissions.** Unsent text remains presentation state;
   only explicit submission creates an identified portable pending-intent record.
   Broken: Opening a conversation in another host submits an old draft or answer editor.
   Affected: people composing instructions and applications offering restoration.

2. **Retain exact meaning.** Queue and steering carry distinct kinds and targets;
   steering retains its original run/boundary and never silently becomes a later task.
   Broken: A correction aimed at interrupted work is delivered to an unrelated turn.
   Affected: people correcting work and modules admitting boundary-scoped input.

3. **Deduplicate from verified evidence.** Retained verified receipts reject duplicate
   admission; missing/conflicting/rolled-back evidence blocks execution, never becomes pending.
   Broken: A lost acknowledgement produces a second turn or a claimed exactly-once tool effect.
   Affected: people retrying controls and hosts recovering transport failures.

4. **Reconfirm after handoff.** Pending intent from a different ownership acquisition
   remains held until explicit reactivation; loading a session or goal is not that decision.
   Broken: Resume releases a queue, retargets steering, or starts paid work without confirmation.
   Affected: people changing applications or inspecting saved conversations.

5. **Do not transfer approval grants.** Saved questions/decisions are receipts only;
   new execution needs a live, scoped request and decision under current policy.
   Broken: A stale answer approves changed parameters, a new run, or another request.
   Affected: people granting permission and modules waiting for authorized decisions.

6. **Retain bounded private intent honestly.** Preserve payload identity and disposition;
   capacity refuses new admission rather than dropping pending/unresolved input.
   Broken: Retention erases submitted intent, logs its text as telemetry, or permits ID reuse.
   Affected: people entrusting instructions and implementers of durable admission.

## Not in v1

Cross-host draft synchronization, a shared approval-policy language, transferable
callbacks, exactly-once effects, deduplication across undetectable lost persistence,
or automatic queue release. Unknown-schema handling and compatible receiver checks
delegate to [portable controls](session-controls.v1.md).

## How the kit checks it

Proposed entrypoint gates distinguish drafts, queue, steering, and answer editors;
switch owners before admission and after a lost acknowledgement; replay duplicate IDs;
deliver stale decisions after parameter/run changes; test missing payload references
and full retention capacity. Assert exact backend operation counts and retained data.
No formal verdicts precede ratification; fixture success is not host-wide conformance.

## Open questions

Which payload references preserve attachments without rereading changed files?
How are terminal intent identities compacted without enabling stale admission?
What explicit reactivation consent is clear across graphical and terminal clients?
