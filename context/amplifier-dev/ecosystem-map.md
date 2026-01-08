# Amplifier Ecosystem Map

## Dependency Hierarchy

```
                      amplifier (entry point, docs, governance)
                           │
                           ▼
                     amplifier-app-cli (reference CLI app)
                      │           │
          ┌──────────┘           └──────────┐
          ▼                                  ▼
    amplifier-foundation              amplifier-core
    (bundle primitives,               (kernel, contracts,
     shared utilities)                 session lifecycle)
          │                                  │
          │                                  ▼
          │                           [ALL MODULES]
          │                                ▲
          └────────────────────────────────┘
                (modules import core, never foundation)
```

## Repository Roles

| Repository | Role | Changes Here Affect |
|------------|------|---------------------|
| **amplifier** | Entry point, docs, governance | User onboarding, ecosystem rules |
| **amplifier-core** | Kernel, contracts, protocols | ALL modules and apps |
| **amplifier-foundation** | Bundle primitives, utilities | Apps using foundation |
| **amplifier-app-cli** | Reference CLI implementation | End users |
| **amplifier-bundle-*** | Capability bundles | Users of that bundle |
| **amplifier-module-*** | Runtime modules | Sessions using that module |

## Change Impact Matrix

| If You Change... | Test By... | Push Order |
|------------------|------------|------------|
| amplifier-core contracts | Shadow env with ALL dependent modules | Core first, then modules |
| amplifier-core internals | Unit tests + shadow with sample modules | Core only |
| amplifier-foundation | Direct tests + app integration | Foundation first |
| A module | Module unit tests | Module only (isolated) |
| A bundle | Load bundle, verify composition | Bundle only |
| amplifier-app-cli | Integration tests | After dependencies |

## Architectural Boundaries

### The Kernel Boundary
```
┌─────────────────────────────────────────┐
│           Applications                   │
│  (amplifier-app-cli, custom apps)       │
├─────────────────────────────────────────┤
│           Libraries                      │
│  (amplifier-foundation)                 │
├─────────────────────────────────────────┤
│           Kernel                         │  ← Stability boundary
│  (amplifier-core)                       │
├─────────────────────────────────────────┤
│           Modules                        │
│  (providers, tools, hooks, etc.)        │
└─────────────────────────────────────────┘
```

**Key rule**: Modules depend ONLY on amplifier-core, never on foundation or apps.

### Bundle vs Module

| Aspect | Bundle | Module |
|--------|--------|--------|
| Contains | YAML config, context, agent definitions | Python code |
| Depends on | Other bundles | Only amplifier-core |
| Changes require | No code changes to Amplifier | Module protocol compliance |
| Testing | Load and verify composition | Unit tests + integration |

## Multi-Repo Workspace Pattern

When working across repos, use `amplifier-dev` to create ephemeral workspaces:

```bash
# Create workspace with all core repos as submodules
amplifier-dev ~/work/my-feature

# Structure created:
~/work/my-feature/
├── AGENTS.md           # Workspace context
├── amplifier/          # submodule
├── amplifier-core/     # submodule
├── amplifier-foundation/  # submodule
└── [other repos as needed]
```

## Common Cross-Repo Scenarios

### Adding a New Module Protocol

1. Define contract in `amplifier-core/docs/contracts/`
2. Update kernel to support new protocol
3. Create reference implementation as a module
4. Document in `amplifier/docs/MODULES.md`

### Adding a New Bundle

1. Create repo `amplifier-bundle-<name>`
2. Define bundle.md with composition
3. Add to `amplifier/docs/MODULES.md`
4. (Optional) Add behavior to foundation for reuse

### Changing a Kernel Contract

**High-impact change** - requires careful coordination:

1. Design change, document in spec
2. Implement in core with backward compatibility if possible
3. Test ALL affected modules in shadow environment
4. Update modules to use new contract
5. Push core, then modules
6. Deprecation period if breaking
