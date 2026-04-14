# Issue Case Studies

Full narratives of past incidents with timelines and detailed analysis. These case studies
are referenced by `context/ISSUE_HANDLING.md` and mined on demand by agents when encountering
situations that rhyme with past cases.

---

## Index

1. [Architectural Divergence (CLI Command Parity)](#case-study-architectural-divergence-cli-command-parity)
2. [State Management Across Lifecycle Operations](#case-study-state-management-across-lifecycle-operations)
3. [Performance Optimization with Constraints](#case-study-performance-optimization-with-constraints)
4. [Algorithm Design (False Positives)](#case-study-algorithm-design-false-positives)
5. [P0 Regression from Incomplete Algorithm Fix](#case-study-p0-regression-from-incomplete-algorithm-fix)
6. [PR Review - Understanding Mechanisms First](#case-study-pr-review---understanding-mechanisms-first)

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
9. **User approval -> Push:** git-ops pushed to main
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
Report of 142x performance degradation in long sessions (0.5s -> 79s gap between events).

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
- X Inter-bundle circular (Bundle A -> Bundle B -> Bundle A) - should block
- V Intra-bundle subdirectory (foundation -> foundation:behaviors/sessions) - should allow

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
- User chose "better option" -> more code but clearer intent

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
- Shadow tested: 11/11 tests passed
- Pushed to production (commit 87e42ae)

**Immediate failure:**
- Users updated and crashed
- Cannot start sessions
- No workaround available
- Emergency investigation required

### Root Cause

**The Issue #6 fix was INCOMPLETE:**

**We identified TWO patterns:**
- V Inter-bundle circular (block this)
- V Intra-bundle subdirectory (allow this)

**We MISSED a THIRD pattern:**
- X Namespace preload self-reference (allow this)

**The bug in our fix:**
```python
is_subdirectory = "#subdirectory=" in uri  # Only checked for fragment

if base_uri in self._loading_base and not is_subdirectory:
    raise BundleDependencyError(...)  # Missed namespace preload!
```

When amplifier-dev included "foundation" by registered name for namespace resolution, the detector saw foundation's base URI already loading but no `#subdirectory=` fragment -> false circular error -> foundation SKIPPED -> no orchestrator config -> crash.

### Key Failures in Our Process

**Failure 1: Incomplete pattern enumeration**
- Stopped at two patterns without asking: "What else?"
- Didn't map ALL the ways bundles reference each other
- Algorithm was solving 2 out of 3 cases

**Lesson:** When categorizing behaviors, enumerate EXHAUSTIVELY before implementing. Document: "This algorithm handles patterns A, B, C" and verify no pattern D exists.

**Failure 2: Testing the wrong scenario**
- Tested with foundation bundle directly
- Actual users use amplifier-dev (nested bundle)
- The failure only appeared in nested bundle -> parent composition
- 11/11 tests passed but didn't cover real deployment

**Lesson:** Test in ACTUAL user scenarios, not isolated components. Ask: "How do users actually use this?" Include their bundle configurations in tests.

**Failure 3: False confidence from green tests**
- "11/11 tests passed" created confidence
- But tests only covered what we thought of
- Didn't cover the pattern we missed
- Green checkmarks != complete coverage

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

**Lesson:** We violated our own process. Shadow test != smoke test. Test in the actual configuration users deploy.

**Failure 6: "Better" wasn't complete**
- Chose Option B (dual tracking) over Option A (simple skip) for elegance
- Option B was more sophisticated but incomplete
- Option A (skip preload if already loading) would have worked for all patterns
- Sophistication without completeness = dangerous

**Lesson:** Simple and complete beats elegant and incomplete. When choosing between options, completeness is more important than conceptual clarity.

### The Cascade

```
Circular detection logic incomplete
  |
Foundation include flagged as circular
  |
Foundation bundle SKIPPED
  |
amplifier-dev has no orchestrator config (depends on foundation)
  |
Session creation crashes
  |
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

4. **Passing tests != complete coverage:**
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
> "Shadow test -> Smoke test (final defense) -> GATE 2 -> Push"

**We did:**
> "Shadow test -> GATE 2 -> Push -> **SKIP** smoke test"

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
- `@mentions` in markdown body -> load at instruction-time, DON'T propagate
- `context.include` in YAML -> load at composition-time, PROPAGATE to parents
- The PR would cause MODULES.md to propagate to ALL parent bundles

### The Architectural Issue

The agent is designed as a **context sink** -- it absorbs heavy docs so parent sessions stay lightweight. Adding MODULES.md to `context.include` would:
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
