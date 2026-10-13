# Optional operation role consumption — v1 (DRAFT 2026-10-13)

The strategy-neutral boundary for callers selecting models for bounded operations.

## Who builds against this

Library and tool authors consuming `model_role_resolver`, and independently
implemented resolvers. This extends the generic [model-role convention](../docs/AGENT_AUTHORING.md)
without changing ordinary agent spawning, requiring Core changes or naming a strategy.

## What it is

A caller supplies a role and operation requirements; a resolver owns preference
order. The caller validates the returned exact provider before executing anything.

```text
resolve(role) -> provider/model/config preferences
optional compatible resolution -> prepared eligible preference | refusal
caller admission -> one exact mounted provider; no resolver-owned execution
```

## The promises

1. **MR1 — Consume a capability.** Callers discover the session's optional
   resolver instead of reading strategy files or importing its implementation.
   Broken: replacing a resolver requires changing the consumer's vendor logic.
2. **MR2 — Preserve ordinary semantics.** The optional operation path leaves
   existing `resolve(role)`, agent inheritance and explicit-preference behavior
   unchanged. Broken: adopting it reroutes an unrelated delegate or recipe step.
3. **MR3 — Filter before commitment.** A negotiated extension can assess
   operation compatibility before selecting a winner, while the strategy owns
   ordering. Broken: post-filtering one winner invents an unseen fallback chain.
4. **MR4 — Keep exact targets.** Returned selections identify an exact mounted
   connection and concrete model/configuration, or report unresolved identity.
   Broken: a family alias ambiguously chooses another account at dispatch.
5. **MR5 — Distinguish refusal causes.** Missing support, incompatibility,
   exhausted scope and provider/contract failures remain distinct. Broken:
   an error becomes silent session-default inheritance for a required operation.
6. **MR6 — Separate selection from effects.** Discovery and candidate checks
   do not generate content or consume execution budgets. Execution applies only
   the admitted selection. Broken: a preview spends or reselects after transmission.
7. **MR7 — Retain enforcement boundaries.** A preference is not credential
   authority or universal scope enforcement. Consumers preserve the host's
   applicable admission checks. Broken: a route label is treated as account consent.

## Not in v1

- A mandatory resolver extension signature; promote after two independent
  implementations qualify the same optional protocol and compatibility behavior.
- A policy engine, vendor rankings or account discovery inside Foundation;
  these remain outside this consumer convention.
- Kernel protocol changes or universal dispatch containment; a separate reviewed
  mechanism and cross-runtime evidence are prerequisites.

## How the kit checks it

Proposed checks, not a claim that the optional extension is implemented.

| Promise | Good observation | Falsifier |
| --- | --- | --- |
| MR1 | Two unrelated resolvers serve the same consumer | Strategy-file or implementation import |
| MR2 | Existing delegate/recipe choices match baseline | Ordinary role or inheritance changes |
| MR3 | Ineligible first candidate advances only in policy order | Consumer invents another candidate |
| MR4 | Two same-family connections remain distinct | Binding ambiguity or rebinding accepted |
| MR5 | No resolver and a failing resolver give different errors | Required operation inherits silently |
| MR6 | Repeated preview makes zero inference/budget changes | Preview spends or paid retry reroutes |
| MR7 | Denial prevents provider transmission | Selection bypasses admission |

## Open questions

The companion [technical note](../docs/MODEL-ROLE-CONSUMPTION.md) compares extension
shapes. Existing consumers need no new keyword arguments until negotiation succeeds.
Locking needs a settled signature, positive/negative fixtures, real implementations,
a complete consumer example and the steward's decision. These gates remain open.

## Changelog

| Date | Change | Evidence |
| --- | --- | --- |
| 2026-10-13 | Initial MR1–MR7 for operation-aware consumption. | Current direct and delegated role consumers need strategy independence and preserve different fallback policies. |
