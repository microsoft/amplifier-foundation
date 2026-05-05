# Amplifier Ecosystem Testing

For testing local changes to Amplifier ecosystem repos (core, foundation, modules, bundles, app-cli), use the **amplifier-tester** bundle. It stands up a Digital Twin Universe (DTU) environment with your local code mirrored via Gitea and verifies the install works end-to-end.

## How to Use

Always delegate to the specialist agents. Do not drive the CLI directly.

```
delegate(agent="amplifier-tester:setup-digital-twin",
         instruction="<repos changed and what to verify>",
         context_depth="all", context_scope="full")
```

For follow-up checks against an existing DTU:

```
delegate(agent="amplifier-tester:validator",
         instruction="<DTU instance ID and what to check>",
         context_depth="recent", context_scope="agents")
```

The setup agent handles repo classification, Gitea mirroring, profile generation, launch, and basic verification. The validator agent runs targeted checks (module loading, bundle availability, CLI smoke tests).

## When to Use

| Scenario | Action |
|----------|--------|
| Single-repo change (module, bundle, foundation) | Delegate to `amplifier-tester:setup-digital-twin` with that repo's path |
| Multi-repo coordinated change | Delegate with all changed repo paths; the agent handles cross-repo profile generation |
| Verifying after launch | Delegate to `amplifier-tester:validator` with the DTU instance ID |

## Inside a DTU

Run commands via `amplifier-digital-twin exec <instance-id> <command>`. The DTU has the local code installed and provider API keys passed through.

## Reference

- `amplifier-tester:context/amplifier-tester-awareness.md` — agent triggers and overview
- `digital-twin-universe:context/dtu-awareness.md` — DTU concepts and lifecycle
