# Domain Validator Authoring Guide

This guide captures the methodology for creating accurate, actionable domain validators as Amplifier recipes. It codifies the critical patterns for avoiding false positives and creating validators that know when to stop.

---

## Overview

### What is a Domain Validator?

A domain validator is a recipe that systematically checks artifacts against:
- **Code-enforced requirements** (will break if violated)
- **Conventions** (code works, but deviates from patterns)
- **Best practices** (recommendations for quality)

### When to Create One

Create a domain validator when:
- A concept has multiple rules users struggle to remember
- Incorrect usage causes confusing errors or silent failures
- You want to codify expertise for consistent guidance
- Onboarding new users or contributors to a domain

### Success Criteria

A good validator:
- ✅ Produces **zero false positives** on known-good exemplars
- ✅ Catches **real issues** with actionable remediation
- ✅ Correctly **classifies severity** (ERROR vs WARNING vs SUGGESTION)
- ✅ **Aligns with code behavior**, not just documentation
- ✅ **Has explicit PASS thresholds** (knows when to stop finding things)
- ✅ **Has deterministic classification before LLM** (reduces variance)
- ✅ **Has a "clean bill of health" path** for passing items

---

## The "Always Finds Something" Anti-Pattern

> **Critical Principle**: Without explicit PASS thresholds, validators will always find something to suggest. This creates user frustration and distrust.

### The Problem

When validators lack clear completion criteria:
- Users fix issues, run again, and get MORE suggestions
- "PASS WITH SUGGESTIONS" feels like failure
- Unclear when work is actually done
- Creates anxiety: "What else will it find?"

### The Solution: Explicit PASS Thresholds

Every validator MUST define what "passing" means:

```yaml
# Example: validate-agents PASS thresholds
# An agent is classified as "good" (PASS) if ALL of:
# ✅ No structural errors
# ✅ Has explicit `tools:` section (even if empty)
# ✅ Description ≥ 100 characters
# ✅ Has at least ONE strong trigger (MUST, ALWAYS, REQUIRED, PROACTIVELY, DO NOT)
# ✅ Has at least ONE `<example>` block
```

### Domain-Specific Thresholds

Different domains have different PASS criteria. What makes an agent "good" differs fundamentally from what makes a bundle repository "good":

| Domain | Threshold Focus | Rationale |
|--------|-----------------|-----------|
| **Agent descriptions** | Behavioral quality | Agents need clear triggers and examples so orchestrators select them correctly |
| **Bundle repositories** | Structural integrity | Bundles need to load correctly and compose properly |

**Agent Description Thresholds:**
```yaml
# Behavioral quality criteria
- Has strong trigger (MUST, ALWAYS, REQUIRED, PROACTIVELY, DO NOT)
- Has at least one <example> block
- Has explicit tools: section
- Description ≥ 100 characters
```

**Bundle Repository Thresholds:**
```yaml
# Structural integrity criteria
- All bundles load without errors (BundleRegistry succeeds)
- Has entry point (root bundle OR behaviors/bundles dirs)
- No orphan agents (all .md files referenced)
- All references resolve (includes, @mentions)
- Consistent namespace usage
```

The key insight: agent validators focus on **how well the agent communicates its purpose**, while bundle validators focus on **whether the bundle will actually work when loaded**.

### Quality Tiers (Not Just Pass/Fail)

Use a tiered classification system:

| Quality Level | Meaning | LLM Analysis? | User Action |
|---------------|---------|---------------|-------------|
| `good` | Meets all thresholds | ❌ Skip | None needed |
| `polish` | Missing ONE criterion | ✅ Yes | Optional improvement |
| `needs_work` | Missing MULTIPLE criteria | ✅ Yes | Should address |
| `critical` | Structural errors | ✅ Yes | Must fix |

### The Clean Bill of Health Path

Validators MUST have a fast path for items that pass:

```yaml
# When all items pass thresholds:
# → Skip detailed LLM analysis
# → Issue brief approval summary
# → Return "PASS" (not "PASS WITH SUGGESTIONS")
```

This respects users' time and builds trust in the validator.

---

## The Code Verification Imperative

> **Critical Learning**: In the bundle validator project, 50% of initial rules were inaccurate when traced against actual code behavior.

**Do NOT trust:**
- Documentation (may be outdated)
- Your intuition (may be based on old patterns)
- What "should" happen (may differ from what does happen)

**DO verify:**
- Trace code paths with LSP tools (`findReferences`, `hover`, `goToDefinition`)
- Find the actual enforcement point (or confirm there isn't one)
- Document evidence for each rule

### Examples of Assumptions vs Reality

| Assumption | Reality (from code) | Impact |
|------------|---------------------|--------|
| "Inline agents are deprecated" | Both formats fully supported by `_parse_agents()` | Would create false positives |
| "Standalone bundles must include foundation" | Need session config (orchestrator + context), any source | Misleading guidance |
| "`context.include` and `@mentions` are interchangeable" | Different composition semantics! (accumulates vs replaces) | Wrong advice |

### The Verification Pattern

```
1. Write initial rule based on documentation/intuition
2. Use LSP tools to trace actual code behavior
3. Find enforcement point (exception raised? validation fails?)
4. Adjust rule severity based on evidence
5. If code differs from docs, update DOCS (not your rule)
```

---

## Graceful Degradation Pattern

> **Principle**: Validators should provide useful output even when infrastructure is unavailable, rather than failing with stack traces.

### The Problem

Validators often depend on infrastructure that may not be available:
- Framework libraries (e.g., `amplifier_foundation` not installed)
- External services (APIs, registries)
- Optional tools (LSP servers, linters)

When dependencies are missing, validators shouldn't crash—they should degrade gracefully while still providing value.

### The Solution: Environment Check + Fallback

```yaml
# Phase 0: Environment check (always runs first)
- id: "check-environment"
  type: "bash"
  command: |
    python3 << 'EOF'
    import json
    result = {"phase": "environment_check"}
    
    # Check for optional infrastructure
    try:
        from amplifier_foundation import BundleRegistry
        result["foundation_available"] = True
    except ImportError:
        result["foundation_available"] = False
        result["fallback_mode"] = "llm_analysis_only"
    
    print(json.dumps(result))
    EOF
  output: "env_check"

# Phase 2: Conditional based on infrastructure
- id: "validate-with-registry"
  condition: "{{env_check.foundation_available}} == true"
  # Full validation with BundleRegistry...

- id: "validate-with-llm-fallback"
  condition: "{{env_check.foundation_available}} == false"
  agent: "foundation:zen-architect"
  prompt: |
    BundleRegistry not available. Perform structural analysis using 
    file inspection instead. Check for:
    - YAML syntax validity
    - Required fields present
    - File references exist
```

### Progressive Enhancement

When full infrastructure IS available, validators provide richer feedback:

| Infrastructure | Validation Capability |
|----------------|----------------------|
| **Minimal** (files only) | Syntax, structure, file existence |
| **Framework available** | Registry loading, namespace resolution, composition |
| **Full tooling** | LSP analysis, type checking, deep reference tracing |

### User Experience

| Scenario | Old Pattern | Graceful Degradation |
|----------|-------------|---------------------|
| Missing dependency | `ImportError: No module named 'amplifier_foundation'` | "Running in fallback mode - structural analysis only" |
| API unavailable | Stack trace, recipe failure | "API check skipped - using cached rules" |
| Optional tool missing | Recipe hangs or fails | "LSP unavailable - using regex-based analysis" |

Users get useful output instead of error messages, and can still benefit from validation even in constrained environments.

---

## Severity Classification Framework

This taxonomy is **critical** for validator usefulness. Misclassified severity causes either:
- **False urgency** (warnings treated as errors → user frustration)
- **Missed issues** (errors treated as suggestions → real problems ignored)

### The Three Levels

| Severity | Definition | Evidence Required | Example |
|----------|------------|-------------------|---------|
| **ERROR** | Code enforces this; violation causes failure | Exception raised, validation fails, function returns error | `bundle.name` missing → namespace won't register |
| **WARNING** | Convention violated; code works but deviation noted | No code enforcement, but documented pattern | Using `/bundles/` instead of `/behaviors/` |
| **SUGGESTION** | Best practice; pure recommendation | No documentation requirement, expertise-based | Adding `meta.description` for discoverability |

### The Litmus Test

```python
# Is it an ERROR?
# Search for enforcement in code:
grep -r "raise.*Error" | grep "your_concept"
# If found → ERROR

# Is it a WARNING?
# Search in documentation:
grep -r "should|must|required" docs/ | grep "your_concept"
# If only in docs, not code → WARNING

# Otherwise → SUGGESTION
# Based on expertise and best practices
```

### Real Examples from Bundle Validator

| Finding | Initial Severity | Verified Severity | Evidence |
|---------|------------------|-------------------|----------|
| Missing `bundle:` wrapper | ERROR | **ERROR** ✅ | `Bundle.from_dict()` raises `ValueError` |
| Not using DRY pattern | ERROR | **WARNING** ⬇️ | No code enforcement, just convention |
| Inline agents format | ERROR | **REMOVED** ❌ | Code explicitly supports both formats |
| Orphaned agent file | WARNING | **WARNING** ✅ | Valid concern, but code doesn't fail |

---

## Recipe Structure Patterns

### Single-Item vs Repository-Wide

| Pattern | Use When | Example |
|---------|----------|---------|
| **Single-item** | Validating one artifact in depth | `validate-bundle.yaml` |
| **Repository-wide** | Scanning entire repo for patterns | `validate-bundle-repo.yaml` |

Often you need both: repo-wide discovers items, then delegates to single-item for deep validation.

### The 6-Phase Architecture

Based on learnings from validate-agents, validators should follow this structure:

```yaml
steps:
  # Phase 1: ENVIRONMENT
  # Detect capabilities, versions, available tools
  - id: check-environment
    type: bash
    
  # Phase 2: STRUCTURAL (deterministic)
  # Parse files, check syntax, verify references exist
  - id: structural-validation
    type: bash  # or Python with parse_json
    
  # Phase 2.5: QUALITY CLASSIFICATION (deterministic)
  # Classify items as good/polish/needs_work/critical
  # Set requires_llm_analysis flag
  - id: quality-classification
    type: bash  # Python script, NOT agent
    output: "quality_classification"
    
  # Phase 2.75: DEFAULT VALUES (deterministic)
  # Set defaults for optional outputs BEFORE conditional phases
  - id: set-default-quality-results
    type: bash
    command: "echo 'Not performed - all items met thresholds'"
    output: "quality_results"
    
  # Phase 3: QUICK APPROVAL (conditional)
  # Fast path when all items pass
  - id: quick-approval
    condition: "{{quality_classification.requires_llm_analysis}} == false"
    agent: foundation:zen-architect
    provider_preferences:
      - provider: anthropic
        model: claude-haiku-*  # Fast/cheap for simple approval
    
  # Phase 4: DETAILED ANALYSIS (conditional)
  # Only runs when quality_classification says needed
  - id: detailed-analysis
    condition: "{{quality_classification.requires_llm_analysis}} == true"
    agent: foundation:zen-architect
    
  # Phase 5: EXPERT REVIEW (conditional)
  # Domain-specific anti-patterns and edge cases
  - id: expert-review
    condition: "{{quality_classification.requires_llm_analysis}} == true"
    agent: foundation:zen-architect
    
  # Phase 6: REPORT SYNTHESIS
  # Always runs - synthesize findings into actionable report
  - id: synthesize-report
    agent: foundation:zen-architect
```

### Key Architectural Changes

| Old Pattern | New Pattern | Why |
|-------------|-------------|-----|
| LLM analyzes everything | Deterministic classification FIRST | Reduces variance, faster |
| Always runs all phases | Conditional execution | Respects user time |
| No fast path | Quick-approval for clean items | Builds trust |
| Severity assigned by LLM | Severity assigned by code | Consistent results |

### When to Use Bash vs Agent Steps

| Check Type | Tool | Rationale |
|------------|------|-----------|
| File exists | bash | Deterministic, fast |
| YAML syntax valid | bash | Deterministic |
| Regex pattern match | bash | Deterministic |
| **Quality classification** | bash/Python | **Deterministic (critical!)** |
| Convention assessment | agent | Requires reasoning |
| Pattern detection | agent | Requires context |
| Report synthesis | agent | Requires judgment |

---

## Conditional Execution Pattern

> **Critical Principle**: Don't run LLM phases when items already pass. This wastes time and creates opportunities for the LLM to find things that shouldn't be flagged.

### The Pattern

```yaml
# Step 1: Deterministic classification sets the flag
- id: "quality-classification"
  type: "bash"
  command: |
    python3 << 'EOF'
    # ... classification logic ...
    result = {
        "quality_level": "good",  # or polish/needs_work/critical
        "requires_llm_analysis": False  # True if any item not "good"
    }
    print(json.dumps(result))
    EOF
  output: "quality_classification"

# Step 2: Quick approval when all pass
- id: "quick-approval"
  condition: "{{quality_classification.requires_llm_analysis}} == false"
  agent: "foundation:zen-architect"
  prompt: |
    All items met quality thresholds. Provide brief approval summary.
    DO NOT look for additional issues - thresholds are intentional.

# Step 3: Detailed analysis ONLY when needed
- id: "detailed-analysis"
  condition: "{{quality_classification.requires_llm_analysis}} == true"
  agent: "foundation:zen-architect"
  prompt: |
    Analyze items that need work: {{items_needing_work}}
```

### Benefits

1. **Faster execution** - Clean items skip LLM entirely
2. **Reduced variance** - LLM can't invent issues for good items
3. **Clear completion** - "PASS" means pass, not "pass but..."
4. **Cost savings** - Fewer LLM calls for well-maintained codebases

---

## "What is NOT an Issue" Guidance

> **Critical Principle**: LLMs naturally want to be helpful by finding things. You must explicitly tell them what NOT to flag.

### The Problem

Without guidance, LLMs will flag:
- Minor style differences ("could use more emojis")
- Reasonable alternative approaches ("consider using X instead")
- "Could be slightly better" for items meeting thresholds
- Personal preferences disguised as issues

### The Solution

Every LLM prompt in a validator MUST include explicit "not an issue" guidance:

```yaml
- id: "description-quality-check"
  agent: "foundation:zen-architect"
  prompt: |
    Analyze these agent descriptions for quality issues.
    
    ## What IS an issue (flag these):
    - Missing trigger words (MUST, ALWAYS, REQUIRED)
    - No examples of when to use the agent
    - Description too short to be useful (<100 chars)
    - Contradictory instructions
    
    ## What is NOT an issue (do NOT flag):
    - Minor style differences (punctuation, formatting)
    - Reasonable alternative phrasings
    - "Could be slightly better" for items meeting thresholds
    - Personal preferences (Oxford comma, etc.)
    - Different but valid organizational patterns
    
    Items that meet all thresholds should receive NO suggestions.
```

### The "Bar vs Polish" Distinction

| Category | Definition | Action |
|----------|------------|--------|
| **Bar** (threshold) | Minimum acceptable quality | Must meet to pass |
| **Polish** | Nice-to-have improvements | Optional, don't block |

Validators should clearly separate these and ONLY flag bar issues for items otherwise meeting thresholds.

---

## Default Values for Skipped Phases

> **Critical Principle**: When phases are conditional, downstream steps may reference undefined variables.

### The Problem

```yaml
# This FAILS when quick-approval runs instead of detailed-analysis
- id: "synthesize-report"
  prompt: |
    Review: {{quality_results}}     # undefined if Phase 4 skipped!
    Tools: {{tool_analysis}}        # undefined if Phase 5 skipped!
```

### The Solution

Set default values BEFORE conditional phases:

```yaml
# Phase 2.75: Set defaults (always runs)
- id: "set-default-quality-results"
  type: "bash"
  command: |
    echo "_Quality analysis not performed - all items met thresholds._"
  output: "quality_results"

- id: "set-default-tool-analysis"
  type: "bash"
  command: |
    echo "_Tool analysis not performed - all items have explicit declarations._"
  output: "tool_analysis"

# Phase 4: Overwrites default if it runs
- id: "detailed-quality-analysis"
  condition: "{{quality_classification.requires_llm_analysis}} == true"
  output: "quality_results"  # Overwrites the default
  # ...

# Phase 6: Safe to reference - always has a value
- id: "synthesize-report"
  prompt: |
    Review: {{quality_results}}     # Has default OR detailed analysis
    Tools: {{tool_analysis}}        # Has default OR detailed analysis
```

### Dependency Chain

Ensure proper `depends_on` ordering:

```yaml
- id: "detailed-quality-analysis"
  depends_on: ["set-default-quality-results"]  # Default set first
  condition: "{{...}} == true"
  output: "quality_results"

- id: "synthesize-report"
  depends_on: 
    - "set-default-quality-results"      # Ensures default exists
    - "detailed-quality-analysis"         # Waits for conditional step
```

---

## The Complete Validation Loop

```
┌─────────────────────────────────────────────────────────────────────┐
│                 DOMAIN VALIDATOR LIFECYCLE                          │
└─────────────────────────────────────────────────────────────────────┘

Phase 1: CREATE
├── Consult domain expert agent first (understand the domain)
├── Write initial recipe from documentation + intuition
├── Define EXPLICIT PASS THRESHOLDS (what means "good enough")
├── Include deterministic classification BEFORE LLM phases
└── Define severity levels (likely wrong at this stage!)

Phase 2: TEST (Three-Repo Pattern)
├── Run on EXEMPLAR repo (known-good) → Should PASS (clean!)
├── Run on REAL-WORLD repo → Should find some issues
└── Run on EXPERIMENTAL repo → Should find boundary cases

Phase 3: VERIFY
├── For each finding, trace to actual code
├── Use LSP: findReferences, hover, incomingCalls
├── Document evidence for each rule
├── Identify FALSE findings (both positives and negatives)
└── Verify threshold boundaries ("barely pass" vs "barely fail")

Phase 4: FIX
├── Remove/downgrade overstated rules (ERROR → WARNING)
├── Upgrade understated rules (SUGGESTION → ERROR)
├── Reword misleading messages
├── Add "not an issue" guidance to LLM prompts
└── Update documentation if code differs from docs

Phase 5: RE-TEST
├── Run on all 3 repos again
├── Confirm false positives removed
├── Confirm PASS threshold gives clean bill of health
└── Confirm real issues still caught

Phase 6: RESULT-VALIDATE
├── Use recipes:result-validator for objective assessment
├── Criteria: accuracy, completeness, actionability
├── Verify edge cases at threshold boundaries
└── Ship or iterate
```

---

## The Three-Repo Testing Pattern

Different repositories reveal different types of issues:

| Repo Type | Purpose | What It Catches |
|-----------|---------|-----------------|
| **Exemplar** | Known-good reference implementation | False positives (should pass cleanly) |
| **Real-world** | Typical usage with natural drift | True positives, edge cases |
| **Experimental** | Boundary cases, intentional deviations | Severity misclassification |

### Threshold Boundary Testing

Create test fixtures that probe the exact threshold boundaries:

| Test Fixture | Purpose | Expected Result |
|--------------|---------|-----------------|
| `just-passes` | Meets ALL thresholds minimally | `good` (PASS) |
| `just-fails-trigger` | Missing trigger ONLY | `polish` (not needs_work) |
| `just-fails-examples` | Missing examples ONLY | `polish` (not needs_work) |
| `fails-both` | Missing trigger AND examples | `needs_work` |
| `structural-error` | Has syntax/structural issues | `critical` |

This ensures the classification logic is correctly calibrated at the boundaries.

### Example from validate-agents

| Repo | Expected | Actual | Insight |
|------|----------|--------|---------|
| `amplifier-bundle-recipes` | PASS | ✅ **PASS** | Quick-approval path worked! |
| `amplifier-foundation` | Minor suggestions | ✅ 8 good, 8 polish | Correct differentiation |
| `amplifier-bundle-shadow` | Some warnings | ⚠️ PASS WITH WARNINGS | Bundle.yaml descriptions noted |

---

## Result Validation Checkpoint

After recipe-author creates or updates a validator, **MUST** validate against original intent:

```yaml
# Example: Validating the validator
- Use recipes:result-validator
- Provide: The recipe output + original intent
- Expect: PASS/FAIL with evidence
```

### Criteria for Shipping

| Criterion | Required |
|-----------|----------|
| Zero false positives on exemplar repo | ✅ Yes |
| Catches known real issues | ✅ Yes |
| Severity correctly classified | ✅ Yes |
| Actionable remediation guidance | ✅ Yes |
| **Has explicit PASS thresholds** | ✅ Yes |
| **Has deterministic classification** | ✅ Yes |
| **Has "clean bill of health" path** | ✅ Yes |
| **Threshold boundaries tested** | ✅ Yes |
| Consistent scoring format | Nice to have |

---

## Documentation Alignment

When validators find discrepancies between documentation and code:

> **Rule**: Update documentation to match code, NOT the other way around.

### The Pattern

```
1. Validator assumes X based on documentation
2. Code verification shows behavior is Y
3. Update validator rule to match code (Y)
4. ALSO update documentation to match code (Y)
5. Commit both: validator fix + doc fix
```

### Example from Bundle Validator

**Finding**: Documentation said `context.include` is only for behaviors.

**Code Reality**: Supported everywhere, but has different **composition semantics** (accumulates) vs `@mentions` (in instruction which replaces).

**Actions Taken**:
1. Fixed validator to not flag `context.include` in root bundles as error
2. Updated BUNDLE_GUIDE.md to explain the **semantic difference**
3. Both committed together for consistency

---

## Exemplar Recipes

Reference these as working examples:

### Agent Validation (Primary Exemplar)

**Recipe**: `@foundation:recipes/validate-agents.yaml`

The **gold standard** for domain validators, demonstrating:
- ✅ Deterministic quality classification (Phase 2.5)
- ✅ Explicit PASS thresholds (trigger + examples + tools + 100 chars)
- ✅ Conditional execution (skip LLM when all agents good)
- ✅ Quick-approval path (uses claude-haiku for speed)
- ✅ "Not an issue" guidance in LLM prompts
- ✅ Default values for skipped phases
- ✅ Threshold boundary test fixtures

**Key patterns to study:**
```yaml
# Quality classification (deterministic Python)
- id: "quality-classification"
  type: "bash"
  command: |
    python3 << 'EOF'
    # Explicit thresholds - code, not LLM judgment
    thresholds = {
        "min_description_length": 100,
        "requires_trigger": True,
        "requires_examples": True,
        "requires_explicit_tools": True
    }
    # ... classification logic ...
    EOF

# Conditional execution
- id: "quick-approval"
  condition: "{{quality_classification.requires_llm_analysis}} == false"

# "Not an issue" guidance
- prompt: |
    ## What is NOT an issue:
    - Minor style differences
    - Personal preferences
    - "Could be slightly better" for passing items
```

### Single Bundle Validation

**Recipe**: `@foundation:recipes/validate-bundle.yaml`

Validates a single bundle file for:
- Structural requirements (YAML syntax, required fields)
- Convention compliance (naming, organization)
- Common gotchas (anti-patterns, mistakes)

### Repository-Wide Validation (Second Gold Standard)

**Recipe**: `@foundation:recipes/validate-bundle-repo.yaml`

The **second gold standard** for domain validators, demonstrating all the patterns from validate-agents plus infrastructure handling:

- ✅ Graceful degradation when `amplifier_foundation` unavailable
- ✅ Domain-specific structural thresholds (loads + entry point + no orphans)
- ✅ Environment check phase (Phase 0)
- ✅ Deterministic quality classification (Phase 2.5)
- ✅ Conditional execution (skip LLM when all bundles good)
- ✅ Quick-approval path (uses claude-haiku for speed)
- ✅ "Not an issue" guidance in LLM prompts
- ✅ Default values for skipped phases

**Key patterns to study:**
```yaml
# Environment check with fallback
- id: "check-environment"
  type: "bash"
  command: |
    python3 << 'EOF'
    try:
        from amplifier_foundation import BundleRegistry
        result["foundation_available"] = True
    except ImportError:
        result["foundation_available"] = False
    EOF

# Conditional validation based on infrastructure
- id: "validate-with-registry"
  condition: "{{env_check.foundation_available}} == true"
  
# Structural thresholds for bundles (different from agents!)
thresholds = {
    "all_bundles_load": True,
    "has_entry_point": True,
    "no_orphan_agents": True,
    "references_resolve": True
}
```

**When to use as exemplar:**
- Building validators that need infrastructure (registries, APIs, services)
- Validating structural/compositional domains (vs behavioral/content domains)
- Domains where "loads without error" is the primary success criterion

### Using the Exemplars

```bash
# Validate agents in a bundle
amplifier tool invoke recipes operation=execute \
  recipe_path=foundation:recipes/validate-agents.yaml \
  context='{"bundle_path": "/path/to/bundle-repo"}'

# Validate a single bundle
amplifier tool invoke recipes operation=execute \
  recipe_path=foundation:recipes/validate-bundle.yaml \
  context='{"bundle_path": "/path/to/bundle.yaml"}'

# Validate entire repository
amplifier tool invoke recipes operation=execute \
  recipe_path=foundation:recipes/validate-bundle-repo.yaml \
  context='{"repo_path": "/path/to/bundle-repo"}'
```

---

## Anti-Patterns to Avoid

### ❌ Encoding Assumptions Without Verification

```yaml
# BAD: Assumed from documentation
- prompt: "Flag ERROR if inline agents are used (deprecated)"

# GOOD: Verified against code
- prompt: "Both inline and include agent patterns are valid (bundle.py:_parse_agents)"
```

### ❌ Overstating Conventions as Errors

```yaml
# BAD: Convention treated as error
findings:
  - severity: ERROR
    message: "Not using DRY pattern"

# GOOD: Convention correctly classified
findings:
  - severity: WARNING
    message: "Consider DRY pattern (RECOMMENDED, not required)"
```

### ❌ Validating Against Docs Instead of Code

```yaml
# BAD: "Documentation says X"
- prompt: "Check if bundle follows the pattern described in BUNDLE_GUIDE.md"

# GOOD: "Code enforces X"
- prompt: "Check if bundle has required fields that Bundle.from_dict() validates"
```

### ❌ Skipping Result Validation

```
# BAD: Ship after initial testing
Create recipe → Test once → Ship

# GOOD: Full validation loop
Create → Test 3 repos → Verify with code → Fix → Re-test → Result-validate → Ship
```

### ❌ No PASS Threshold

```yaml
# BAD: Always finds something
- prompt: "Analyze for any possible improvements"
# Result: Users never get a clean bill of health

# GOOD: Explicit threshold
- prompt: |
    Items meeting ALL thresholds should receive NO suggestions:
    - Description ≥ 100 characters ✓
    - Has strong trigger word ✓
    - Has at least one example ✓
```

### ❌ LLM Classifies Severity

```yaml
# BAD: LLM decides what's critical
- prompt: "Classify the severity of each finding"
# Result: Inconsistent between runs

# GOOD: Code classifies, LLM elaborates
- id: "classification"
  type: "bash"  # Deterministic!
  command: "python3 classify.py"
  
- id: "elaboration"
  condition: "{{classification.needs_analysis}}"
  prompt: "Explain why these items need work: {{items}}"
```

### ❌ JSON Interpolation Gotchas

```python
# BAD: ast.literal_eval can't handle JSON's true/false
structural = ast.literal_eval('''{{structural_results}}''')
# Fails because JSON uses lowercase true/false, Python uses True/False

# GOOD: json.loads handles JSON booleans correctly
structural = json.loads('''{{structural_results}}''')
# Works because json.loads expects JSON's true/false
```

### ❌ Multiline Content in JSON

```python
# BAD: Including fields with multiline strings breaks interpolation
agent_result["description"] = "Line 1\nLine 2\nLine 3"  # Multiline!
print(json.dumps(results))
# Later: structural = json.loads('''{{structural_results}}''')  # BREAKS!

# GOOD: Remove multiline fields before JSON output
for field in ["frontmatter", "meta", "description"]:
    agent_result.pop(field, None)  # Remove problematic fields
print(json.dumps(results))
```

---

## Quick Reference: Creating a New Domain Validator

### Checklist

```
[ ] Phase 1: Domain Understanding
    [ ] Consult domain expert agent
    [ ] List all rules (code-enforced, conventions, best practices)
    [ ] For each rule, note evidence source
    [ ] DEFINE EXPLICIT PASS THRESHOLDS

[ ] Phase 2: Recipe Creation
    [ ] Use recipe-author agent (don't write YAML directly)
    [ ] Follow 6-phase architecture (with Phase 2.5 classification)
    [ ] Add deterministic quality classification step
    [ ] Add conditional execution for LLM phases
    [ ] Add default values for skipped phases
    [ ] Define clear severity for each check
    [ ] Include "not an issue" guidance in LLM prompts

[ ] Phase 3: Code Verification
    [ ] For each ERROR, find code enforcement point
    [ ] For each WARNING, confirm no code enforcement
    [ ] Document evidence in recipe comments

[ ] Phase 4: Three-Repo Testing
    [ ] Test on exemplar (should PASS with clean bill of health)
    [ ] Test on real-world (should find issues)
    [ ] Test on experimental (should handle edge cases)
    [ ] Create threshold boundary test fixtures

[ ] Phase 5: Fix and Re-test
    [ ] Remove false positives
    [ ] Adjust severity based on evidence
    [ ] Verify threshold boundaries work correctly
    [ ] Update related documentation

[ ] Phase 6: Result Validation
    [ ] Run result-validator
    [ ] Confirm: accuracy, completeness, actionability
    [ ] Confirm: clean bill of health for passing items
    [ ] Ship or iterate
```

### Time Investment

| Phase | Typical Time | Notes |
|-------|--------------|-------|
| Domain understanding | 1-2 hours | Consult experts, read code |
| Initial recipe | 30-60 min | Use recipe-author, include new patterns |
| Code verification | 2-4 hours | The critical investment |
| Three-repo testing | 30-60 min | Include threshold boundary tests |
| Fix and re-test | 1-3 hours | Depends on findings |
| Result validation | 15-30 min | Quick checkpoint |
| **Total** | **6-11 hours** | For production-quality validator |

---

## Summary

Building accurate domain validators requires:

1. **Code verification first** - Don't trust assumptions
2. **Correct severity classification** - ERROR vs WARNING vs SUGGESTION
3. **Explicit PASS thresholds** - Know when to stop finding things
4. **Deterministic classification before LLM** - Reduce variance
5. **Conditional execution** - Skip LLM for passing items
6. **"Not an issue" guidance** - Tell LLM what to ignore
7. **Three-repo testing** - Exemplar + real-world + experimental
8. **Threshold boundary testing** - "Barely pass" vs "barely fail"
9. **The validation loop** - Create → Test → Verify → Fix → Re-test → Validate
10. **Documentation alignment** - Update docs when code differs

The investment pays off in:
- Consistent guidance across the ecosystem
- Reduced support burden
- Faster onboarding
- Codified expertise that scales
- **User trust** - Clean bills of health for well-maintained code

---

## See Also

- `@foundation:recipes/validate-agents.yaml` - **Primary exemplar** for new validator pattern
- `@foundation:recipes/validate-bundle.yaml` - Single bundle validation exemplar
- `@foundation:recipes/validate-bundle-repo.yaml` - Repository validation exemplar
- `@foundation:test-fixtures/agents/` - Threshold boundary test fixtures
- `@foundation:docs/BUNDLE_GUIDE.md` - Bundle authoring guide (updated based on validator findings)
- `@recipes:docs/RECIPE_SCHEMA.md` - Recipe authoring reference
