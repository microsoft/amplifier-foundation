# Experimental Bundles

This directory contains **experimental** bundles for testing new features before wider rollout.

## Philosophy

### Why Experimental Bundles?

New features need real-world validation before becoming defaults. Experimental bundles provide:

1. **Safe opt-in testing** - Users explicitly choose to test new features
2. **Feedback collection** - Early adopters help refine implementations
3. **Gradual rollout** - Features mature in experiments before promotion
4. **Easy rollback** - Standard bundles remain stable

### The Experiment Lifecycle

```
1. EXPERIMENT     → Feature implemented in exp-* bundle
2. VALIDATION     → Early adopters test and provide feedback
3. REFINEMENT     → Issues fixed, patterns refined
4. PROMOTION      → Feature moves to standard bundle
5. DEPRECATION    → Experimental bundle marked for removal
```

### Naming Convention

All experimental bundles use the `exp-` prefix:

| Bundle | Description |
|--------|-------------|
| `exp-foundation` | Foundation bundle with experimental features |
| `exp-amplifier-dev` | Amplifier development bundle with experimental features |

---

## Current Experiments

### exp-foundation

**Testing:** New `delegate` tool with enhanced agent orchestration

**Key Features:**
- Two-parameter context inheritance (`context_depth` + `context_scope`)
- Short session ID resolution (6+ character prefixes)
- Fixed tool inheritance (explicit declarations always honored)
- Feature Registry for dynamic description composition

**How to Use:**
```bash
amplifier bundle use exp-foundation
```

### exp-amplifier-dev

**Testing:** Amplifier ecosystem development with new delegate tool

**Includes:**
- All exp-foundation features
- Amplifier dev behavior (multi-repo workflows)
- Shadow environment helpers

**How to Use:**
```bash
amplifier bundle use exp-amplifier-dev
```

---

## Providing Feedback

When testing experimental bundles, please report:

1. **What worked well** - Features that improved your workflow
2. **What didn't work** - Bugs, unexpected behavior, regressions
3. **What's missing** - Features that would help but aren't implemented
4. **Suggestions** - Ideas for improvement

Open issues in the amplifier-foundation repository with the `[experiment]` tag.

---

## Comparison: Standard vs Experimental

| Aspect | Standard (foundation) | Experimental (exp-foundation) |
|--------|----------------------|-------------------------------|
| Delegation tool | `task` (legacy) | `delegate` (new) |
| Context params | `inherit_context` | `context_depth` + `context_scope` |
| Session resume | Full UUID required | Short 6+ char prefix |
| Tool inheritance | Bug: exclusions override | Fixed: declarations honored |
| Stability | Production-ready | Testing/validation |

---

## Creating New Experiments

When adding a new experimental bundle:

1. **Name it `exp-<name>`** - Follow the naming convention
2. **Document the experiment** - What's being tested and why
3. **Set version to 0.x.x** - Signal experimental status
4. **Include feedback instructions** - How to report issues
5. **Update this README** - Add to "Current Experiments" section

### Template

```yaml
---
bundle:
  name: exp-<name>
  version: 0.1.0
  description: |
    EXPERIMENTAL bundle for testing <feature>.
    
    To use: amplifier bundle add foundation:experiments/exp-<name>
---

# Experimental <Name> Bundle

## What's Being Tested
<description>

## Feedback
Please report issues with [experiment] tag.
```

---

## Graduation Criteria

An experiment is ready for promotion when:

- [ ] Core functionality works reliably
- [ ] No critical bugs outstanding
- [ ] Positive feedback from early adopters
- [ ] Documentation is complete
- [ ] Performance is acceptable
- [ ] Backwards compatibility addressed (migration path if breaking)

---

## Current Status

| Experiment | Status | Target Promotion |
|------------|--------|------------------|
| exp-foundation | Active testing | TBD |
| exp-amplifier-dev | Active testing | TBD |
