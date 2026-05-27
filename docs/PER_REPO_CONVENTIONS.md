# Per-Repo Conventions

How agents (and humans) discover and honor the local rules of a repository before changing it.

---

## The Principle

Foundation defines the philosophy. Individual repositories define their specifics. An agent working in a repo is a guest — read the house rules before changing the furniture.

This is not bureaucracy. It is stewardship. Foundation cannot know every repo's gates, smoke tests, common pitfalls, or release rituals; the repo owners encode that knowledge in files they expect you to read. When you skip the discovery step, you re-discover bugs they already solved, you break invariants they already documented, and you waste reviewer time on rework that the checklist would have caught.

If you are about to act in a repository you have not worked in before — or have not worked in recently — **stop and read the conventions first**. It takes a minute. It saves hours.

---

## What to Look For

Read these files, in this order, before writing code or opening a PR:

| File | Purpose | When it wins |
|------|---------|--------------|
| `AGENTS.md` | Agent-facing conventions: test commands, gates, common pitfalls, "what 'done' looks like" | Always. This is the repo owner talking directly to you. |
| `.github/PULL_REQUEST_TEMPLATE.md` | The checklist a PR body is expected to honor; reviewers see unchecked boxes immediately | Whenever you open a PR. |
| `CONTRIBUTING.md` | Style, branch naming, commit conventions, contribution workflow | General contribution context. |
| `README.md` | User-facing overview; sometimes contains a contributor or development section | When the repo is unfamiliar. |

### Discovery locations

Some of these files live only at the repo root. Others — `AGENTS.md` in particular — may also appear in subdirectories that scope conventions to a specific subsystem.

The discovery pattern:

1. Read the file at the repository root if it exists.
2. Read any same-named file in the current working directory.
3. Walk upward from the working directory to the repo root, reading any same-named files along the way.
4. Apply the **most specific** file last — subsystem rules override repo-wide rules.

If a repo has no `AGENTS.md`, no PR template, no `CONTRIBUTING.md` — that is itself information. Default behavior applies. But check before you assume.

---

## JIT-Loaded Files: `PRINCIPLES.md` and `SMOKE_TESTS.md`

Some files are read **only at specific phases of work**, not on every operation. They earn their own file — rather than a section in `AGENTS.md` — because force-loading their content into every agent operation would tax context for content that is only sometimes relevant. The convention is to keep `AGENTS.md` thin (always-on) and push phase-specific material to dedicated files that the agent loads when the phase fires.

| File | Loading scope | When to read |
|------|---------------|--------------|
| `PRINCIPLES.md` | Just-in-time, design phase | Before planning or designing a change. Captures philosophical context, architectural invariants, upstream-spec linkage, intentional deltas, and pointers to deeper material (ADRs, design docs). |
| `SMOKE_TESTS.md` | Just-in-time, planning **and** verification phases | **Twice.** At planning, to know the scenarios this change will be graded against — design *to* the rubric. At verification, to run the smokes and confirm. Names the repo's own smoke runnable(s) and any cross-repo smokes that must also run when changes touch dependent repos. |

If a repo has either file, its `AGENTS.md` should point at them with explicit triggers ("Before designing changes, read `PRINCIPLES.md`. Before verifying, read `SMOKE_TESTS.md`.").

On `SMOKE_TESTS.md` specifically: read it at **both** planning and verification. The point of phase-specific files is not "do not load until the phase fires" — it is "load when their content is relevant to the work at hand." Discovering the grading rubric only at verification means you designed without knowing what excellence looked like. The agent loads them when the phase fires; otherwise they stay out of context.

These files are optional. A repo without either is signalling that everything an agent needs at design or verification time is already in `AGENTS.md` or self-evident from the codebase. Author them when the gap is real, not pre-emptively.

---

## The Discovery Pattern (Concrete)

**Before writing code in a repo:**

- Read `AGENTS.md` if it exists. Apply its test commands, smoke-test invocations, and common pitfalls.
- Read `CONTRIBUTING.md` if it exists. Honor its branch naming and commit message conventions.
- Note any gates the repo declares (lint, type-check, smoke tests, integration tests). You will run these before declaring done.

**When opening a PR:**

- Read `.github/PULL_REQUEST_TEMPLATE.md` if it exists. The template is not decorative — it is the reviewer's checklist.
- Populate the PR body using the template's structure. Fill in evidence for each checkbox. Do not silently skip items.
- If a checkbox does not apply, say so explicitly: `- [x] N/A — no provisioning changes`. An unchecked box without explanation reads as "I forgot."

**When verifying work:**

- The PR template's verification checklist tells you which gates the repo expects. Run them.
- If the template asks for a smoke test, a live run, or evidence from a specific environment, produce that evidence. Link it. Paste it. Do not paraphrase.

**When repo conventions contradict your defaults:**

- The repo wins. You are a guest.
- If you believe the convention is wrong, say so in the PR description with evidence. Do not silently override.

---

## The Verification Gradient

Unit tests are necessary but rarely sufficient. Most repos that have had integration-blocking bugs in production specify a gradient of gates beyond unit tests — and the gradient exists because integration is where unit-tested code breaks.

A repo's `AGENTS.md` or PR template typically encodes which of these gates apply:

| Gate | What it proves | When the repo requires it |
|------|----------------|---------------------------|
| Unit tests | Code-as-written behaves as written | Always. Floor, not ceiling. |
| Integration tests | Components compose correctly | When the change crosses module boundaries. |
| Smoke tests | The system starts and the happy path runs | When the change touches startup, provisioning, or configuration. |
| Live runs | The system handles a real workload end-to-end | When the change touches engines, pipelines, or orchestration. |

Repo owners have learned through experience which of these gates catch real bugs in their code. Their conventions encode that experience. Skipping a gate the repo specifies is not a time-saver; it is a way of re-discovering bugs the owner already paid for.

---

## Why This Matters

A recurring failure pattern: unit tests pass, integration fails. The author wrote tests from the implementation outward — does this code do what I wrote it to do? — and never asked the question that catches integration bugs: what scenarios does the production path actually trigger?

Repo owners who have lived through this learn the specific paths their code breaks on. They write those paths into `AGENTS.md`. They put gates into the PR template. They list the smoke tests in `CONTRIBUTING.md`.

When an agent ignores these files, the agent is not being clever; it is choosing to learn from scratch what someone has already documented. The cost is paid by reviewers and by users in production. Read the files. The owner wrote them for exactly this moment.

---

## Author Guidance (If You Own a Repo)

If you maintain a repository, write down what the next agent or contributor needs to know. The audience is **the next person (or agent) who will touch this code**, and they have no prior context.

**Put in `AGENTS.md`:**

- The test command(s) that must pass before "done"
- Smoke-test invocations, with the exact commands
- Common pitfalls — bugs you have hit more than once
- What "done" looks like for a typical change in this repo
- Any environment setup that is not obvious from `README.md`

**Put in `.github/PULL_REQUEST_TEMPLATE.md`:**

- The verification checklist a PR body must address
- Evidence requirements (logs, screenshots, smoke-test output)
- Links to the relevant gates and where to find their commands

**Keep both files short.** Twenty lines of actionable checklist beat two hundred lines of philosophy. Philosophy lives here in foundation; specifics live in your repo.

**Update them when you learn something.** If a class of bug bit you twice, that is a pitfall worth documenting. If you added a gate, add it to the template. The files are living documents.

---

## Cross-References

- [AGENT_AUTHORING.md](AGENT_AUTHORING.md) — how to author agents (including the discovery behavior described above)
- [PR_REVIEW_GUIDE.md](PR_REVIEW_GUIDE.md) — how to review PRs, including how to use the repo's template as the review baseline
- [CONCEPTS.md](CONCEPTS.md) — foundation concepts that apply across all repos

---

## TL;DR

Before acting in a repo, read its `AGENTS.md`, `.github/PULL_REQUEST_TEMPLATE.md`, and `CONTRIBUTING.md`. Apply them. When opening a PR, fill in the template honestly. When the repo's rules contradict your defaults, the repo wins.

The repo owner has learned things you have not. Read what they wrote.
