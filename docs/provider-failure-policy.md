# Provider preparation and initialization policy

Applications can opt into per-provider source failure handling without disabling
strict preparation for tools, hooks, orchestrators, contexts, or bundle packages.
The default `Bundle.prepare()` behavior is unchanged.

```python
from amplifier_foundation.bundle import ProviderPreparationFailure

async def record_failure(outcome: ProviderPreparationFailure):
    # Private host-owned diagnostics, not public browser state.
    failures.append(outcome)
    # Returning accepts this provider failure; raising rejects preparation.

prepared = await bundle.prepare(
    strict=True,
    provider_failure_policy=record_failure,
)

async def install_host_policy(session):
    session.coordinator.register_capability(
        "provider.load_failure", handle_mount_failure
    )

session = await prepared.create_session(before_initialize=install_host_policy)
# Explicitly apply the same host policy to spawned sessions:
result = await prepared.spawn(
    child_bundle, instruction, before_initialize=install_host_policy
)
```

`ProviderPreparationFailure` contains a copy of the configured provider entry,
its agent name (`None` for root entries), a phase (`source_resolution` or
`activation`), and the original exception. Accepted outcomes are also available
in `prepared.provider_preparation_failures`. Specs and exceptions may include
credentials; applications must expose only their own allowlisted diagnostics.
Changing the callback's spec does not modify the bundle, mount plan, or stored
outcome. Cancellation is propagated, never recorded as an accepted failure.

Provider entries, instance IDs, source declarations, model configuration, and
priority remain in the mount plan. The resolver records the exact failed source;
it cannot borrow a successful path for another source using the same module ID,
or silently retry an accepted failure. Successful source aliases are also
recorded explicitly. Repeated accounts using one source share its activation,
while each failed account gets its own outcome. An unknown source, missing
prepared path, or omitted source for these opted-in providers fails explicitly;
prepare the updated bundle again to recover or introduce a new provider source.
Ordinary non-provider lazy resolution is unchanged.

`before_initialize` is awaited after the module resolver, working directory, and
mention capabilities are mounted, but before module initialization and lifecycle
hooks. It applies to root, resumed, and spawned sessions and can deliberately
abort. It is not inherited automatically: the application owns spawning and must
pass its installer for each new session. This general callback does not require
a particular provider failure capability; applications using
`provider.load_failure` need a Core version that supplies that contract.

These mechanisms do not choose providers, remove unavailable entries, or enable
account fallback. The host still needs an unavailable-provider representation and
routing that distinguishes an explicit failed account from an absent model match.
They do not make arbitrary package-install side effects transactional.

## Applying a selected child's provider preferences

A host that retains unavailable accounts may additionally register the
synchronous `provider.check_available(instance_id)` capability. Returning means
the mounted account is available; raising preserves its actionable failure.
`apply_provider_preferences_with_resolution` calls it for the exact configured
account before an exact model or catalog lookup, honoring Core `instance_id`
before the older `id` field. Only another entry in the declared preference list
may provide fallback. If all entries fail or no model resolves, it raises rather
than leaving the default account selected. Cancellation is never fallback.
Without this capability, legacy no-match behavior remains unchanged.

This is a host boundary, not an automatic health probe. The host owns safe
error text, per-session account state, revalidation, and applying this policy on
each root/resumed/child session. Never publish provider secrets in exceptions.
