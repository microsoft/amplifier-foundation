# Amplifier Development Workflows

## Workspace Lifecycle

### Create → Work → Destroy Pattern

```bash
# 1. Create ephemeral workspace
amplifier-dev ~/work/feature-name

# 2. Work in the workspace (changes go to submodule repos)
cd ~/work/feature-name
# ... make changes, commit to submodules, push ...

# 3. Destroy workspace when done
amplifier-dev -d ~/work/feature-name
```

**Key insight**: The workspace itself is disposable. Your work persists because you push submodule changes to their repos.

### Working Memory with SCRATCH.md

For long sessions, maintain a `SCRATCH.md` file at workspace root:

```markdown
# Current Focus
[One sentence: what are we doing RIGHT NOW]

# Key Decisions
- Decision: [what] → Reason: [why]

# Blockers / Questions
- [ ] Thing to resolve

# Next Actions
1. Immediate next step
2. After that
```

**Pruning rule**: If it doesn't inform the NEXT action, remove it.

## Cross-Repo Development Flow

### Standard Flow

```
┌─────────────────────────────────────────────────────────┐
│ 1. Create workspace with affected repos                 │
├─────────────────────────────────────────────────────────┤
│ 2. Make changes in dependency order:                    │
│    core → foundation → modules → bundles → apps         │
├─────────────────────────────────────────────────────────┤
│ 3. Test at each level (unit → local override → shadow)  │
├─────────────────────────────────────────────────────────┤
│ 4. Push in dependency order                             │
├─────────────────────────────────────────────────────────┤
│ 5. Destroy workspace                                    │
└─────────────────────────────────────────────────────────┘
```

### Dependency Order

Always make changes and push in this order:

1. **amplifier-core** (if kernel changes needed)
2. **amplifier-foundation** (if foundation changes needed)
3. **amplifier-module-*** (affected modules)
4. **amplifier-bundle-*** (affected bundles)
5. **amplifier-app-*** (affected apps)
6. **amplifier** (docs, MODULES.md updates)

## Common Workflows

### Adding a Feature to a Module

```bash
# 1. Create minimal workspace
amplifier-dev ~/work/module-feature
cd ~/work/module-feature

# 2. Add the module repo
git submodule add https://github.com/microsoft/amplifier-module-xyz.git

# 3. Make changes
cd amplifier-module-xyz
git checkout -b feat/my-feature
# ... edit, test ...

# 4. Test locally
pytest tests/

# 5. Push
git push origin feat/my-feature
# Create PR

# 6. Cleanup
cd ~/work
amplifier-dev -d ~/work/module-feature
```

### Changing Core + Dependent Module

```bash
# 1. Workspace with both repos
amplifier-dev ~/work/core-change

# 2. Make core changes first
cd amplifier-core
git checkout -b feat/new-capability
# ... make changes ...
pytest tests/

# 3. Update module to use new capability
cd ../amplifier-module-xyz
git checkout -b feat/use-new-capability
# ... make changes ...
pytest tests/

# 4. Shadow test (critical for core changes)
# Use shadow tool to test module works with local core changes

# 5. Push core first
cd ../amplifier-core
git push origin feat/new-capability
# Wait for CI, merge

# 6. Then push module
cd ../amplifier-module-xyz
git push origin feat/use-new-capability
```

### Adding a New Bundle

```bash
# 1. Create the bundle repo on GitHub first
# microsoft/amplifier-bundle-newbundle

# 2. Clone and structure
git clone https://github.com/microsoft/amplifier-bundle-newbundle.git
cd amplifier-bundle-newbundle

# 3. Create bundle structure
mkdir -p behaviors agents context docs

# 4. Create bundle.md (see BUNDLE_GUIDE.md for details)
cat > bundle.md << 'EOF'
---
bundle:
  name: newbundle
  version: 1.0.0
  description: Description of what this bundle provides

includes:
  - bundle: git+https://github.com/microsoft/amplifier-foundation@main
  - bundle: newbundle:behaviors/main
---

# New Bundle

@newbundle:context/instructions.md

---

@foundation:context/shared/common-system-base.md
EOF

# 5. Add to MODULES.md
# Edit amplifier/docs/MODULES.md to include new bundle
```

## Git Workflow

### Commit Message Format

```
type: short description

Longer explanation if needed.

🤖 Generated with [Amplifier](https://github.com/microsoft/amplifier)

Co-Authored-By: Amplifier <240397093+microsoft-amplifier@users.noreply.github.com>
```

Types: `feat`, `fix`, `docs`, `refactor`, `test`, `chore`

### Branch Naming

- `feat/description` - New features
- `fix/description` - Bug fixes
- `docs/description` - Documentation
- `refactor/description` - Code restructuring

### PR Process

1. Push branch to origin
2. Create PR with clear description
3. Link related PRs if cross-repo change
4. Wait for CI
5. Request review if needed
6. Squash merge

## Debugging Cross-Repo Issues

### Issue in Module Using Core

1. Identify which core API the module calls
2. Check if core API contract changed
3. Test with pinned core version to isolate
4. Use shadow environment to test fix

### Issue in Bundle Composition

1. Validate bundle YAML syntax
2. Check include paths resolve correctly
3. Test with `amplifier bundle validate`
4. Load bundle and check agent availability

### Issue in App

1. Check if foundation or core changed
2. Test with pinned dependencies
3. Use local source overrides to test fixes
