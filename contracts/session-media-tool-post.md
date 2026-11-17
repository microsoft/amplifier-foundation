# Participating final tool-result bridge

`install_media_hook_bridge(coordinator)` installs the public
`session.media.tool_post` boundary before module initialization and host posthook
registrations. It preserves Core's native emit/process semantics and executes
the chain exactly once. It neither publishes refs nor acknowledges persistence.

Positive construction supports exactly Core 2.0.2 and an explicitly participating
cooperative host graph:

```python
bridge = install_media_hook_bridge(session.coordinator)
graph = bridge.begin_participating_graph(
    session, owner="actual-host-variant",
    participants=("actual-loop", "actual-context", "actual-provider",
                  "actual-tool", "host.final-policy"),
)
await session.initialize()
view = graph.participant("host.final-policy")
view.hooks.register("tool:post", actual_final_policy, name="final-policy", priority=900)
receipt = graph.seal()
assert bridge.validate_receipt(receipt)
```

The names above are illustrative, not a source-approval inventory. The host must
select the actual reviewed modules and late owners. Structural
`participating_module_ids(session)` is inspection, not automatic approval.

Construction requires the actual fresh uninitialized Core session/coordinator.
The decorated loader records ordered load/mount/ready occurrences and their
module/config/source and live installed identities. All configured occurrences
and prescribed late owners must participate. Registrations use their supplied
participant views, not a retained raw registry. Sealing after actual initialization
and late registrations issues an opaque local token. Reports, names, copied
fields or a Boolean cannot reconstruct it. `readiness()` is nonauthoritative.

Ordinary saved configuration can use `graph.configure()` before sealing, or
`graph.transition()` on a sealed idle graph. Pass the explicit participant view
to `SessionConfigurator(session, prepared_bundle, coordinator=view)`. Its
participating suspension/restore adapter preserves loaded slot provenance.
Failed or cancelled operations retain evidence and fence fallback/retry; a stash
or name alone cannot authorize restoration. Generic nonparticipating coordinators
retain their prior stash behavior. Requests/boundaries in flight fence changes;
successful resealing invalidates the earlier generation token.

## Final effective result

Invoke the bridge with the actual call event after media staging:

```python
settled = await bridge({
    "tool_name": actual_tool_name,
    "tool_call_id": actual_call_id,
    "result": staged_result.model_dump(mode="json"),
})
```

Its mapping contains `complete` (Boolean), `result` (effective value or null) and
the actual processed Core `HookResult` in `hook_result`. Consumers must preserve
both the complete flag and null; truthiness/default fallback to prehook data is
incorrect. Incomplete construction or a changed/unwitnessed chain yields
`complete=False, result=None`.

Explicit null, removed result/output, in-place redaction, denial, malformed
callback results and swallowed callback errors must never reattach original
refs. A later injection callback cannot resurrect an earlier null. Native
context injection/denial remains processed once. Only refs surviving the final
complete result may be published and placed in canonical rows.

The bridge retains its attributed route API for the paired streaming dispatcher.
It does not install child sessions, grant inherited media access or add
delegate/fork/subprocess forwarding. Those remain outside this extraction.

## Cooperative guarantee

This is not isolation from arbitrary Python or a universal native hook witness.
A raw callback can register, unregister itself and throw before a participating
callback, while Core logs/swallows the failure; public snapshots cannot prove
that hidden execution. Hosts must keep the actual graph cooperative and use
supported views/lifecycles. Source approval, host policy, writer ownership,
checkpoint durability and provider transport are separate obligations. No live
graph enabling, user-history migration or paid inference is implied.