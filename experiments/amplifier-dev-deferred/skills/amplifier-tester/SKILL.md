---
name: amplifier-tester
description: "Use when driving the amplifier-tester harness to validate ecosystem changes end-to-end inside a Digital Twin -- writing test scenarios, interpreting harness output, and the harness lifecycle."
---

> *Loaded on demand. The full guidance below is not in your default context --
> you reached for this skill because the situation called for it. Treat the
> sections that follow as authoritative for the duration of this task.*


You have access to the Amplifier amplifier tester bundle for testing changes to Amplifier repos (core, modules, bundles, foundation, app-cli) in isolated Digital Twin Universe environments.

## When to Use

- Developer has local changes to Amplifier ecosystem repos and wants to validate them
- Developer wants to test bundle, module, or prompt changes before pushing to GitHub
- Developer needs to verify multi-repo changes work together in isolation
- Do NOT use this to validate apps or things outside the Amplifier ecosystem. You should use `amplifier-bundle-reality-check` for that instead. If it is not installed, tell the user to get that bundle. Only use this bundle if they insist.

## How to Use

**ALWAYS delegate amplifier tester work to the specialized agent.** Do NOT attempt to generate DTU profiles or drive the CLI directly.

Set up a DTU environment for validating Amplifier ecosystem changes:
```
delegate(agent="amplifier-tester:setup-digital-twin", instruction="<what the user needs>", context_depth="all", context_scope="full")
```

For additional targeted validation checks inside an existing DTU:
```
delegate(agent="amplifier-tester:validator", instruction="<DTU instance ID and what to check>", context_depth="recent", context_scope="agents")
```

