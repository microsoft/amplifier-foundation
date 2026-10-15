# Operation-aware model-role consumption (DRAFT 2026-10-13)

Technical note for [MR](../contracts/model-role-consumption.v1.md). Foundation
implements the optional helper described here; the experimental contract remains
draft and unfrozen. Availability in this repository does not establish adoption
by a host or every resolver. No whole-repository vision, required Core interface
or strategy policy is changed by this note.

## Existing boundary

Consumers obtain `model_role_resolver` from coordinator capabilities and call
`await resolver.resolve(role)`. Role input can be a string or ordered role list;
preferences carry `provider`, `model` and optional `config`. `known_roles` is
optional discovery, not authorization. The capability is duck-typed; callers
need not import a strategy implementation.

[ProviderPreference](../amplifier_foundation/spawn_utils.py) is the existing data
type. [Delegate](../modules/tool-delegate/README.md) and
[session naming](../modules/hooks-session-naming/README.md) are consumers with
different fallback policies. The operation helper does not harmonize or overwrite
those policies. In particular, a bounded operation with a required output capability
cannot assume a session-default model can satisfy it.

## Prepare a bounded operation

A resolver can return a single winner even when its internal policy has several
candidates. A caller that checks compatibility only after selection cannot infer
the remaining candidates. The optional extension permits preselection eligibility
without exposing or duplicating the strategy's policy.

[`amplifier_foundation.operation_routing`](../amplifier_foundation/operation_routing.py)
provides the source-candidate entry point:

```python
async def prepare_operation(
    coordinator,
    role: str,
    requirements: dict,
    prepare,  # async (provider, preference) -> dict plan | None
    *,
    preferences=None,  # optional ordered ProviderPreference/dict list
) -> PreparedOperation:
    ...
```

The caller supplies non-generating preparation using the provider's optional
operation surface. Return a plain plan dictionary on success or `None` for
explicit incompatibility. Exceptions are failures and propagate; they do not
authorize trying another account. The helper owns no operation schema, vendor
ranking, credential discovery or execution.

`PreparedOperation` retains `provider_id`, the exact mounted `provider` object,
a copied `preference` and a copied `plan`. For example, with a caller-supplied
`prepare_candidate`:

```python
from amplifier_foundation.operation_routing import prepare_operation

prepared = await prepare_operation(
    coordinator,
    role="bounded-operation",
    requirements={"output_format": "required-format"},
    prepare=prepare_candidate,
)
# Apply the host's operation admission before execution.
prepared.assert_current(coordinator)
# Invoke the negotiated operation on prepared.provider with prepared.plan.
```

Preparation does not execute the operation or replace the host's admission
checks. Durable effect identity and saved results remain the caller's job.

The frozen `PreparedOperation` dataclass prevents field reassignment, not
mutation of its nested plan or preference configuration. Copies isolate these
values from the preparer's originals; they are not tamper-proof authorization
tokens. Callers must not change a prepared request or plan. The provider owns
validation of request, credential/account scope, plan integrity and single-use
execution. `assert_current()` checks only mounted-object identity and the
availability capability, not those provider-owned guarantees.

## Negotiate the resolver extension

An operation-aware resolver advertises:

```python
operation_resolution_protocol = "amplifier.operation-routing.v1"

async def resolve_for(self, role, *, requirements, accept):
    ...
```

`requirements` and `accept` are keyword arguments. `accept(preference)` is an
async, non-generating callback returning a strict boolean after exact-instance
preparation. The resolver owns candidate order, scope, substitutions and final
commitment. It must return a one-element preference list whose exact final
selection has been accepted once. Returning an unprepared or altered preference,
multiple winners, or the same selection prepared more than once is refused.

A resolver must propagate an exception from `accept`, not catch it and continue
to another account. This is a trusted in-process protocol: the current helper
does not sandbox a resolver or record a permanent failure if it swallows the
callback's exception. Providers and hosts still enforce execution admission.

If the protocol attribute is present but unsupported, or `resolve_for` is not
callable, the helper refuses with `unsupported_resolver`. It never probes a
signature by retrying after `TypeError`.

Without that protocol advertisement, the helper calls ordinary `resolve(role)`
and considers only its returned ordered preference list. A single winner does
not license an invented fallback chain. Passing explicit `preferences` bypasses
the resolver but uses the same preparation and exact-binding checks.

## Identity, observations and failure

A preference's `provider` must be an exact key in `coordinator.get("providers")`.
The helper does not resolve a family alias, clone an instance or construct a new
client; a non-matching key fails with `unknown_binding`. A resolver that supports
aliases must resolve them unambiguously before calling `accept`. `model` must
be nonempty and `config`, when present, must be a mapping.

When available, the coordinator's synchronous `provider.check_available`
capability is called with `provider_id` during preparation and again by
`assert_current()`. Hosts should follow the shared
[return-or-raise convention](provider-failure-policy.md#applying-a-selected-childs-provider-preferences):
return normally when available and raise an actionable exception otherwise.
The operation helper propagates that exception without trying another account.
As an operation-only defensive check, it also refuses the literal value `False`;
this is **not** a portable way for a host to report unavailability to other
consumers. An async availability result is invalid. `assert_current()` also
refuses if the mounted object at that key changed. These checks do not establish
entitlement or change ordinary child-spawn fallback policy.
The helper has no catalogue cache; consumers that cache evidence must key it by
exact binding and catalogue kind, never provider family alone.

`OperationRoutingError.code` identifies the helper's explicit refusals. It is
not a complete exception taxonomy: host/preparer exceptions propagate, and
malformed preferences or synchronous resolver implementations can raise
`TypeError`. Consumers must not translate any such exception into compatibility,
an empty result or permission to try an undeclared alternative.

Candidate incompatibility can advance within declared permitted order before
execution. An unavailable account, denied scope or broken capability contract is
not automatically incompatibility. The strategy's explicit failure policy must
preserve that distinction; the caller cannot convert an exhausted failure into
another account. A catalogue error is not an authoritative empty catalogue.

Requirements can constrain an operation without altering the conversational
model, mutable provider defaults or inherited caller policy. If a resolver
substitutes a model/configuration, revalidate the complete resulting choice.
An execution admission is separate from non-consuming compatibility inspection.

## Qualification before adoption

Use a synthetic non-matrix resolver and a second strategy implementation, each
with compatible, incompatible and denied candidates. Compare ordinary role
resolution before/after, test ambiguous same-family mounts, missing/old extension
versions, catalogue errors and no generation during preparation. Do not claim a
passing extension from a documentation example or a single production strategy.

The source helper and interoperability fixtures do not establish that every MR
clause has passed for every consumer. Retain exact dependency versions and
observed results separately. This documentation update reports no new test
execution or live operation and does not freeze the contract.
