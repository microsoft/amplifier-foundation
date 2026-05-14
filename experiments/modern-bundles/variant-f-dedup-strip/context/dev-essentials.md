# Amplifier Ecosystem

## Dependency Hierarchy

```
amplifier (docs) → amplifier-app-cli (reference app)
                      ├── amplifier-foundation (bundles, utilities)
                      └── amplifier-core (kernel, contracts)
                              ↑ ALL modules depend on core only
```

Modules depend ONLY on amplifier-core, never on foundation or apps.

## Change Impact & Push Order

| Change | Test With | Push Order |
|--------|-----------|------------|
| Core contracts | DTU validation + E2E smoke test | Core first → modules |
| Core internals | Unit tests + local override | Core only |
| Module API | Module tests + local override | Module only |
| Bundle composition | `amplifier run --bundle ./path` | Bundle only |
| Foundation | Direct tests + app integration | Foundation first |
| Multi-repo | DTU validation via amplifier-tester | Dependency order |

Push in dependency order: core → foundation → modules → bundles → apps.

## Testing Ladder

| Level | Confidence | When |
|-------|-----------|------|
| 1. Unit tests (`pytest`) | Low | Internal changes |
| 2. Local source override (`.amplifier/settings.yaml`) | Medium | API changes |
| 3. DTU validation (delegate to `amplifier-tester:setup-digital-twin`) | High | Cross-repo, contract changes |
| 4. Push & CI | High | Before merge |
| 5. Docker E2E smoke test (`./scripts/e2e-smoke-test.sh`) | Highest | Core releases / tags |

## Working in Multi-Repo

Use `amplifier-dev ~/work/feature-name` to create ephemeral workspaces with submodules. Changes persist when you push submodule repos. Destroy with `amplifier-dev -d`.

Commit format: `type: description` (feat, fix, docs, refactor, test, chore). Branch naming: `feat/`, `fix/`, `docs/`, `refactor/`.
