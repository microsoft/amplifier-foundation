---
name: superpowers-philosophy
description: "Use when reasoning about design tradeoffs in the Amplifier style -- ruthless simplicity, bricks-and-studs modularity, kernel philosophy. Pull when a decision warrants principled framing."
---

> *Loaded on demand. The full guidance below is not in your default context --
> you reached for this skill because the situation called for it. Treat the
> sections that follow as authoritative for the duration of this task.*


## Core Principles

### 1. Test-Driven Development is Non-Negotiable

Write the test first. Watch it fail. Write minimal code to pass.

If you didn't watch the test fail, you don't know if it tests the right thing. Code written before tests must be deleted - no exceptions, no "keeping as reference."

The cycle:

```
RED: Write failing test
  → Verify it fails for the right reason
GREEN: Write minimal code to pass
  → Verify all tests pass
REFACTOR: Clean up
  → Stay green
REPEAT
```

### 2. Systematic Over Ad-Hoc

Random fixes waste time and create new bugs. Quick patches mask underlying issues.

Always find the root cause before attempting fixes. Follow the four-phase debugging process: investigate, analyze patterns, form hypotheses, then implement.

### 3. Evidence Over Claims

"It should work" is not verification. "I tested it manually" is not proof.

**The Gate Function — apply before ANY completion claim:**
1. IDENTIFY: What command proves this claim?
2. RUN: Execute the FULL command (fresh, in this session)
3. READ: Full output, check exit code, count failures
4. VERIFY: Does output confirm the claim?
5. ONLY THEN: Make the claim

Every fix must be demonstrated with evidence: tests passing, manual verification documented, side effects checked.

### 4. Complexity Reduction as Primary Goal

Simplicity is not just nice to have - it's the goal. Every abstraction must justify its existence.

YAGNI (You Aren't Gonna Need It) ruthlessly. DRY (Don't Repeat Yourself) pragmatically. Start minimal, grow only as needed.

### 5. Structured Planning Before Implementation

Never jump into code. First understand what you're building through collaborative design. Then create a detailed plan with bite-sized tasks. Then execute systematically.

The plan should be clear enough for "an enthusiastic junior engineer with poor taste, no judgment, no project context, and an aversion to testing" to follow.

### 6. Isolation for Safety

Use git worktrees to isolate feature work. Never work directly on main. Verify clean test baseline before starting. Clean up when done.

### 7. Human Checkpoints at Critical Points

Autonomous work is powerful, but human judgment is essential at key moments:
- After design completion (before saving)
- After plan creation (before execution)
- Between execution batches
- Before merging/PR creation

## The Superpowers Workflow

```
1. BRAINSTORM -> Refine idea into design (human approves)
2. PLAN -> Break design into tasks (human approves)
3. WORKTREE -> Create isolated workspace
4. EXECUTE -> Implement with TDD + reviews
5. FINISH -> Verify, merge/PR, cleanup
```

Each step uses the appropriate recipe with built-in quality gates.

## The Two-Stage Review Pattern

After each task implementation, two separate review passes ensure quality:

**Stage 1: Spec Compliance Review** (superpowers:spec-reviewer)
- Does implementation match the spec exactly?
- Nothing missing from requirements?
- Nothing extra that wasn't requested?

**Stage 2: Code Quality Review** (superpowers:code-quality-reviewer)
- Clean code principles followed?
- Proper error handling?
- Test coverage adequate?
- No obvious issues?

Both stages must pass before moving to next task. Order matters - spec compliance first, quality second.

## Anti-Patterns to Avoid

- **Jumping to code** without understanding requirements
- **Skipping tests** for "simple" changes
- **Multiple fixes at once** instead of isolated changes
- **Ignoring test failures** or marking them as "expected"
- **Working on main** instead of feature branches
- **Claiming success** without verification evidence
- **Rationalizing shortcuts** ("just this once", "too simple to test")
- **Test passes on first run** — you didn't verify it tests the right thing
- **Keeping code as "reference"** while writing tests after the fact
- **"This is different because..."** — it's not different
- **"It's about the spirit, not the letter"** — the letter IS the spirit

## Philosophy in Practice

When you catch yourself thinking any of these, STOP:

| Thought | Action |
|---------|--------|
| "This is too simple to need a test" | Write the test anyway. Simple code breaks. Test takes 30 seconds. |
| "I'll add tests later" | Write them now or delete the code. Tests passing immediately prove nothing. |
| "Quick fix, then investigate" | Investigate first |
| "It should work now" | Verify with evidence |
| "Just one more try" (after 2 failures) | Question the architecture |
| "I know what the problem is" | Prove it with evidence |
| "I already manually tested it" | Ad-hoc ≠ systematic. No record, can't re-run. |
| "Deleting working code is wasteful" | Sunk cost fallacy. Keeping unverified code is debt. |
| "Need to explore first" | Fine. Throw away exploration, then start with TDD. |
| "TDD will slow me down" | TDD is faster than debugging. |
| "Tests after achieve the same thing" | Tests-first = "what should this do?" Tests-after = "what does this do?" |
| "I'll keep it as reference" | You'll adapt it. That's testing after. Delete means delete. |
| "Test is hard to write" | Hard to test = hard to use. Listen to the test. |
| "TDD is dogmatic, I'm being pragmatic" | TDD IS pragmatic. Shortcuts = debugging in production = slower. |
| "This is different because..." | It's not different. The process exists because every project thinks it's different. |

## The Goal

Superpowers isn't about following rules for their own sake. It's about:

1. **Higher quality** - Fewer bugs, more reliable software
2. **Faster delivery** - Less debugging, less rework
3. **Sustainable pace** - No firefighting, no technical debt spiral
4. **Confidence** - Know the code works because you proved it

The discipline enables the speed, not the other way around.

