# Issue Handling Process

This document captures the systematic approach for handling software issues, derived from real-world resolutions across multiple projects.

---

## Core Principles

### 1. **Investigation Before Action**

**Never start coding until you understand the complete picture.**

- Use specialized agents to gather information (explorer, amplifier-expert, code-intel)
- Trace the actual code paths involved
- Compare working vs broken scenarios
- Identify the EXACT divergence point

**Anti-pattern:** Jump to fixes based on assumptions  
**Correct pattern:** Investigate → understand → design → implement → test

### 2. **Evidence-Based Testing**

**Define specific, measurable proof requirements BEFORE testing.**

Each fix must have concrete evidence it works:
- "Command exits with code 0" ✓
- "No error message X appears in output" ✓
- "Output contains actual AI-generated content" ✓
- "Specific keywords present in result" ✓

**Anti-pattern:** "I think it works"  
**Correct pattern:** "Here's the evidence it works: [specific outputs]"

### 3. **User Time is Sacred**

**The user's time is more valuable than tokens or agent time.**

Before presenting work to the user:
- Complete the investigation fully
- Test the fix thoroughly
- Gather all evidence
- Have a complete story, not partial findings

**Only bring design/philosophy decisions to the user, not missing research.**

### 4. **Follow Your Reasoning to Its Conclusion**

**If your analysis establishes a premise, trace it all the way through.**

When you've built the logical case for a position — "X has negligible cost, provides clear value, and the data is lost forever if not captured now" — follow that reasoning to its natural endpoint. Don't stop short and present half-conclusions that require the user to connect the final dots.

Common failure mode: Arguing that a feature is non-destructive, low-overhead, and universally useful... then suggesting it should be configurable. If there's truly no cost, there's no reason for a toggle. The toggle is complexity that contradicts your own analysis.

**Anti-pattern:** "X has no real cost and provides value. Consider making X optional."
**Correct pattern:** "X has no real cost and provides value. X should always be on — a config option would be dead complexity."

**The test:** After writing a recommendation, ask: "Does my conclusion follow from my premises? Or am I hedging on something I've already resolved?"

---

## The Process (6-Phase Workflow)

### Phase 1: **Reconnaissance**

**Goal:** Understand what's broken and what's involved.

**Actions:**
1. Read the issue carefully - what's the user scenario?
2. Check recent commits in potentially affected repos
3. Delegate investigation to appropriate agents:
   - `amplifier:amplifier-expert` - "What repos/modules are involved?"
   - `foundation:explorer` - "How does this code path work?"
   - `lsp-python:python-code-intel` - "What calls what?"

**Deliverable:** Complete understanding of the problem and affected components.

**Example scenario:**
- Read issue: recipe execution fails with "No providers mounted"
- Checked recent commits in relevant repos
- Used explorer to compare working vs broken code paths
- Found exact divergence: specific module bypassed required wrapper

---

### Phase 2: **Root Cause Analysis**

**Goal:** Identify the EXACT cause, not just symptoms.

**Actions:**
1. Trace the complete flow for both working and broken scenarios
2. Find the divergence point (where do they split?)
3. Understand WHY the divergence exists
4. Verify your hypothesis with code inspection

**Deliverable:** Specific file:line_number where the bug lives.

**Red flags:**
- "I think this might be the issue" - not specific enough
- "Probably something in this function" - keep narrowing
- "Could be related to..." - find the exact relationship

**Example scenario:**
- Initial hypothesis: Config not syncing (WRONG)
- Deeper investigation: Data not passed to handler (WRONG)
- Final discovery: Specific file bypasses required wrapper (CORRECT)
- Evidence: Working path has wrapper, broken path doesn't

---

### Phase 3: **GATE 1 - Investigation Approval**

**Goal:** Get user approval on approach before implementing.

**Present to user:**
1. Clear problem statement
2. Root cause with evidence (file:line references)
3. Proposed fix with rationale
4. What will be tested and how

**Wait for explicit approval before proceeding.**

**Example scenario:**
- Initially proposed wrong fix (sync conditions)
- User asked clarifying questions about architecture
- Re-investigated with correct understanding
- Found real root cause (missing wrapper)
- Presented complete analysis with code references

---

### Phase 4: **Implementation**

**Goal:** Make the fix, commit locally, prepare for testing.

**Actions:**
1. Implement the fix
2. Run `python_check` to verify syntax
3. **Commit locally** (before shadow testing)
   - Creates snapshot for testing
   - Enables easy rollback if needed
   - Documents what changed
4. Create related issues for out-of-scope work discovered

**Commit message format:**
```
type: short description

Detailed explanation of:
- Root cause
- Why it happened  
- What the fix does
- Impact

Fixes: [issue-tracker]#[issue-number]

🤖 Generated with [Amplifier](https://github.com/microsoft/amplifier)
Co-Authored-By: Amplifier <240397093+microsoft-amplifier@users.noreply.github.com>
```

**Example scenario:**
- Implemented required wrapper in target module
- Verified syntax with python_check
- Committed locally with descriptive message
- Created new issue for future parity work discovered

---

### Phase 5: **Shadow Testing**

**Goal:** Prove the fix works with evidence.

**Actions:**
1. Create shadow environment with local changes
2. Install Amplifier from local source
3. Reproduce the original issue scenario
4. Verify all evidence requirements
5. If tests fail → investigate → fix → re-test (loop until working)

**Evidence collection:**
- Capture command outputs (before/after)
- Note exit codes
- Grep for specific error messages
- Verify functional correctness (not just "no error")

**Don't present to user until ALL evidence requirements pass.**

**Example scenario:**
- First fix attempt - shadow test FAILED
- Re-investigated, found real cause
- Second fix - shadow test PASSED
- All evidence requirements verified
- Collected before/after comparison

---

### Phase 6: **Final Validation & Push Approval**

**Goal:** Complete all testing and get user approval to push.

**Actions:**

1. **Run Independent Smoke Test (FINAL DEFENSE):**
   - Execute shadow-smoke-test in fresh environment
   - Verify fix works from user perspective
   - Capture objective PASS/FAIL verdict
   - This is the LAST validation before seeking push approval

2. **GATE 2 - Present Complete Solution:**
   - Summary of fix with file:line references
   - Complete shadow test results with evidence
   - Before/after comparison
   - Independent smoke test results (PASS verdict)
   - Commit hash ready to push
   
   **Wait for explicit approval before pushing.**

3. **After approval:**
   - Push via git-ops agent (handles rebasing, quality commit messages)
   - Comment on issue with fix details and resolution steps
   - Close issue with comment about how users can get the fix
   - Update any related documentation

**IMPORTANT:** If any changes occur after the smoke test (fixing issues it found, user feedback iterations), the smoke test MUST run again before requesting push approval.

**Example workflow:**
- Shadow test passed → Run smoke test → PASS
- Present complete solution with all evidence
- User approved
- git-ops pushed (rebased onto 3 new commits)
- Commented on issue with root cause and fix explanation
- Closed issue with user resolution steps

---

## Investigation Patterns

### Pattern 1: **Parallel Agent Dispatch**

For complex issues, dispatch multiple agents in parallel:

```
[task agent=foundation:explorer] - Survey the code paths
[task agent=amplifier:amplifier-expert] - Consult on architecture  
[task agent=lsp-python:python-code-intel] - Trace call hierarchies
```

Different perspectives reveal different aspects of the problem.

### Pattern 2: **Compare Working vs Broken**

Always find a working scenario and compare:
- What does the working path do that the broken path doesn't?
- Where do they diverge?
- What's different about the setup/config?

**Example:** `amplifier run` works, `tool invoke` doesn't → compare session creation flows

### Pattern 3: **Follow the Data**

Trace where critical data (config, providers, modules) flows:
- Where does it originate? (settings.yaml, bundle.md, CLI flags)
- Where does it get transformed? (merge functions, override logic)
- Where does it get consumed? (session creation, module loading)
- Where does it get lost? (conditional guards, missing handoffs)

---

## Testing Requirements

### Shadow Environment Testing

**When to use:**
- Testing fixes in amplifier-app-cli, amplifier-core, or amplifier-foundation
- Need to test with local uncommitted changes
- Want to test multi-repo changes together

**Workflow:**
```bash
# 1. Commit locally first (creates snapshot)
git commit -m "fix: description"

# 2. Shadow test via shadow-operator agent
# Pass specific evidence requirements

# 3. If tests pass → GATE 2 approval
# If tests fail → investigate, fix, re-commit, re-test
```

**Evidence requirements template:**
```markdown
1. **Specific error disappears:**
   - Before: "Error message X"
   - After: No error message X in output
   - Verify: grep output for error string

2. **Functional behavior works:**
   - Execute: specific command
   - Expected: specific result
   - Verify: exit code, output content, side effects

3. **End-to-end correctness:**
   - Scenario: user workflow
   - Proof: specific content in output
   - Verify: keywords, data presence, state changes
```

**End-to-End Evidence (User Perspective):**

Testing should match how a user would actually encounter and use the feature. If the issue was "tool X failed to load", evidence should show tool X working in a realistic scenario, not just unit tests.

**Examples:**

```markdown
**If issue: "Agent failed to load tool-web"**
- Evidence: Run task that exercises web search as user would
- Command: `amplifier run "search the web for Python tutorials"`
- Verify: Search results returned, no tool loading errors in output
- Why: Shows the tool loads AND functions in real usage

**If issue: "Recipe execution fails with 'No providers mounted'"**
- Evidence: Execute actual recipe workflow from user perspective
- Command: `amplifier tool invoke recipes operation=execute recipe_path=...`
- Verify: Recipe completes, child sessions spawn correctly, deliverable produced
- Why: Tests the complete flow users experience, not just initialization

**If issue: "Command Y errors with 'module not found'"**
- Evidence: Run command Y successfully from fresh environment
- Command: `amplifier Y [args]`
- Verify: Command exits 0, produces expected output, no import errors
- Why: Confirms the fix works in conditions users will encounter

**If issue: "Performance degradation in long sessions"**
- Evidence: Run representative long session scenario
- Setup: Create session with N events/operations
- Measure: Time between events at start vs end
- Verify: Performance remains within acceptable bounds
- Why: Reproduces actual user pain point
```

**Key principle:** Evidence should demonstrate the fix from the user's perspective, not just from a developer testing perspective. If a user reported "X doesn't work when I do Y", your evidence should show "I did Y and X worked".

### Independent Validation

**After pushing, run shadow-smoke-test:**
- Fresh environment (no local sources)
- Pull from GitHub (tests what users will get)
- Run same test scenario
- Verify fix works in production conditions

**This catches:**
- Missing commits (forgot to push something)
- Environment-specific issues
- Dependency problems

---

## Out-of-Scope Work

### When Discovery Reveals Additional Work

**Pattern:** Create a new issue immediately, don't expand scope.

**Example scenario:**
- While investigating an issue, found that command lacks certain flags
- This is enhancement work, not part of the bug fix
- Created new issue for future parity work
- Continued with minimal fix for original issue

**Request permission from user:**
> "I've discovered [related work] which is out of scope for this issue. Should I create a new issue for [description]?"

**Benefits:**
- Keeps issues focused and closeable
- Documents all discovered work
- Allows separate prioritization
- Prevents scope creep

---

## Issue Resolution

### Closing Issues

**Include in the closing comment:**
1. What was done (commit reference)
2. How the fix was verified
3. How users can get the fix

**Template:**
```markdown
Fixed in commit [hash].

**Root cause:** [brief explanation]

**Fix:** [what changed]

**Verified:** [testing evidence]

**Resolution for users:** 
Since this is a live system (no releases), users should run:
```bash
amplifier reset --remove cache -y
amplifier provider install <provider-name>  # e.g., anthropic, openai
```

**IMPORTANT:** Provider modules must be **installed** (not just configured). The `reset` command clears the cache and reinstalls Amplifier, but does NOT reinstall provider modules. Users must explicitly run `amplifier provider install` after reset.

**For broken updates specifically:**
Mention that `amplifier update` won't work because the update mechanism itself is broken, so users need `reset`.

---

## Common Pitfalls (Learned the Hard Way)

### Pitfall 1: **Assuming Understanding Too Early**

**Example scenario:**
- First assumption: Config not syncing → WRONG
- Second assumption: Data not passed to handler → WRONG
- Third investigation: Found actual divergence (missing wrapper) → CORRECT

**Lesson:** Keep investigating until you can point to the exact line of code causing the issue.

### Pitfall 2: **Incomplete Fixes**

**Example scenario:**
- First fix: Changed sync conditions → Still failed
- Second fix: Added data to handler → Still failed
- Complete fix: Wrapped component with required layer → SUCCESS

**Lesson:** A fix isn't complete until shadow testing proves it works end-to-end.

### Pitfall 3: **Skipping Independent Validation**

**After pushing, always run shadow-smoke-test:**
- Tests the PUSHED code (not your local changes)
- Catches missing commits, environment issues
- Provides objective PASS/FAIL verdict

**Lesson:** Don't rely solely on your own testing.

### Pitfall 4: **Not Checking Existing Precedent**

Before evaluating whether a change is appropriate, ask: "Does the codebase already do this?" Finding that the kernel's `events.py` already had ~10 unused vocabulary constants (defined but never emitted) completely reframed an analysis — what looked like "adding something new" was actually "following the established pattern." Existing precedent legitimizes or undermines a proposal far more effectively than abstract reasoning.

**Lesson:** Ask "what's the existing precedent?" before designing or evaluating solutions.

### Pitfall 5: **Not Testing the Right Layer**

PR authors may verify a bug correctly but at the wrong abstraction level — checking an intermediate Python attribute that shows `None` when the underlying SDK correctly resolves the value. Always verify claimed behavior at the *end behavior* layer.

**Lesson:** Test what the user actually experiences, not intermediate state.

### Pitfall 6: **Attributing Test Failures to a PR Without Checking Main**

Before rejecting a PR for test failures, run the same failing tests on main. Failures may be pre-existing. This prevents both false blame (rejecting good PRs) and false confidence (assuming a PR's tests are clean because the same failures exist on main).

**Lesson:** Always baseline test failures against main.

### Pitfall 7: **Not Asking What Each Layer Can Know**

When evaluating where a change belongs, ask: "Does this layer have the information needed?" The kernel can't emit `session:end` because `execute()` returns per-turn and only the caller knows when a session is truly done. This "what can this layer know?" test is a precise heuristic for layer-boundary decisions.

**Lesson:** If a layer doesn't have the information, it can't own the behavior.

### Pitfall 8: **Averaging Expert Disagreement**

When expert agents disagree, evaluate the *reasoning quality* of each position — don't average or defer to the one that seems more authoritative. When foundation-expert and core-expert disagreed on component placement, the two-implementation-rule argument was structurally stronger regardless of which expert made it.

**Lesson:** When experts disagree, the stronger reasoning wins, not the louder voice.

### Pitfall 9: **Not Comparing Similar Code Paths**

**Example scenario:**
- Two commands should have same behavior
- Finding they diverge reveals the bug location immediately
- The working path shows what the broken path is missing

**Lesson:** When something works in one place but not another, compare the paths systematically.

---

## Agent Usage Strategy

### Investigation Phase

| Agent | When to Use | What They Provide |
|-------|-------------|-------------------|
| `amplifier:amplifier-expert` | Always first for Amplifier issues | Ecosystem knowledge, architecture context |
| `foundation:explorer` | Code path tracing, comparison | Structured survey of code flows |
| `lsp-python:python-code-intel` | Call hierarchy, definitions | Deterministic code relationships |
| `foundation:bug-hunter` | When you have errors/stack traces | Hypothesis-driven debugging |

### Implementation Phase

| Agent | When to Use | What They Provide |
|-------|-------------|-------------------|
| `foundation:zen-architect` | Design decisions, architectural review, trade-off analysis | Philosophy compliance, design patterns, system-wide consistency |
| `foundation:security-guardian` | Security-sensitive changes (auth, data access, API boundaries) | Security review, vulnerability analysis, best practices |
| `foundation:modular-builder` | Coding implementation | Code generation |

**When to consult zen-architect:**
- Fix involves architectural changes or patterns
- Multiple solution approaches with trade-offs
- Changes affect public APIs or interfaces
- Design decisions that impact maintainability
- Need validation that fix aligns with project philosophy

**When to consult security-guardian:**
- Changes touch authentication or authorization
- Handling user input or external data
- File system operations or path handling
- API endpoints or external integrations
- Data validation or sanitization logic

### Testing Phase

| Agent | When to Use | What They Provide |
|-------|-------------|-------------------|
| `foundation:test-coverage` | Comprehensive testing strategy needed | Test planning, coverage analysis, edge case identification |
| `shadow-operator` | Shadow environment testing | Isolated test execution |
| `shadow-smoke-test` | Independent validation | Objective PASS/FAIL verdict |

**When to consult test-coverage:**
- Complex fix requiring multi-layered testing
- Need to identify edge cases and failure modes
- Testing strategy for integration/E2E scenarios
- Validation that evidence requirements are sufficient
- Regression testing planning

### Finalization Phase

| Agent | When to Use | What They Provide |
|-------|-------------|-------------------|
| `foundation:git-ops` | Always for commits/pushes | Quality messages, safety protocols |

### Delegation Discovers What Direct Work Misses

Direct tool calls (reading files, grepping) consume tokens in YOUR context. Delegation to expert agents is not just efficient—it surfaces insights you would miss.

**Comparative example from PR review:**

| Approach | Files Read | Tokens Consumed | Insights Found |
|----------|------------|-----------------|----------------|
| Direct investigation | 8 | ~15,000 | Formatting bug only |
| Delegated investigation | 0 | ~1,000 (summaries) | Formatting bug + token cost concern + propagation mechanism + architectural issue |

**Why delegation found more:**
- `amplifier:amplifier-expert` had MODULES.md @-mentioned, knew token implications
- `foundation:foundation-expert` had bundle composition docs, explained propagation mechanics
- Direct file reading would have required knowing WHICH docs to read

**Lesson:** Expert agents carry @-mentioned documentation you don't have. They find architectural issues because they have architectural context loaded.

---

## Process Checklist

Use this checklist for every issue:

### Investigation
- [ ] Read issue and understand user scenario
- [ ] Check recent commits in affected repos
- [ ] Delegate investigation to appropriate agents
- [ ] Trace code paths (working vs broken if applicable)
- [ ] Identify exact root cause with file:line references
- [ ] **GATE 1:** Present investigation to user for approval

### Implementation
- [ ] Implement fix based on approved design
- [ ] Run `python_check` to verify syntax
- [ ] Commit locally with detailed message
- [ ] Create new issues for any out-of-scope work discovered

### Testing
- [ ] Define specific evidence requirements
- [ ] Create shadow environment with local changes
- [ ] Run complete end-to-end test
- [ ] Verify ALL evidence requirements pass
- [ ] Collect before/after comparison
- [ ] If tests fail → investigate → fix → re-test (don't present until passing)
- [ ] **GATE 2:** Present complete tested solution to user for approval

### Finalization
- [ ] Push via git-ops agent (handles rebasing, quality)
- [ ] Run independent shadow-smoke-test validation
- [ ] Comment on issue with fix details and evidence
- [ ] Close issue with resolution steps for users
- [ ] Update process documentation with learnings

---

## Case Study: Architectural Divergence (CLI Command Parity)

### Timeline
1. **Initial investigation (WRONG):** Thought provider config wasn't syncing
2. **User clarification:** Explained `tool invoke` creates fresh session, should have parity with `run`
3. **Re-investigation:** Found provider sources not passed to prepare (STILL WRONG)
4. **Shadow test FAILED:** Fix didn't work, providers still not found
5. **Deep investigation:** Compared session creation flows between run.py and tool.py
6. **Discovery:** tool.py bypassed AppModuleResolver wrapper
7. **Correct fix:** Added wrapper to tool.py
8. **Shadow test PASSED:** All evidence requirements met
9. **User approval → Push:** git-ops pushed to main
10. **Independent validation:** shadow-smoke-test confirmed fix works

### Key Learnings

**What went right:**
- Used multiple agents in parallel for investigation
- Created new issue for out-of-scope work (didn't expand scope)
- Defined specific evidence requirements before testing
- Didn't present to user until fix was proven working

**What went wrong initially:**
- Jumped to fix without complete understanding (twice!)
- Should have compared session creation flows earlier
- Could have saved iteration by being more thorough upfront

**The turning point:**
User asked: "How does `tool invoke` even work? What's the parent session?"
This forced me to re-think the architecture completely, leading to the correct fix.

**Lesson:** When the user asks clarifying questions, it's a signal you don't fully understand yet. Use it as a prompt to investigate deeper.

---

## Case Study: State Management Across Lifecycle Operations

### Problem
tool-web module failed with "No module named 'aiohttp'" after upgrade/reset, despite dependency being declared in pyproject.toml.

### Discovery Process
1. Confirmed dependency was declared correctly
2. Traced recent changes - found fast-startup optimization (commit 2c2d9b4)
3. Identified install-state.json tracking mechanism
4. Realized state file location matters (~/.amplifier/ vs cache/)

### Root Cause
Install state tracking survived cache clearing, causing ModuleActivator to skip reinstallation.

### Key Learnings

**Performance optimizations create state:**
- Fast-startup optimization added install-state.json
- State persisted across resets (wrong location)
- Created "phantom installed" condition

**Lesson:** When adding caching/state tracking:
- Document what state files are created and where
- Ensure cleanup commands handle ALL related state
- Test the upgrade/reset path specifically
- Co-locate state with the data it tracks when possible

**State file location matters:**
- Cache: `~/.amplifier/cache/` (cleared during reset)
- Install state: `~/.amplifier/install-state.json` (survived reset)
- This mismatch caused the bug

**Lesson:** State tracking files should live in cache/ if they track cached data.

---

## Case Study: Performance Optimization with Constraints

### Problem
Report of 142x performance degradation in long sessions (0.5s → 79s gap between events).

### Special Challenges
**Reporter caveat:** Non-technical, known for misunderstandings and assumptions presented as fact.

**Response strategy:**
1. Read report as "pointers to explore" not gospel truth
2. Dispatch multiple agents for independent verification
3. Trace actual code paths, don't trust claimed flow
4. Verify every claim with code evidence

### Investigation Approach

**Parallel agent dispatch (3 agents simultaneously):**
1. **amplifier:amplifier-expert** - Architecture validation, module ownership
2. **foundation:explorer** - Actual code path tracing with file:line references
3. **foundation:bug-hunter** - Hypothesis testing with code evidence

**Why multiple agents:**
- Different perspectives (architecture, code flow, hypothesis testing)
- Independent verification (don't assume reporter is right)
- Comprehensive coverage (may find issues reporter missed)

### Root Cause Discovery

**Reporter claimed:** Provider message conversion was the bottleneck
**Actually:** hooks-logging dir() introspection on event serialization

**How we found it:**
- explorer traced actual execution path (found dir() usage)
- bug-hunter tested hypotheses systematically (confirmed serialization bottleneck)
- amplifier-expert verified claims vs reality (module ownership correct, flow slightly wrong)

### Key Learnings

**Non-technical reporters require extra validation:**
- Treat reports as starting points, not conclusions
- Verify every claimed file:line reference
- Trace actual code, don't trust described flow
- Claims may be directionally correct but technically wrong

**Lesson:** When reporter is non-technical:
- Dispatch multiple agents for independent investigation
- Don't trust claimed root causes - verify with code
- Reporter may correctly identify SYMPTOMS but misattribute CAUSE
- Use their observations as clues, not conclusions

**Multiple agents reveal ground truth:**
- explorer: "Here's the actual code path"
- bug-hunter: "Here's what the data shows"
- amplifier-expert: "Here's what the architecture says"
- Combined: Accurate picture emerges

**Lesson:** For complex issues, parallel agent dispatch provides multiple perspectives that converge on truth.

**User constraints can drive better solutions:**
- User: "Keep raw_debug on, need full contents"
- This eliminated quick workarounds (disable debug, truncate)
- Forced us to find the REAL fix (optimize serialization)
- Result: Better solution that helps everyone

**Lesson:** Constraints can lead to better fixes than quick workarounds.

---

## Case Study: Algorithm Design (False Positives)

### Problem
"Circular dependency detected" warnings for foundation, python-dev, shadow, and behaviors/sessions.yaml when loading bundles.

### Investigation Approach

**Parallel agent dispatch (3 agents):**
1. **amplifier:amplifier-expert** - Verify module ownership, check recent changes
2. **foundation:explorer** - Trace detection algorithm, map include chains
3. **foundation:bug-hunter** - Test hypotheses systematically

**Why parallel:** Different angles revealed different pieces of the puzzle.

### Discovery Process

**Initial findings:**
- All three agents independently verified the error was real (not environmental)
- Explorer traced the detection code (registry.py:318-319)
- Bug-hunter identified self-referential namespace includes as trigger
- Expert confirmed no external bundles had real circular dependencies

**Verified by checking actual bundle files:**
- python-dev explicitly comments: "must NOT include foundation (circular dep)"
- shadow has no includes at all
- Foundation's self-references use namespace:path syntax

### Root Cause

**Algorithm couldn't distinguish:**
- ❌ Inter-bundle circular (Bundle A → Bundle B → Bundle A) - should block
- ✅ Intra-bundle subdirectory (foundation → foundation:behaviors/sessions) - should allow

**Detection used simple set:** `if uri in self._loading: raise Error`

This flagged legitimate self-referential namespace patterns as circular.

### Key Learnings

**False positives need nuanced detection:**
- Simple algorithms (set membership) miss important distinctions
- Need to track WHY something appears twice (same bundle subdirectory vs different bundle)
- The "better option" (Option B) used dual tracking for semantic correctness

**Algorithm design trade-offs:**
- Option A: 3 lines, simple, works
- Option B: 20 lines, conceptually cleaner, distinguishes intra vs inter-bundle
- User chose "better option" → more code but clearer intent

**Lesson:** When presented with "simple vs correct", prefer correct. The extra complexity is worth semantic clarity.

**Validation of claims:**
- Reporter (robotdad) was technical and accurate
- Errors were real (not misunderstandings)
- Still dispatched multiple agents to verify independently
- Found the errors were false positives, not real circulars

**Lesson:** Even with technical reporters, verify claims with code. Trust but verify.

**Testing dual behavior (positive and negative cases):**
- Phase 1: Verify false positives eliminated (intra-bundle subdirectories work)
- Phase 2: Verify real circulars still caught (protection preserved)

**Lesson:** When fixing detection algorithms, test BOTH what should pass AND what should fail.

---

## Case Study: P0 Regression from Incomplete Algorithm Fix

### Problem
CRITICAL P0 regression immediately after deploying the circular dependency fix. Users completely blocked - cannot start Amplifier sessions.

**Two symptoms:**
1. Circular dependency warnings still appearing (fix didn't work)
2. NEW error: "Configuration must specify session.orchestrator" (crash)

### What We Did (Timeline)

**Fix deployment:**
- Identified circular dependency false positives
- Chose "better option" (dual tracking with _loading_base)
- Shadow tested: 11/11 tests passed ✓
- Pushed to production (commit 87e42ae)

**Immediate failure:**
- Users updated and crashed
- Cannot start sessions
- No workaround available
- Emergency investigation required

### Root Cause

**The Issue #6 fix was INCOMPLETE:**

**We identified TWO patterns:**
- ✅ Inter-bundle circular (block this)
- ✅ Intra-bundle subdirectory (allow this)

**We MISSED a THIRD pattern:**
- ❌ Namespace preload self-reference (allow this)

**The bug in our fix:**
```python
is_subdirectory = "#subdirectory=" in uri  # Only checked for fragment

if base_uri in self._loading_base and not is_subdirectory:
    raise BundleDependencyError(...)  # Missed namespace preload!
```

When amplifier-dev included "foundation" by registered name for namespace resolution, the detector saw foundation's base URI already loading but no `#subdirectory=` fragment → false circular error → foundation SKIPPED → no orchestrator config → crash.

### Key Failures in Our Process

**Failure 1: Incomplete pattern enumeration**
- Stopped at two patterns without asking: "What else?"
- Didn't map ALL the ways bundles reference each other
- Algorithm was solving 2 out of 3 cases

**Lesson:** When categorizing behaviors, enumerate EXHAUSTIVELY before implementing. Document: "This algorithm handles patterns A, B, C" and verify no pattern D exists.

**Failure 2: Testing the wrong scenario**
- Tested with foundation bundle directly
- Actual users use amplifier-dev (nested bundle)
- The failure only appeared in nested bundle → parent composition
- 11/11 tests passed but didn't cover real deployment

**Lesson:** Test in ACTUAL user scenarios, not isolated components. Ask: "How do users actually use this?" Include their bundle configurations in tests.

**Failure 3: False confidence from green tests**
- "11/11 tests passed" created confidence
- But tests only covered what we thought of
- Didn't cover the pattern we missed
- Green checkmarks ≠ complete coverage

**Lesson:** Passing tests prove what you tested, not what you didn't test. Ask: "What scenarios are NOT in our test suite?"

**Failure 4: Didn't test cascading impact**
- Fixed warnings (cosmetic issue)
- Broke orchestrator config (P0 blocker)
- Didn't validate: "What happens if includes are skipped?"
- Downstream impact not considered

**Lesson:** When fixing error handling, validate cascading effects. If detection rejects something, trace what depends on it loading successfully.

**Failure 5: Skipped actual deployment smoke test**
- Tested in shadow with foundation bundle
- Didn't test with amplifier-dev (what users actually run)
- Independent smoke test would have caught this immediately
- Our own methodology says: smoke test before GATE 2

**Lesson:** We violated our own process. Shadow test ≠ smoke test. Test in the actual configuration users deploy.

**Failure 6: "Better" wasn't complete**
- Chose Option B (dual tracking) over Option A (simple skip) for elegance
- Option B was more sophisticated but incomplete
- Option A (skip preload if already loading) would have worked for all patterns
- Sophistication without completeness = dangerous

**Lesson:** Simple and complete beats elegant and incomplete. When choosing between options, completeness is more important than conceptual clarity.

### The Cascade

```
Circular detection logic incomplete
  ↓
Foundation include flagged as circular
  ↓
Foundation bundle SKIPPED
  ↓
amplifier-dev has no orchestrator config (depends on foundation)
  ↓
Session creation crashes
  ↓
Users completely blocked (P0 incident)
```

**Impact:**
- ALL users blocked
- No workaround
- Required emergency hotfix
- Violated "don't break userspace" principle from KERNEL_PHILOSOPHY

### Emergency Response

**Rapid investigation:**
- Parallel agent dispatch (bug-hunter + explorer)
- Found missing pattern in <30 minutes
- Implemented hotfix: added is_namespace_preload check
- Pushed without normal gates (P0 exception)
- Users unblocked within 1 hour

**The correct fix:**
```python
is_namespace_preload = (
    name_or_uri in self._registry and
    self._registry[name_or_uri].uri.split("#")[0] == base_uri and
    base_uri in self._loading_base
)

if base_uri in self._loading_base and not is_subdirectory and not is_namespace_preload:
    raise BundleDependencyError(...)
```

Now handles all THREE patterns correctly.

### Critical Learnings

**When fixing algorithms (especially detection/validation):**

1. **Enumerate ALL patterns BEFORE implementing:**
   - Map every way the behavior can legitimately occur
   - Don't stop at "two types"
   - Document exhaustively: "Patterns A, B, C are all legitimate"

2. **Test in ACTUAL deployment configurations:**
   - Not just the component in isolation
   - Include nested bundles, dependent bundles, user configurations
   - Test how users actually use the system

3. **Validate downstream impact of rejections:**
   - If algorithm rejects X, what breaks that depends on X?
   - Trace cascading failures
   - Test error paths as thoroughly as success paths

4. **Passing tests ≠ complete coverage:**
   - "All tests pass" proves what you tested, not what you missed
   - Ask: "What scenarios aren't in our test suite?"
   - Real-world scenarios > artificial test cases

5. **Simple and complete > elegant and incomplete:**
   - Sophistication is a liability without completeness
   - "Conceptually cleaner" doesn't matter if it's wrong
   - Completeness is the priority, simplicity is the tie-breaker

6. **Follow your own process:**
   - Our methodology says: smoke test before GATE 2
   - We skipped it (thought shadow test was enough)
   - Smoke test in actual user config would have caught this
   - Process exists for a reason - don't skip steps

7. **P0 risk assessment for "working" systems:**
   - Issue #6 was warnings (cosmetic, bundles still worked)
   - Seemed low-risk to fix
   - But the fix could break loading entirely
   - "Working but annoying" > "broken" - be conservative

### Updated Process Requirements

**For algorithm/detection fixes, add to checklist:**

```markdown
### Algorithm Fix Validation
- [ ] Enumerate ALL legitimate patterns (not just two)
- [ ] Test with actual user bundle configurations
- [ ] Validate cascading impact if algorithm rejects inputs
- [ ] Run smoke test with EXACT user deployment scenario
- [ ] Ask: "What real-world scenarios aren't in our tests?"
- [ ] Consider: Is simple fix complete? Or is elegant fix incomplete?
```

### The Meta-Lesson

**We violated our own methodology:**

The ISSUE_HANDLING.md we just created says:
> "Shadow test → Smoke test (final defense) → GATE 2 → Push"

**We did:**
> "Shadow test → GATE 2 → Push → **SKIP** smoke test"

**Result:** Deployed broken code that blocked all users.

**The irony:** We shipped the methodology document in the SAME commit that violated it (d340ca3 + 87e42ae pushed together).

**Lesson:** Follow your own process, especially the parts designed to catch exactly this kind of error.

---

## Case Study: PR Review - Understanding Mechanisms First

### Situation

Reviewed PR #211 proposing to add `MODULES.md` to an agent's `context.include` to enable "check before building" functionality.

### The Trap

On surface inspection, the PR looked reasonable:
- Good intent (prevent duplicate work)
- Added a file to context
- Had a related PR for guidance text

Direct file reading showed WHAT changed but not WHY it mattered.

### What Delegation Revealed

**Delegated to `amplifier:amplifier-expert`:**
- MODULES.md is ~20KB (~4,600 tokens)
- It's already @-mentioned in the agent's markdown body (line 88)
- The agent can fetch it on-demand; auto-loading may be unnecessary

**Delegated to `foundation:foundation-expert`:**
- `@mentions` in markdown body → load at instruction-time, DON'T propagate
- `context.include` in YAML → load at composition-time, PROPAGATE to parents
- The PR would cause MODULES.md to propagate to ALL parent bundles

### The Architectural Issue

The agent is designed as a **context sink**—it absorbs heavy docs so parent sessions stay lightweight. Adding MODULES.md to `context.include` would:
1. Propagate 20KB to every bundle that includes the behavior
2. Defeat the context sink pattern entirely
3. Bloat sessions that just want to DELEGATE to the expert, not BE the expert

### Process That Worked

1. `web_fetch` for PR content (no agent for GitHub PRs)
2. Delegate to `amplifier:amplifier-expert` - "What's the current state and token implications?"
3. Delegate to `foundation:foundation-expert` - "How do these mechanisms actually work?"
4. Synthesize findings into architectural assessment

### Key Learning

**Understand mechanisms before reviewing changes to those mechanisms.**

Direct file reading would have shown the diff. Expert delegation revealed:
- The difference between `@mentions` and `context.include`
- Why one propagates and the other doesn't
- The architectural pattern being violated

**Lesson:** When reviewing PRs that modify system behavior, delegate to experts who have the mechanism documentation loaded. They can explain not just WHAT but WHY it matters.

---

## External PRs Are Communication, Not Proposals

**A PR from a contributor is just another form of communication. It is NOT a proposal to merge, NOT a reliable diagnosis, and NOT necessarily even pointing at the right problem.**

Do not presume:
- That what the PR is "fixing" is actually a bug
- That the behavior it changes is undesirable
- That the files it touches are the right files
- That the approach has any relationship to the correct solution

A PR is someone's *interpretation* of a symptom they experienced, expressed as code. That interpretation may be wrong at every level — wrong about what's broken, wrong about why, wrong about where to look.

### PRs vs Issues

**Context-rich issues are generally MORE useful than PRs.** A good issue describes symptoms, reproduction steps, and user impact — the raw observations that investigation needs. A PR skips all of that and jumps straight to a conclusion (the diff), which may be built on faulty premises. The issue tells you what happened; the PR tells you what someone *thinks* should change. The former is evidence; the latter is opinion.

When an issue and PR arrive together, **start with the issue.** The PR is supplementary context at best.

### The Principle

We presume contributors do NOT know our vision, philosophy, or design intent. Therefore:

1. **ALL changes go through our full process** — investigation, root cause analysis, determination of whether a change is even warranted, our own solution design
2. **The PR carries zero weight** beyond its value as communication — it is one person's idea, nothing more
3. **Our position/intent/design/vision/philosophy is what we maintain** — a PR that conflicts with it is simply wrong, regardless of technical correctness
4. **If our design happens to align** with the PR's approach, that's incidental — not a reason to merge their code

Contributors have been informed of this policy. No hard feelings. Most contributions are from other Amplifier instances reporting a bug with a possible solution attached.

### The Process

When an issue arrives (with or without a companion PR):

1. **Start with the issue and reported symptoms** — what did the user actually experience? What's the user scenario? This is the ground truth.
2. **If a PR exists, skim it for supplementary context** — but treat everything in it as unverified claims. The files it touches may be wrong. The root cause it implies may be wrong. The behavior it "fixes" may be intentional.
3. **Ask: "Can they do this without our changes?"** — Before investigating whether a change is correct, check whether the public APIs already support what the contributor needs. If they can build it as their own bundle/module using existing primitives, no change to our codebase is warranted. This filter can save the entire investigation cycle for large architectural PRs.
4. **Investigate the problem independently** — as if no PR existed. Run the full Phase 1-2 process. Determine whether a change is even warranted.
5. **If a change is warranted, design our own solution** — following our philosophy, patterns, and architectural vision.
6. **Implement, test, push our solution** — through the normal Phase 4-6 process.
7. **Close the PR** — thank the contributor for the report. Explain what we found and what we did (or didn't do) about it.

### What to Trust, What to Verify

| From the issue | Trust level | Why |
|----------------|-------------|-----|
| Symptoms described | High — start here | Users report what they experienced |
| Reproduction steps | Medium — verify | May be incomplete or environment-specific |
| Root cause claims | Low — investigate yourself | Users diagnose incorrectly more often than not |

| From the PR | Trust level | Why |
|-------------|-------------|-----|
| That a problem exists | Medium | Something motivated the PR, but it may not be a bug |
| Which files are involved | Low | May be looking at the wrong layer entirely |
| The approach/fix | None | This is their opinion, not ours |

**Anti-pattern:** "The PR touches `expression_evaluator.py`, so the bug is in the expression evaluator"
**Correct pattern:** "The issue says recipe execution fails with apostrophes. Let me trace the actual failure path."

**Anti-pattern:** Evaluating a PR for merge-worthiness
**Correct pattern:** Reading the issue for symptoms, investigating independently, deciding if/what to change

**Anti-pattern:** "The author tested it and showed the bug exists"
**Correct pattern:** Verify the claimed bug at the *end behavior* layer, not intermediate state. Authors often test correctly but at the wrong abstraction level — checking a Python attribute that shows `None` when the underlying SDK correctly resolves the value from an env var.

### Why This Matters

Merging external PRs without our own design process means:
- We're letting someone outside our vision make design decisions
- We're trusting their diagnosis without verification
- We're skipping the investigation that might reveal the real problem is elsewhere
- We're potentially accepting a fix for something that isn't broken
- We're potentially accepting code that works today but creates maintenance burden

The only way to maintain our design integrity is to do our own work. Every time.

### Case Study: Expression Evaluator Quote Escaping

**What happened (wrong):** Issue #215 arrived with PR #28. We treated the PR as a reliable diagnosis — accepted its claim about the root cause, verified its code for correctness, and merged it. We skipped our own investigation and design entirely.

**What should have happened:** Read the issue for symptoms (recipe execution fails with apostrophes). Investigate independently — is the expression evaluator the right place to fix this? Is escaping the right approach, or should substitution work differently? Should the evaluator even handle arbitrary strings, or is this a design smell? Only after answering these questions should we design and implement a fix.

**What we got lucky about:** The PR's diagnosis and approach happened to be reasonable. But we have no idea if there was a better approach, because we never asked the question. We also never questioned whether the files were right, the root cause was right, or the fix was even desirable.

**Lesson:** "The PR looks correct" is the wrong question. The right questions are: "Is there actually a problem? What is it really? Is a change warranted? What's our design?" Only our own investigation can answer these.

### Batch PR Review

When reviewing multiple open PRs on a repo:

1. **List all open PRs** with `gh pr list --state open`
2. **Triage by type**: blocked (waiting on dependencies), reviewable (ready for review), stale (no activity)
3. **Triage by effort**: Quick wins first (dependabot bumps, obvious closes, small doc fixes), deep dives last. Clearing the board early builds momentum and sometimes reveals that later PRs are superseded by the quick wins.
4. **Check for superseded PRs**: If your own work implemented the same feature as an open PR, close the original with attribution — credit the design influence and link to the replacement
5. **Check merge conflicts before reviewing**: `git fetch origin pull/N/head:pr-N-test && git merge --no-commit --no-ff pr-N-test` — if it conflicts, note that in the review; if it merges clean, proceed
6. **Review in dependency order**: If PR B builds on PR A, review and merge A first
7. **Create follow-up PRs immediately**: If reviewing a PR reveals an enhancement opportunity, merge the PR first, then create a follow-up PR that builds on it — don't scope-creep the original
8. **Understand author intent before finalizing feedback**: External context (internal posts, Slack, issue comments) can reframe a PR from "wrong" to "valid experiment with cleanup needed." We don't trust their *diagnosis*, but understanding their *hypothesis* improves our feedback quality and contributor relationships.

### Superseding PRs

When your implementation replaces an open PR from another contributor:

```
gh pr close N --repo org/repo --comment "Closing in favor of #X, which implemented
the same feature set along with [additional fixes]. The design from this PR directly
informed the implementation — thank you @author for the proposal."
```

Always: credit the original author's design contribution, link to the replacement PR, and note where the features are documented.

### Follow-Up PRs

When reviewing a PR reveals an immediate enhancement:

1. **Merge the original PR first** — don't delay it with scope creep
2. **Create a new branch from updated main**
3. **Implement the enhancement** that builds on the merged code
4. **Reference the original PR** in the follow-up commit message

This keeps PRs focused and gives the original contributor clean attribution.

---

## Templates

### Evidence Requirements Template

```markdown
**Evidence-based proof requirements:**

1. **[Specific error disappears]:**
   - Execute: [command]
   - Expected: [specific output or lack of error]
   - Verify: [how to check - grep, exit code, etc.]

2. **[Functional behavior works]:**
   - Execute: [command]
   - Expected: [specific result]
   - Verify: [specific checks]

3. **[End-to-end correctness]:**
   - Scenario: [user workflow]
   - Expected: [specific content in output]
   - Verify: [keywords, data, state]
```

### Investigation Report Template

```markdown
## 🚨 GATE 1: Investigation Complete

### Problem
[User scenario and error]

### Root Cause
[Exact file:line with code snippets]

### Evidence
[How you know this is the cause]

### Proposed Fix
[Specific changes with rationale]

### Files to Change
[List with line numbers]

### Testing Evidence Requirements
[Specific proof requirements]

## 🛑 Waiting for Approval
[What you need user to decide]
```

### Fix Presentation Template

```markdown
## 🚨 GATE 2: Complete Solution Ready for Push Approval

### Issue RESOLVED ✅

### Root Cause Discovered
[Complete explanation]

### The Fix
[Code changes with explanation]

### Shadow Testing - ALL EVIDENCE VERIFIED ✅
[Table of evidence requirements and results]

### Files Changed
[List with descriptions]

### 🛑 Ready for Push
**Commit:** [hash] - "[message]"
**Do you approve pushing this fix?**
```

---

## Special Cases

### Broken Update Issues

When the update mechanism itself is broken:

**User resolution steps:**
```
Users should run: `amplifier reset --remove cache -y`
NOT `amplifier update` (because update is what's broken)
```

### Multi-Repo Fixes

When a fix touches multiple repos:
1. Test all changes together in shadow environment
2. Push in dependency order (core → foundation → modules → apps)
3. Reference related commits in each commit message
4. Create tracking issue linking all PRs

### Design Philosophy Decisions

When the fix involves trade-offs or design choices:
1. Present options with pros/cons
2. Consult relevant experts (amplifier-expert, zen-architect)
3. Let user make the call
4. Document the decision in commit message

### Bundle Cache and Module Loading

After merging a PR to a bundle repository (amplifier-bundle-recipes, amplifier-bundle-notify, etc.):

1. **The running Amplifier process has the old code in memory.** Python loads modules from the bundle cache at startup and stores them in `sys.modules`. Patching the `.py` file in the cache directory does NOT affect the running process.
2. **Tell the user to restart.** "You'll need to restart Amplifier to pick up the new bundle cache."
3. **If the cache doesn't refresh**, the user may need to delete the stale cache directory.
4. **Never attempt more than one retry** after patching a cached module file. If it doesn't work the first time, the module is already loaded in memory — stop and communicate the restart requirement.

**Anti-pattern:** Patching a cached `.py` file, clearing `.pyc`, re-running, seeing the same error, patching again, clearing again...
**Correct pattern:** Merge upstream, tell user to restart, wait.

### Incremental Testing Strategy

When a complex integration fails, decompose into progressive tests:

1. **Unit-level test** — the smallest possible reproduction (5-10 lines). Isolate the single feature that's failing.
2. **Feature interaction test** — combine two features that need to work together.
3. **Integration test** — the real workflow with all features combined.

Run each level before moving to the next. The level where the failure first appears is where the bug lives.

This is faster than repeatedly running the full system and hoping the error message is diagnostic enough. Each level isolates one variable.

---

## Anti-Patterns to Avoid

❌ **"I'll fix it and see if it works"** → Investigate first, understand, then fix  
❌ **"The tests probably pass"** → Actually run them with evidence requirements  
❌ **"I think this is done"** → Shadow test proves it's done  
❌ **"Let me make one more change"** → Commit, test, then make next change  
❌ **"This might be related"** → Find the exact relationship  
❌ **"I'll ask the user to test it"** → You test it first, present working solution  
❌ **"Consider doing X"** → If your analysis supports X, recommend X decisively  
❌ **"Issue 1: mutation. Issue 3: nesting."** → If they're one change, present one item  
❌ **"X has no cost, so make it optional"** → If there's no cost, there's no reason for a toggle  
❌ **Same approach, fourth attempt** → If it failed three times, the approach is wrong — re-investigate from scratch
❌ **"The PR looks correct, let me verify and merge"** → PRs are context, not proposals. Design your own solution.  

---

## Success Metrics

An issue is properly resolved when:

- [x] Root cause identified with specific file:line references
- [x] Fix implemented and committed locally
- [x] Shadow tested with all evidence requirements passing
- [x] Independent smoke test validation (PASS verdict)
- [x] Pushed to appropriate repository
- [x] Issue commented with fix details and user resolution steps
- [x] Issue closed with appropriate label
- [x] Related issues created for out-of-scope work
- [x] Process learnings documented

---

## Autonomy Guidelines

These guidelines help the system handle issues more autonomously, reducing unnecessary human intervention.

### 1. GATE 1 Presentation Pattern

When presenting investigation findings at GATE 1, always include:

1. **Clear recommendation** (not just options)
2. **Reasoning** for the recommendation
3. **Default action** with indication of proceeding unless redirected

**Anti-pattern:** "Here are options A, B, C, D. Which would you like?"
**Correct pattern:** "Based on investigation, I recommend Option C (shadow test to verify, then respond with clarification request) because this validates our finding before responding. Proceeding with shadow test unless you redirect me."

### 2. Unknown Terms = Custom Code Heuristic

When issue reports mention terms not found in the Amplifier codebase:

1. **Assume custom app-layer code** until proven otherwise
2. **Proactively hypothesize** the most likely explanation
3. **Include workaround for custom code** in initial response if applicable

**Example:**
> Reporter mentions "the foreman" which doesn't exist in Amplifier.
> Most likely: Custom orchestrator that bypasses PreparedBundle.
> Action: Include manual capability registration workaround in response.

### 3. Test-Before-Advising Rule

**NEVER propose posting code advice/workarounds without first:**

1. Shadow testing the exact code pattern
2. Verifying it works in a realistic scenario
3. Having specific evidence the advice is correct

**Anti-pattern:** "I can add a follow-up comment suggesting they try X"
**Correct pattern:** "I tested X in shadow environment [evidence]. Ready to post."

This applies even when the advice seems obviously correct. Test it.

### 4. Multi-Scenario Test Planning

When issue could have multiple explanations:

1. **List all plausible scenarios** before testing
2. **Design test plan covering all scenarios** in single round
3. **Run comprehensive test** rather than iterating

**Example for timing issues:**
- Scenario A: Standard PreparedBundle flow → test with shadow-operator
- Scenario B: Custom orchestrator bypassing PreparedBundle → test with amplifier-smoke-test
- Run BOTH tests before presenting findings

### 5. Version Mismatch Detection

When reporter describes code that doesn't match current main:

1. **Verify reporter's version** - Ask what version they're running
2. **Compare against main** - Check if the fix already exists
3. **Provide update instructions** - If fix exists, explain how to get it

**Key pattern:** If reporter's line numbers or code descriptions don't match current main, this is likely a version mismatch, not a missing fix.

### 6. Decisiveness Over Hedging

When you have enough information to make a recommendation, **make it.** Don't present "consider X" when your analysis supports "do X." Hedging wastes the user's time by forcing them to re-derive a conclusion you've already reached.

**Signs you're hedging unnecessarily:**
- You wrote "consider" or "you might want to" but your analysis clearly supports one answer
- You're presenting a design decision to the user that your investigation has already resolved
- You listed pros and cons but didn't say which side wins (and the evidence clearly favors one)

**The litmus test:** "If the user asked me 'so what should I do?', would I immediately know the answer?" If yes, just say it. Don't make them ask.

**Anti-pattern:** "Default True changes behavior. Consider False initially."  (when your own analysis says the change is non-destructive, low-cost, and beneficial)
**Correct pattern:** "Default True is correct — timestamps are non-destructive, can't be added retroactively, and the opt-out is trivial."

**Exception:** When there's a genuine trade-off with no clear winner (e.g., two architecturally valid approaches with different maintenance costs), present the trade-off and let the user decide. The key distinction: unresolved trade-offs go to the user; resolved analysis does not.

---

## Remember

> "My time is cheaper than yours. I should do all the investigation, testing, and validation before bringing you a complete, proven solution. Only bring you design decisions, not missing research."

> "Commit locally before shadow testing. Test until proven working. Present complete evidence, not hopes."

> "If I discover something three times and it's still not working, I don't understand the problem yet. Keep investigating."
