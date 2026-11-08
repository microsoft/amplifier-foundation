# Amplifier Ecosystem Testing

For testing local changes to Amplifier ecosystem repos (core, foundation, modules, bundles, app-cli), use the **amplifier-tester** bundle. It stands up a Digital Twin Universe (DTU) environment with your local code mirrored via Gitea and verifies the install works end-to-end.

## How to Use

Always delegate to the specialist agents; route all CLI driving through them.

```
delegate(agent="amplifier-tester:setup-digital-twin",
         instruction="<repos changed, exact checks, current live DTU count, remaining budget and cleanup owner>",
         context_depth="all", context_scope="full")
```

For follow-up checks against an existing DTU:

```
delegate(agent="amplifier-tester:validator",
         instruction="<complete setup handoff, exact checks and existing authorization>",
         context_depth="recent", context_scope="agents")
```

The setup agent classifies repos, snapshots approved source through its owned
Gitea sidecar, writes a Compose profile, launches and verifies it. The validator
runs the agreed checks (module loading, bundle composition, CLI smoke tests).
The caller enforces the cumulative cap across sibling delegations, accounts for
Gitea/gateway sidecars, and must not assume automatic teardown.

## When to Use

| Scenario | Action |
|----------|--------|
| Single-repo change (module, bundle, foundation) | Delegate to `amplifier-tester:setup-digital-twin` with that repo's path |
| Multi-repo coordinated change | Delegate with all changed repo paths; the agent handles cross-repo profile generation |
| Verifying after launch | Delegate to `amplifier-tester:validator` with the complete setup handoff |

## Inside a DTU

For new ordinary tests, the migrated tester uses the **digital-twin-universe
Smart Tool** on Docker Compose. Require the complete setup handoff defined in
`amplifier-tester:agents/validator.md`: backend tag, executable and library
source/version, Docker endpoint and observed daemon identity, private state root,
operation and instance IDs, profile path/hash, approved repos, required checks,
evidence destination, resource budget and cleanup owner. Resolve missing fields
before execution. An instance ID alone is not enough to select an operator.
Delegate checks to the validator; it uses the selected tool's canonical help
rather than a remembered CLI syntax. Preserve the host's managed admission and
durable-job route where one is available.

Tests use synthetic homes and deterministic checks by default. Provider
credentials and paid model calls are not automatically passed through or
authorized. Supply only the access required by the approved check.

Existing Incus environments remain bound to their original operator and records.
Do not reinterpret their IDs, translate profiles, or retry an unknown launch
through the Smart Tool. An unsupported legacy binding needs its existing owner,
not a replacement environment.

## Reference

- `amplifier-tester:context/amplifier-tester-awareness.md` — agent triggers and overview
- The selected tester's `digital-twin-universe` skill — inspect its source before
  loading; a separately enabled legacy behavior can expose the same skill name.
  Report a collision; do not load legacy instructions as the Smart Tool manual
  or silently reorder host sources. Use the selected executable's canonical help.
  Missing pinned knowledge or an unavailable selected executable is a concrete
  setup limitation, not permission to install a different revision.
