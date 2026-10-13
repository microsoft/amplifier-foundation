# Operation-aware model-role consumption (DRAFT 2026-10-13)

Technical note for [MR](../contracts/model-role-consumption.v1.md), not a shipped
resolver API. No whole-repository vision or runtime policy is changed here.

## Existing boundary

Consumers obtain `model_role_resolver` from coordinator capabilities and call
`await resolver.resolve(role)`. Role input can be a string or ordered role list;
preferences carry `provider`, `model` and optional `config`. `known_roles` is
optional discovery, not authorization. The capability is duck-typed; callers
need not import a strategy implementation.

[ProviderPreference](../amplifier_foundation/spawn_utils.py) is the existing data
type. [Delegate](../modules/tool-delegate/README.md) and
[session naming](../modules/hooks-session-naming/README.md) are consumers with
different fallback policies. This proposal does not harmonize or overwrite those
policies. In particular, a bounded operation with a required output capability
cannot assume a session-default model can satisfy it.

## The missing operation boundary

A resolver can return a single winner even when its internal policy has several
candidates. A caller that checks compatibility only after selection cannot infer
the remaining candidates. The extension needs preselection eligibility without
exposing or duplicating the strategy's policy.

Proposed shape, subject to qualification:

```python
if negotiated_operation_resolution(resolver):
    preference = await resolver.resolve_for(role, requirements, prepare_candidate)
else:
    preferences = await resolver.resolve(role)
    preference = validate_returned_preferences(preferences, requirements)
```

The names above are illustrative. A versioned extension advertises its signature
before use; the caller never retries after a TypeError to guess which API ran.
`prepare_candidate` uses only the selected provider's optional operation surface.
It returns a prepared compatible choice or a typed refusal, never a generation.
The resolver owns order, scope, substitutions and final commitment. Preparation
facts can be retained so execution does not repeat selection or discovery.

A requirements-only extension is also viable if it preserves the same ownership
without importing vendor or consuming-application implementations. Choose one
signature through interoperability fixtures before making it an implementation
prerequisite. No new package, background router or required Core method is implied.

## Identity, observations and failure

An exact instance key is preferred to a provider-family alias. Legacy aliases
must bind through supported generic instance-selection semantics and refuse
unresolved ambiguity; do not clone private spawn helpers into every tool.
Cache catalogue evidence by exact binding and catalogue kind, never family alone.

Candidate incompatibility can advance within declared permitted order before
execution. An unavailable account, denied scope or broken capability contract is
not automatically incompatibility. The strategy's explicit failure policy must
preserve that distinction; the caller cannot convert an exhausted failure into
another account. A catalogue error is not an authoritative empty catalogue.

Requirements can constrain an operation without altering the conversational
model, mutable provider defaults or inherited caller policy. If a resolver
substitutes a model/configuration, revalidate the complete resulting choice.
An execution admission is separate from non-consuming compatibility inspection.

## Verification before adoption

Use a synthetic non-matrix resolver and a second strategy implementation, each
with compatible, incompatible and denied candidates. Compare ordinary role
resolution before/after, test ambiguous same-family mounts, missing/old extension
versions, catalogue errors and no generation during preparation. Do not claim a
passing extension from a documentation example or a single production strategy.

Runtime and dependency changes remain separate work; this note adds none.
