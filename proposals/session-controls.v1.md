# Portable session controls Contract — v1 (DRAFT)

Proposed for independent review, not an implemented API or ratified obligation.
Context and candidate storage are in [the proposal overview](../docs/PORTABLE_SESSION_CONTROLS.md).

## Who builds against this

Session hosts, configuration/module adapter authors, and people switching hosts.
Foundation owns the interchange mechanism, not provider, mode, or goal policy.

## What it is

```text
control = id + kind + adapter/version + required + scope + selected/cleared + value/reference
registered receiver capabilities + controls -> applicable | unsupported | blocked
apply through owner -> verify effective selection -> report restored
```

This proposed artifact carries explicit selections, not serialized mounted objects.
Messages and observations retain the authority defined in [SESSION_HISTORY](../docs/SESSION_HISTORY.md).

## The promises

1. **Name the owner and scope.** Every control identifies its semantic kind, owning
   adapter/schema, required status, and intended scope; absence and explicit clearing differ.
   Broken: A root provider pin silently applies to children or auxiliary calls.
   Affected: people paying for work and modules interpreting selections.

2. **Keep one settings authority.** Existing configuration retains its owner and
   precedence; portable state references it or stores only otherwise-unrepresented overrides.
   Broken: Two copies of `config.md` or a mount plan compete by last-read order.
   Affected: configuration authors and people returning to saved sessions.

3. **Restore through the owning adapter.** Validate, apply, and verify the effective
   selection before claiming restoration; restoring a goal never itself starts work.
   Broken: Attribute-name matching fakes support, or opening history starts a goal loop.
   Affected: module authors and people expecting inspection rather than execution.

4. **Refuse silent fallback.** Unsupported or partially applied required controls
   block execution, retain state, and permit inspection; explicit consent can change them.
   Broken: A different model/mode/budget runs while the host reports successful restoration.
   Affected: people relying on saved constraints and costs.

5. **Preserve and bound unknown data.** Preserve unknown optional fields; unsupported
   major versions/required records block execution; bounded diagnostics omit payloads.
   Broken: Reading a newer schema erases its state or treats malformed data as defaults.
   Affected: host authors and people using different application versions.

6. **Verify receiver participation.** A writable handoff checks actual receiver
   profile/adapter support, not a saved claim; preserve-but-ignore clients are unsupported.
   Broken: Metadata or the common lock is advertised as enforcing rules on older clients.
   Affected: people switching hosts under required controls.

7. **Keep state private and inert.** Use bounded JSON and credential-safe references;
   adapter IDs resolve only registered implementations, never fetches or executable payloads.
   Broken: Portable state leaks credentials, installs code, or dumps private module objects.
   Affected: people sharing sessions and maintainers of replaceable modules.

## Not in v1

Universal private-state serialization, credential transfer, new configuration precedence,
remote enforcement, or automatic activation. [Intent](session-intent.v1.md) owns pending
inputs and approvals; [recovery](session-recovery.v1.md) owns execution authority.

## How the kit checks it

Proposed gates restore model/mode/goal through real fixture adapters in both host
directions; reject missing required adapters and partial application; preserve unknown
fields; exercise unsupported receivers, conflicting settings, bounds, and secrets.
Document shape checks do not prove behavior; no formal verdicts precede ratification.

## Open questions

Which selection adapters are semantically equivalent enough for the first profile?
How does a receiver prove support before handoff, including manual launches?
What bounds and reference formats preserve usability without exposing private state?
