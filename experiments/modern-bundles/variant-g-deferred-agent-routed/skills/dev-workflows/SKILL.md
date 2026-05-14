---
skill:
  name: dev-workflows
  description: Amplifier development workflows — multi-repo workspaces, cross-repo testing, push order, working memory. Load when doing ecosystem development.
  version: 1.0.0
---

# Amplifier Dev Workflows

## Multi-Repo Workspace

```bash
amplifier-dev ~/work/feature-name    # Create workspace with submodules
# ... work, commit, push submodules ...
amplifier-dev -d ~/work/feature-name  # Destroy when done
```

## Push Order (dependency order)

1. amplifier-core (if changed)
2. amplifier-foundation (if changed)
3. amplifier-module-* (affected modules)
4. amplifier-bundle-* (affected bundles)
5. amplifier-app-* (affected apps)
6. amplifier (docs, MODULES.md)

## Testing Ladder

| Level | Confidence | When |
|-------|-----------|------|
| 1. Unit tests (pytest) | Low | Internal changes |
| 2. Local source override | Medium | API changes |
| 3. DTU validation | High | Cross-repo, contracts |
| 4. Push & CI | High | Before merge |
| 5. Docker E2E smoke test | Highest | Core releases |

## Cross-Repo Validation

Changes to kernel contracts MUST be validated through at least one full E2E path: session init → tool dispatch → agent delegation → sub-session spawning.

## Working Memory

For long sessions, maintain SCRATCH.md at workspace root: current focus, key decisions, blockers, next actions. Prune if it doesn't inform the next action.
