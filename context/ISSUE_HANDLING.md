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
**Correct pattern:** Investigate -> understand -> design -> implement -> test

### 2. **Evidence-Based Testing**

**Define specific, measurable proof requirements BEFORE testing.**

Each fix must have concrete evidence it works:
- "Command exits with code 0" V
- "No error message X appears in output" V
- "Output contains actual AI-generated content" V
- "Specific keywords present in result" V

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

When you've built the logical case for a position -- "X has negligible cost, provides clear value, and the data is lost forever if not captured now" -- follow that reasoning to its natural endpoint. Don't stop short and present half-conclusions that require the user to connect the final dots.

Common failure mode: Arguing that a feature is non-destructive, low-overhead, and universally useful... then suggesting it should be configurable. If there's truly no cost, there's no reason for a toggle. The toggle is complexity that contradicts your own analysis.

**Anti-pattern:** "X has no real cost and provides value. Consider making X optional."
**Correct pattern:** "X has no real cost and provides value. X should always be on -- a config option would be dead complexity."

**The test:** After writing a recommendation, ask: "Does my conclusion follow from my premises? Or am I hedging on something I've already resolved?"

---

## The Process (7-Phase Workflow)

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

**Resource mining:** Delegate to an agent to scan `docs/ISSUE_CASE_STUDIES.md` for situations
similar to the current issue. The agent should return only the relevant lessons and patterns,
not full narratives. Also scan `docs/PR_REVIEW_GUIDE.md` if the task involves reviewing a PR.

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

**Resource mining:** If the investigation pattern matches a known case study category
(algorithm fix, state management, performance, architectural divergence), delegate to
an agent to retrieve the specific lessons from that case study before designing the fix.

---

### Phase 3: **GATE 1 - Investigation Approval**

**Goal:** Get user approval on approach before implementing.

**Present to user:**
1. Clear problem statement
2. Root cause with evidence (file:line references)
3. Proposed fix with rationale
4. What will be tested and how
5. **Draft GH text** (if the gate concludes with a GH interaction):
   - If "no fix needed" → draft closing/response comment (close, request info, by design)
   - If "fix needed" → approach only (GH text comes at Gate 2)

**Wait for explicit approval before proceeding.**

**Gate efficiency rule:** If the investigation concludes with a GH interaction (close,
request info, etc.), the user approves the action AND the text in one pass. Never
present the decision in one round and the text in a follow-up.

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

Generated with [Amplifier](https://github.com/microsoft/amplifier)
Co-Authored-By: Amplifier <240397093+microsoft-amplifier@users.noreply.github.com>
```

**Resource mining:** Before implementing, delegate to an agent to check if similar
implementations exist in the case studies or if known pitfalls apply to this type of change.

---

### Phase 5: **Shadow Testing**

**Goal:** Prove the fix works with evidence.

**Actions:**
1. Create shadow environment with local changes
2. Install Amplifier from local source
3. Reproduce the original issue scenario
4. Verify all evidence requirements
5. If tests fail -> investigate -> fix -> re-test (loop until working)

**Evidence collection:**
- Capture command outputs (before/after)
- Note exit codes
- Grep for specific error messages
- Verify functional correctness (not just "no error")

**End-to-end evidence principle:** Testing should match how a user would actually
encounter and use the feature. If the issue was "tool X failed to load", evidence
should show tool X working in a realistic scenario, not just unit tests. Evidence
must demonstrate the fix from the user's perspective.

**Don't present to user until ALL evidence requirements pass.**

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
   - **Draft GH closing comment** — the exact text to post on the issue:
     root cause, what changed, testing evidence, and how users get the fix
     (typically `amplifier reset --remove cache -y` followed by
     `amplifier provider install <provider>`)
   
   **User approves the fix AND the public-facing text in one pass.**

3. **After approval:**
   - Push via git-ops agent (handles rebasing, quality commit messages)
   - Post the approved closing comment on the issue
   - Close issue
   - Update any related documentation
   - **Provide post-merge verification steps proactively:**
     (1) How to verify locally (`git pull && pytest`)
     (2) How to verify on another machine (fresh clone or shadow environment)
     (3) What to watch for (regressions, dependency issues)

**IMPORTANT:** If any changes occur after the smoke test (fixing issues it found, user feedback iterations), the smoke test MUST run again before requesting push approval.

---

### Phase 7: **Reflect and Improve**

**Goal:** Capture learnings and update context so the system gets smarter.

**Trigger:** After EACH completed task -- not just at session end. When running a series
of tasks, reflect after each one while context is fresh.

**Why this matters:** The conversation context window is finite. Learnings that aren't
captured in persistent context files are lost to compaction. The "loaded" version of
context files (from bundle cache) is what every LLM request sees -- working directory
changes are invisible to future sessions.

**Actions:**

1. **ANALYZE** -- Self-reflect on the completed task:
   - What worked well? What didn't?
   - Where did the human user have to provide guidance, philosophy, or decision-making
     that the system should have known autonomously?
   - What assumptions were wrong? What was discovered that wasn't in any context file?

2. **MINE** -- Delegate to `foundation:session-analyst` to review the current session:
   - Find exchanges where the user corrected the system's approach
   - Find decisions the user made that reveal philosophy or priorities
   - Find patterns that repeated across multiple tasks in the session

3. **CLASSIFY** -- Categorize each learning:
   - **Principle**: A general rule that applies broadly -> add to Core Principles or Autonomy Guidelines
   - **Pitfall**: A specific mistake to avoid -> add to Anti-Patterns
   - **Pattern**: A reusable approach -> add to Investigation Patterns or a companion doc
   - **Case Study**: A rich narrative worth preserving -> add to `docs/ISSUE_CASE_STUDIES.md`

4. **DISTILL** -- Write the update:
   - Principles, pitfalls, and patterns: <=5 lines each in the core file
   - Case studies: full narrative in `docs/ISSUE_CASE_STUDIES.md`, distilled lesson (<=3 lines) in core
   - **Pruning discipline**: The core file has a 550-line ceiling. If adding a lesson
     would exceed it, consolidate or archive older entries. The Distilled Lessons section
     is a ring buffer -- ~15 items max, oldest get absorbed into principles or archived.

5. **DEPLOY** -- Make the update live:
   - Edit the working directory files
   - Delegate to `foundation:git-ops` to commit and push
   - Create PR or push to main (per repo policy)
   - After merge: `amplifier reset --remove cache -y` clears the bundle cache
   - Next session or agent-spawn picks up the updated context
   - If continuing in the current session: note that the running process still has
     the old version in memory. The update takes effect on restart.

**Anti-pattern:** "I'll capture this later" -> Context is lost to compaction. Capture NOW.
**Anti-pattern:** "This isn't worth documenting" -> If the user had to tell you, it IS worth documenting.

---

## PR Review Gates

The issue handling process was designed for GH Issues but applies to PR reviews too.
PR reviews have their own gate structure to ensure all GH interactions are reviewed
before posting.

### PR Review Gate

When reviewing a PR (any round):

**Present to user:**
1. Review findings (per-PR verdicts, required fixes, non-blocking notes)
2. **Draft review comment text** — the exact text to be posted on each PR
3. For batch reviews: all draft comments together so the user can review the set

**User approves the text before it's posted.** Never post review comments to GH
without the user seeing the exact text first.

### PR Re-review Gate (Fix Rounds)

When the author addresses feedback and requests re-review:

**Present to user:**
1. What changed (new commits)
2. Fix verification status (each required fix: verified or not)
3. Any new issues found
4. **Draft re-review comment text** — the exact text to be posted

**User approves before posting.** Each fix round gets adversarial re-review —
fix commits change the attack surface.

### PR Merge Gate

When recommending merge:

**Present to user:**
1. Merge recommendation with rationale
2. **Draft squash commit message** — the exact commit message for the merge
3. Any pre-merge housekeeping (squash, formatting, etc.)

**User approves the merge AND the commit message in one pass.**

### Gate Efficiency Rule

**Never have two consecutive approval points.** Bundle the draft GH text into
the nearest existing gate where the decision is made. The user reviews the
decision and the public-facing text simultaneously:

- Investigation concludes "close issue" → Gate 1 includes the closing comment text
- Fix is ready to push → Gate 2 includes the closing comment text
- PR review is ready → PR Review Gate includes the comment text
- PR is ready to merge → PR Merge Gate includes the commit message

This eliminates the pattern where findings are approved in one round and the
user has to separately approve (or prompt) the GH text in a follow-up.

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

**Example:** `amplifier run` works, `tool invoke` doesn't -> compare session creation flows

### Pattern 3: **Follow the Data**

Trace where critical data (config, providers, modules) flows:
- Where does it originate? (settings.yaml, bundle.md, CLI flags)
- Where does it get transformed? (merge functions, override logic)
- Where does it get consumed? (session creation, module loading)
- Where does it get lost? (conditional guards, missing handoffs)

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

Direct tool calls (reading files, grepping) consume tokens in YOUR context. Delegation to expert agents is not just efficient -- it surfaces insights you would miss.

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
- [ ] Mine case studies for similar situations
- [ ] Delegate investigation to appropriate agents
- [ ] Trace code paths (working vs broken if applicable)
- [ ] Identify exact root cause with file:line references
- [ ] **GATE 1:** Present investigation to user for approval

### Implementation
- [ ] Implement fix based on approved design
- [ ] Run `python_check` to verify syntax
- [ ] Commit locally with detailed message
- [ ] Create new issues for any out-of-scope work discovered (don't expand scope)

### Testing
- [ ] Define specific evidence requirements
- [ ] Create shadow environment with local changes
- [ ] Run complete end-to-end test
- [ ] Verify ALL evidence requirements pass
- [ ] Collect before/after comparison
- [ ] If tests fail -> investigate -> fix -> re-test (don't present until passing)
- [ ] **GATE 2:** Present complete tested solution to user for approval

### Finalization
- [ ] Push via git-ops agent (handles rebasing, quality)
- [ ] Run independent shadow-smoke-test validation
- [ ] Comment on issue with fix details and evidence
- [ ] Close issue with resolution steps for users
- [ ] Update process documentation with learnings

### Reflection
- [ ] Self-reflect on what worked and what didn't
- [ ] Delegate to session-analyst to find user guidance moments
- [ ] Classify learnings (principle/pitfall/pattern/case study)
- [ ] Distill and write updates (<=5 lines each, respect 550-line ceiling)
- [ ] Deploy: commit, push, merge, cache clear

---

## Distilled Lessons

For full case study narratives, see `docs/ISSUE_CASE_STUDIES.md`.
For PR review guidance, see `docs/PR_REVIEW_GUIDE.md`.

### Investigation Discipline
- Multiple wrong hypotheses preceded the correct one in every major incident.
  Keep investigating until you can point to the exact line of code.
- When the user asks clarifying questions, it signals incomplete understanding.
  Dig deeper, don't treat it as a disruption.
- Even technical reporters need independent code verification. Trust but verify.

### State and Lifecycle
- Performance optimizations create hidden state. Co-locate state with the data
  it tracks, ensure cleanup handles ALL related state, test upgrade/reset paths.

### Algorithm and Detection Fixes
- Enumerate ALL legitimate patterns BEFORE implementing. Don't stop at two.
- Test in ACTUAL deployment configurations, not isolated components.
- If algorithm rejects X, trace what depends on X loading successfully.
- Simple and complete beats elegant and incomplete.

### PR Review
- Each fix round changes the attack surface. Review adversarially every time.
- Tests must guard contracts, not implementations. If "X is overridable" is
  the design claim, test the override.
- Kernel fields with ambiguous semantics need `Field(description=...)` not
  inline comments -- invisible in IDE hover, help(), JSON schema.
- Even owner PRs need independent expert review.

### Multiple Perspectives
- Parallel agent dispatch surfaces ground truth through convergence.
  Different agents find different aspects of the same problem.
- Constraints that eliminate quick workarounds force finding the REAL fix.

### Process Compliance
- Follow your own process, especially the parts designed to catch exactly
  the kind of error you're about to make.
- "Working but annoying" > "broken" -- be conservative with fixes to working systems.

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
## GATE 1: Investigation Complete

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

## Waiting for Approval
[What you need user to decide]
```

### Fix Presentation Template

```markdown
## GATE 2: Complete Solution Ready for Push Approval

### Issue RESOLVED

### Root Cause Discovered
[Complete explanation]

### The Fix
[Code changes with explanation]

### Shadow Testing - ALL EVIDENCE VERIFIED
[Table of evidence requirements and results]

### Files Changed
[List with descriptions]

### Ready for Push
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
2. Push in dependency order (core -> foundation -> modules -> apps)
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
4. **Never attempt more than one retry** after patching a cached module file. If it doesn't work the first time, the module is already loaded in memory -- stop and communicate the restart requirement.

**Anti-pattern:** Patching a cached `.py` file, clearing `.pyc`, re-running, seeing the same error, patching again, clearing again...
**Correct pattern:** Merge upstream, tell user to restart, wait.

### Incremental Testing Strategy

When a complex integration fails, decompose into progressive tests:

1. **Unit-level test** -- the smallest possible reproduction (5-10 lines). Isolate the single feature that's failing.
2. **Feature interaction test** -- combine two features that need to work together.
3. **Integration test** -- the real workflow with all features combined.

Run each level before moving to the next. The level where the failure first appears is where the bug lives.

This is faster than repeatedly running the full system and hoping the error message is diagnostic enough. Each level isolates one variable.

---

## Reference Material (Load On Demand)

These companion files contain detailed reference material. Use agents to mine
them for contextually relevant content rather than loading everything.

- **`docs/ISSUE_CASE_STUDIES.md`** -- Full narratives of past incidents with
  timelines and detailed analysis. Mine when encountering a situation that
  rhymes with a past case.
- **`docs/PR_REVIEW_GUIDE.md`** -- External PR policy, multi-round review
  process, kernel PR criteria, batch review workflow. Load when reviewing PRs.

---

## Anti-Patterns to Avoid

X **"I'll fix it and see if it works"** -> Investigate first, understand, then fix  
X **"The tests probably pass"** -> Actually run them with evidence requirements  
X **"I think this is done"** -> Shadow test proves it's done  
X **"Let me make one more change"** -> Commit, test, then make next change  
X **"This might be related"** -> Find the exact relationship  
X **"I'll ask the user to test it"** -> You test it first, present working solution  
X **"Consider doing X"** -> If your analysis supports X, recommend X decisively  
X **"Issue 1: mutation. Issue 3: nesting."** -> If they're one change, present one item  
X **"X has no cost, so make it optional"** -> If there's no cost, there's no reason for a toggle  
X **Same approach, fourth attempt** -> If it failed three times, the approach is wrong -- re-investigate from scratch
X **"The PR looks correct, let me verify and merge"** -> PRs are context, not proposals. Design your own solution.  
X **Burying status in analysis** -> Lead with the action confirmation on its own line ("Done — posted to PR #10"), then the analytical summary. Action status must be scannable, not embedded in paragraphs.
X **Adding to a large file without flagging size** -> Before modifying context files, check their current size. If a context file exceeds 500 lines, flag it and propose restructuring before adding more. Growing a problem is not acceptable just because the growth is individually justified.
X **Posting to GH without gate approval** -> Every GH interaction (comment, review, close) must be reviewed by the user. Bundle the draft text into the nearest existing gate — never post first and report after.

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
- Scenario A: Standard PreparedBundle flow -> test with shadow-operator
- Scenario B: Custom orchestrator bypassing PreparedBundle -> test with amplifier-smoke-test
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
**Correct pattern:** "Default True is correct -- timestamps are non-destructive, can't be added retroactively, and the opt-out is trivial."

**Exception:** When there's a genuine trade-off with no clear winner (e.g., two architecturally valid approaches with different maintenance costs), present the trade-off and let the user decide. The key distinction: unresolved trade-offs go to the user; resolved analysis does not.

### 7. Post-Action = Next-Action

After completing any action, propose the logical next step. Never leave conversational
dead air that requires the user to re-engage.

**Anti-pattern:** "Merged. PR #10 is on main." [silence]
**Correct pattern:** "Merged. PR #10 is on main. To verify: `git pull && pytest`. Next: review the Phase 2 batch, or run reflection?"

### 8. Follow the Process You Built

When an agreed-upon workflow defines the next step, execute it (or begin it) rather
than asking permission. Deference is for design decisions, not for executing agreed
processes.

**Anti-pattern:** "Phase 7 says to reflect now. But you may have something else in mind..."
**Correct pattern:** Begin the reflection. "Running Phase 7 reflection on this task. [ANALYZE step]..."

---

## Remember

> "My time is cheaper than yours. I should do all the investigation, testing, and validation before bringing you a complete, proven solution. Only bring you design decisions, not missing research."

> "Commit locally before shadow testing. Test until proven working. Present complete evidence, not hopes."

> "If I discover something three times and it's still not working, I don't understand the problem yet. Keep investigating."
