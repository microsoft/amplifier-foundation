# PR Review Guide

Comprehensive guide for reviewing pull requests, including external contributor PRs,
batch review workflows, and kernel PR criteria. Referenced by `context/ISSUE_HANDLING.md`
and loaded on demand when reviewing PRs.

---

## External PRs Are Communication, Not Proposals

**A PR from a contributor is just another form of communication. It is NOT a proposal to merge, NOT a reliable diagnosis, and NOT necessarily even pointing at the right problem.**

Do not presume:
- That what the PR is "fixing" is actually a bug
- That the behavior it changes is undesirable
- That the files it touches are the right files
- That the approach has any relationship to the correct solution

A PR is someone's *interpretation* of a symptom they experienced, expressed as code. That interpretation may be wrong at every level -- wrong about what's broken, wrong about why, wrong about where to look.

### PRs vs Issues

**Context-rich issues are generally MORE useful than PRs.** A good issue describes symptoms, reproduction steps, and user impact -- the raw observations that investigation needs. A PR skips all of that and jumps straight to a conclusion (the diff), which may be built on faulty premises. The issue tells you what happened; the PR tells you what someone *thinks* should change. The former is evidence; the latter is opinion.

When an issue and PR arrive together, **start with the issue.** The PR is supplementary context at best.

### The Principle

We presume contributors do NOT know our vision, philosophy, or design intent. Therefore:

1. **ALL changes go through our full process** -- investigation, root cause analysis, determination of whether a change is even warranted, our own solution design
2. **The PR carries zero weight** beyond its value as communication -- it is one person's idea, nothing more
3. **Our position/intent/design/vision/philosophy is what we maintain** -- a PR that conflicts with it is simply wrong, regardless of technical correctness
4. **If our design happens to align** with the PR's approach, that's incidental -- not a reason to merge their code

Contributors have been informed of this policy. No hard feelings. Most contributions are from other Amplifier instances reporting a bug with a possible solution attached.

### The Process

When an issue arrives (with or without a companion PR):

1. **Start with the issue and reported symptoms** -- what did the user actually experience? What's the user scenario? This is the ground truth.
2. **If a PR exists, skim it for supplementary context** -- but treat everything in it as unverified claims. The files it touches may be wrong. The root cause it implies may be wrong. The behavior it "fixes" may be intentional.
3. **Ask: "Can they do this without our changes?"** -- Before investigating whether a change is correct, check whether the public APIs already support what the contributor needs. If they can build it as their own bundle/module using existing primitives, no change to our codebase is warranted. This filter can save the entire investigation cycle for large architectural PRs.
4. **Investigate the problem independently** -- as if no PR existed. Run the full Phase 1-2 process. Determine whether a change is even warranted.
5. **If a change is warranted, design our own solution** -- following our philosophy, patterns, and architectural vision.
6. **Implement, test, push our solution** -- through the normal Phase 4-6 process.
7. **Close the PR** -- thank the contributor for the report. Explain what we found and what we did (or didn't do) about it.

### What to Trust, What to Verify

| From the issue | Trust level | Why |
|----------------|-------------|-----|
| Symptoms described | High -- start here | Users report what they experienced |
| Reproduction steps | Medium -- verify | May be incomplete or environment-specific |
| Root cause claims | Low -- investigate yourself | Users diagnose incorrectly more often than not |

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
**Correct pattern:** Verify the claimed bug at the *end behavior* layer, not intermediate state. Authors often test correctly but at the wrong abstraction level -- checking a Python attribute that shows `None` when the underlying SDK correctly resolves the value from an env var.

### Why This Matters

Merging external PRs without our own design process means:
- We're letting someone outside our vision make design decisions
- We're trusting their diagnosis without verification
- We're skipping the investigation that might reveal the real problem is elsewhere
- We're potentially accepting a fix for something that isn't broken
- We're potentially accepting code that works today but creates maintenance burden

The only way to maintain our design integrity is to do our own work. Every time.

---

## Case Study: Expression Evaluator Quote Escaping

**What happened (wrong):** Issue #215 arrived with PR #28. We treated the PR as a reliable diagnosis -- accepted its claim about the root cause, verified its code for correctness, and merged it. We skipped our own investigation and design entirely.

**What should have happened:** Read the issue for symptoms (recipe execution fails with apostrophes). Investigate independently -- is the expression evaluator the right place to fix this? Is escaping the right approach, or should substitution work differently? Should the evaluator even handle arbitrary strings, or is this a design smell? Only after answering these questions should we design and implement a fix.

**What we got lucky about:** The PR's diagnosis and approach happened to be reasonable. But we have no idea if there was a better approach, because we never asked the question. We also never questioned whether the files were right, the root cause was right, or the fix was even desirable.

**Lesson:** "The PR looks correct" is the wrong question. The right questions are: "Is there actually a problem? What is it really? Is a change warranted? What's our design?" Only our own investigation can answer these.

---

## Batch PR Review

When reviewing multiple open PRs on a repo:

1. **List all open PRs** with `gh pr list --state open`
2. **Triage by type**: blocked (waiting on dependencies), reviewable (ready for review), stale (no activity)
3. **Triage by effort**: Quick wins first (dependabot bumps, obvious closes, small doc fixes), deep dives last. Clearing the board early builds momentum and sometimes reveals that later PRs are superseded by the quick wins.
4. **Check for superseded PRs**: If your own work implemented the same feature as an open PR, close the original with attribution -- credit the design influence and link to the replacement
5. **Check merge conflicts before reviewing**: `git fetch origin pull/N/head:pr-N-test && git merge --no-commit --no-ff pr-N-test` -- if it conflicts, note that in the review; if it merges clean, proceed
6. **Review in dependency order**: If PR B builds on PR A, review and merge A first
7. **Create follow-up PRs immediately**: If reviewing a PR reveals an enhancement opportunity, merge the PR first, then create a follow-up PR that builds on it -- don't scope-creep the original
8. **Understand author intent before finalizing feedback**: External context (internal posts, Slack, issue comments) can reframe a PR from "wrong" to "valid experiment with cleanup needed." We don't trust their *diagnosis*, but understanding their *hypothesis* improves our feedback quality and contributor relationships.

---

## Superseding PRs

When your implementation replaces an open PR from another contributor:

```
gh pr close N --repo org/repo --comment "Closing in favor of #X, which implemented
the same feature set along with [additional fixes]. The design from this PR directly
informed the implementation -- thank you @author for the proposal."
```

Always: credit the original author's design contribution, link to the replacement PR, and note where the features are documented.

---

## Follow-Up PRs

When reviewing a PR reveals an immediate enhancement:

1. **Merge the original PR first** -- don't delay it with scope creep
2. **Create a new branch from updated main**
3. **Implement the enhancement** that builds on the merged code
4. **Reference the original PR** in the follow-up commit message

This keeps PRs focused and gives the original contributor clean attribution.
