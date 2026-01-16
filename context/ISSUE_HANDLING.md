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

### Pitfall 4: **Not Comparing Similar Code Paths**

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

---

## Anti-Patterns to Avoid

❌ **"I'll fix it and see if it works"** → Investigate first, understand, then fix  
❌ **"The tests probably pass"** → Actually run them with evidence requirements  
❌ **"I think this is done"** → Shadow test proves it's done  
❌ **"Let me make one more change"** → Commit, test, then make next change  
❌ **"This might be related"** → Find the exact relationship  
❌ **"I'll ask the user to test it"** → You test it first, present working solution  

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

## Remember

> "My time is cheaper than yours. I should do all the investigation, testing, and validation before bringing you a complete, proven solution. Only bring you design decisions, not missing research."

> "Commit locally before shadow testing. Test until proven working. Present complete evidence, not hopes."

> "If I discover something three times and it's still not working, I don't understand the problem yet. Keep investigating."
