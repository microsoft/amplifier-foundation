# Per-Repo Conventions

How agents (and humans) discover, honor, and keep re-reading the local rules of a repository while changing it.

---

## Why This Matters

Foundation defines the philosophy. Individual repositories define their specifics. An agent working in a repo is a guest — read the house rules before changing the furniture, and keep them in view while you work.

This is not bureaucracy; it is stewardship. Foundation cannot know any given repo's gates, smoke tests, pitfalls, invariants, or release rituals. The repo's owners encode that knowledge in files they expect you to read. The cost of reading them is minutes. The cost of skipping them is paid downstream, and it is larger:

- You re-derive decisions the owners already settled.
- You break invariants they already documented.
- You design without knowing how the change will be graded, then fail verification.
- You re-propose work the team already chose to defer.
- You burn reviewer time on rework a checklist would have caught.

The owners learned these things the hard way. Reading what they wrote is how you avoid learning them the same way.

---

## The Two Kinds of Convention Files

Repo conventions live in two classes of file. The distinction is about *loading cost*, and it changes how you treat each.

**Always-loaded** — thin, and in context on every operation:

| File | Purpose |
|------|---------|
| `AGENTS.md` | Agent-facing conventions: test commands, gates, common pitfalls, what "done" looks like. The owner talking directly to you. |
| `.github/PULL_REQUEST_TEMPLATE.md` | The checklist a PR body must honor; reviewers see unchecked boxes immediately. |
| `CONTRIBUTING.md` | Style, branch naming, commit conventions, contribution workflow. |
| `README.md` | User-facing overview; sometimes carries a contributor or development section. |

**Contextual** — read when their phase is relevant, not crammed into the always-on set:

| File | Holds | When it's read / written |
|------|-------|--------------------------|
| `VISION.md` | The desired end state the repo is converging toward, and what it deliberately resists. For repos steering against a governing contract — see below. | Read before designing; amended only with evidence, never edited to record what shipped. |
| `PRINCIPLES.md` | Philosophical context, architectural invariants, upstream-spec linkage, intentional deltas, pointers to ADRs and design docs. | Read before designing or planning a change. |
| `SMOKE_TESTS.md` | The repo's smoke runnable(s) and any cross-repo smokes to run when changes touch dependents. | Read **twice** — at planning (design *to* the rubric) and at verification (run it). |
| `KNOWN_ISSUES.md` | Known-broken, deliberately-deferred, or unsupported items, plus intent and good ideas for future work. | Read when scoping; written when work is deferred (offer and confirm first — see below). |

Why two classes? `AGENTS.md` stays thin and always-on because it is paid for on every operation. The contextual files would tax that budget with material that is only sometimes relevant, so they earn their own file and load when their content is needed. Keeping them separate avoids per-operation bloat — it is **not** permission to skip them. They are optional to *author*: a repo without them is signalling that everything an agent needs is already in `AGENTS.md` or self-evident from the code. Author them when the gap is real, not pre-emptively.

---

## Where the Files Live

Some of these files exist only at the repo root. Others — `AGENTS.md` especially — may also appear in subdirectories that scope conventions to a specific subsystem.

To resolve them:

1. Read the file at the repository root if it exists.
2. Read any same-named file in your current working directory.
3. Walk upward from the working directory to the root, reading same-named files along the way.
4. Apply the **most specific** file last — subsystem rules override repo-wide rules.

If a repo has none of these files, that is itself information: default behavior applies. But check before you assume.

---

## When to Read — and Re-Read

An agent acts only on what is in its context. A convention you never loaded cannot guide you; a convention that has fallen out of context no longer does. So reading repo conventions is not a one-time gate at the start of a task — it is a habit you repeat as the work changes shape.

**Read what exists before you start.** Before any new work in a repo — designing, coding, debugging, opening a PR — read the always-loaded files and whichever contextual files exist, at the root and in the subdirectories you will touch.

**Re-read at each major shift in what you are doing.** Design → implementation. Investigation → fix. Coding → verification → PR. Two forces make a single up-front read insufficient:

- *Context decay.* Over a long session, earlier reads fall out of context through compaction and intervening work. A file you read an hour ago may no longer be in front of you.
- *Changing lens.* The same file matters differently depending on what you are doing. At design, `PRINCIPLES.md` tells you which invariants to honor; at verification, whether you did. `SMOKE_TESTS.md` at planning is a rubric to design toward; at verification it is a command to run. Re-reading is re-aiming, not repetition.

(Those transitions are illustrative, not a canonical list. The rule is simply: when the nature of your work shifts, re-read what is now relevant.)

**The one exception:** files that an `AGENTS.md` pulls in through an always-loaded @-mention chain ride along automatically and need no deliberate re-read. Everything else is on you to revisit.

**Honor what you read.** As the work reaches each stage:

- *Opening a PR* — populate the body from `.github/PULL_REQUEST_TEMPLATE.md`. For each item, exactly one of three cases holds: (1) you have real evidence — paste it; (2) it genuinely does not apply — say so (`- [x] N/A — no provisioning changes`); (3) it applies, is required, and you cannot honestly satisfy it — **stop and return the unmet item to the caller; do not fabricate evidence or pre-check the box.** A self-granted N/A is a determination to surface for confirmation, not a licence to proceed. An unchecked box without explanation reads as "I forgot"; a checked box you can't back is worse — it reads as "passed" when it didn't."
- *Verifying* — run the gates the repo declares (see the gradient below). If the template asks for a smoke test, a live run, or evidence from a specific environment, produce it. Link it, paste it — do not paraphrase.
- *Deferring work* — when you and the user decide *not* to address something now, record it in `KNOWN_ISSUES.md` so the gap and the intent behind it survive. Because appending is cheap, this file bloats fast: **offer the entry and get the user's confirmation before writing it.** Do not auto-log every rough edge or passing TODO — only what the user agrees is worth keeping.
- *Capturing what you learned* — when a session surfaces a lesson worth keeping — a footgun that bit you, an invariant you discovered or fixed, a decision you and the user reached — write it back to the file that owns it: a recurring pitfall or a changed command into `AGENTS.md`, a new gate into the PR template, an architectural invariant or intentional delta into `PRINCIPLES.md`, deferred work into `KNOWN_ISSUES.md`. Do it **as the lesson lands**, and **sweep back through the session before you call the work done** to catch what fell out of context — a lesson left only in a closed session is a footgun re-armed for the next agent. Same discipline as deferral: offer the entry, get the user's confirmation, keep what's worth keeping — not every passing thought.
  - *Route by ownership, honor awareness boundaries.* A lesson belongs in the repo whose truth it encodes. You may only write a lesson into repo A that names repo B when A is already allowed to know B (A depends on B). If the lesson lives at a boundary the repo must stay agnostic about — in a modular system you cannot know every way your repo will be mixed and matched — it goes elsewhere: into the dependency that owns it, into the layer permitted to know both, or up into foundation if it generalizes. Never leak a downstream repo's specifics into an agnostic upstream repo's conventions — that is the exact modular-boundary pollution these files exist to prevent. `REPOSITORY_RULES.md` (in the `amplifier` repo) is the authority on who may know whom.
- *Conflict* — when a repo convention contradicts your defaults, the repo wins; you are a guest. If you believe the convention is wrong, say so in the PR description with evidence. Do not silently override.

---

## The Verification Gradient

Unit tests are necessary but rarely sufficient. The recurring failure: unit tests pass, integration fails. The author wrote tests from the implementation outward — *does this code do what I wrote it to do?* — and never asked the question that catches integration bugs: *what scenarios does the production path actually trigger?*

Repos that have lived through this declare a gradient of gates beyond unit tests. A repo's `AGENTS.md` or PR template encodes which apply:

| Gate | What it proves | When the repo requires it |
|------|----------------|---------------------------|
| Unit tests | Code-as-written behaves as written | Always. Floor, not ceiling. |
| Integration tests | Components compose correctly | When the change crosses module boundaries. |
| Smoke tests | The system starts and the happy path runs | When the change touches startup, provisioning, or configuration. |
| Live runs | The system handles a real workload end-to-end | When the change touches engines, pipelines, or orchestration. |

Owners have learned which gates catch real bugs in their code. Skipping a gate the repo specifies is not a time-saver; it is a way of re-discovering a bug the owner already paid for.

---

## Steering Toward a Governing Contract

Some repos are not free to move in whatever direction they like. They implement an upstream spec, serve a published schema, honor an API contract, or carry a design charter someone else owns. In those repos a change has to answer a second question beyond *does this work?* — namely *which way does this move us, relative to the thing we are bound to?*

This section is for those repos only. A repo with no governing contract has no direction to drift from and should not manufacture one. But where the contract is real, so is the drift, and it arrives quietly: one locally-reasonable change at a time, each defensible on its own, none of them recorded as a decision.

**State the destination in a file, and never edit that file for status.** A `VISION.md` says what the repo is converging toward, written as though already true — the north star, the principles it operates by, what it deliberately resists. What shipped, what is in flight, and what comes next are status and sequencing; those belong in the issue queue and in whatever ledgers the repo keeps. That separation is the whole discipline: *a page that has to be edited when a feature ships is a status report wearing a vision's name*, and it will end up competing with the ledgers instead of being the thing they are measured against. Amendments carry evidence — a failure this framing would have caught, or a cost it retires — and land in a dated changelog on the page itself, so the vision's own history stays legible.

**Price each direction differently.** How much friction a change meets should depend on which way it moves relative to the contract, not on how large it is:

| Direction relative to the contract | Posture | What the change owes |
|------------------------------------|---------|----------------------|
| **Toward** — closes a gap with the contract | Easy and supported; presumption of yes | The normal evidence for its change class. No ledger entry: conforming is the default state, not a deviation. |
| **Into unspecified territory** — the contract is silent here | Resisted, but permitted | An argument for why the silence is not itself a signal, plus proof the change is additive and non-interfering — anything written to the contract behaves identically. |
| **Away** — moves against what the contract specifies | Hard, and readily pushed back on | Measured evidence that the contract-literal behavior actually failed, behavior that fails loudly rather than resolving quietly toward success, and a ledger entry recording the deviation. |

Three postures, not two, and *resisted* is not *forbidden* — the middle tier is a toll, not a wall, and most extensions worth having pass through it and pay it. **Name the tier in the PR.** A tier is a claim a reviewer can disagree with, which is only possible if it was stated; an unstated tier defaults in practice to the cheapest one.

**If you see something, do something.** A vision cannot only be examined when someone sits down to examine it. During *any* work in the repo — including work with nothing to do with the vision — watch for observations against the currently documented vision: the repo drifting from what the page says, the page drifting from what the team actually believes, a shipped surface contradicting a stated principle. Capture them **without derailing the work at hand** — a labeled issue citing the passage it bears on, plus an `## Observations` heading in the PR body when one arises mid-PR. That heading is honest when empty: "none arose" is a finding, not a blank to fill. Observations never gate the work that surfaced them, which is exactly what makes the duty affordable — one issue, not a detour. Triage them periodically and *as a set*, because one observation is often noise and five are a pattern, and record the outcome as one of three: an amendment to the vision, a filed work item, or a decline that says why it changes nothing. A declined observation that says why keeps most of its value; a silently-closed one is lost.

**Defend against drift in layers.** No single check sees every class of drift, because each layer is blind to what the next one up is looking at. Adopt only the layers your repo has a real source for — a repo with no upstream contract has no vendored truth and should not invent one.

| Layer | What it is | The class it catches that the others cannot |
|-------|------------|---------------------------------------------|
| Vendored truth | The governing contract copied into the repo and pinned to a known upstream revision | Disagreement about what the contract actually says. Upstream movement becomes an event that re-opens every decision resting on the old text. |
| Deterministic guards | Tests pinning each load-bearing doc claim to **its source in code** | Docs going stale. A page-only assertion ("the page says 500") passes forever and fails only when someone edits the page — the one case needing no guard. The assertion has to read the value from the code and fail when the *code* moves. |
| An executable conformance matrix | One row per normative statement: the verbatim contract text, the decided disposition, the ledger entry licensing it, and an assertion that fails when the behavior moves | Unrecorded movement **in either direction** — including drifting silently *back* into conformance at a point where the ledger says the deviation was deliberate. Drift is any movement not recorded in the ledger. A flipped assertion names the ledger entry that must change; editing the assertion to match the new behavior is the one illegal exit. |
| Periodic holistic review | A semantic read of the whole — docs, examples, guidance surfaces, ledgers — against the contract *and* against the stated vision, with the collected observations as a named input | What no local check can see: a README teaching one mental model while an example demonstrates another; records individually well-formed and collectively obsolete. |
| A meta-protocol | The rules governing the layers themselves | Machinery accreting. Amendments need measured evidence; every guard names its retirement condition; machinery whose condition has fired is **removed, not deprecated**. |

The last row is the one most often skipped, and it is the one that keeps the other four affordable. Every guard costs runtime, review attention, and friction on every unrelated change that has to walk past it. Yesterday's scaffolding is today's tax, and a protocol that cannot delete its own machinery only grows.

**A working exemplar.** [`microsoft/amplifier-bundle-attractor`](https://github.com/microsoft/amplifier-bundle-attractor) runs this pattern against the upstream [`strongdm/attractor`](https://github.com/strongdm/attractor) spec, and its files read as a reference implementation: [`docs/VISION.md`](https://github.com/microsoft/amplifier-bundle-attractor/blob/main/docs/VISION.md) (the vision and its amendment meta-protocol), [`docs/QUALITY_PROTOCOL.md`](https://github.com/microsoft/amplifier-bundle-attractor/blob/main/docs/QUALITY_PROTOCOL.md) (the decision matrix in section 3, the observation convention in 4, the five layers in 5, the meta-protocol and its self-ablation rule in 7), [`SPEC_CONFORMANCE.md`](https://github.com/microsoft/amplifier-bundle-attractor/blob/main/SPEC_CONFORMANCE.md) (the compatibility doctrine and the deviation ledger it decides), and [`specs/conformance/attractor-matrix.yaml`](https://github.com/microsoft/amplifier-bundle-attractor/blob/main/specs/conformance/attractor-matrix.yaml) (the executable matrix). Read it for the shape rather than the specifics: its lint rules, ledger formats, guard files and pinned upstream revision are that repo's local answers to general questions — *what is your normative source, what pins your claims to it, and what is the record of deliberate deviation?* Every repo's answers look different. Its maintainers have offered to help seed a customized version for another repo — open an issue there.

---

## Author Guidance (If You Own a Repo)

Write down what the next agent or contributor needs to know. The audience has no prior context.

**In `AGENTS.md`:** the test command(s) that must pass before "done"; smoke-test invocations with exact commands; common pitfalls (bugs that bit you more than once); what "done" looks like for a typical change; non-obvious environment setup.

**In `.github/PULL_REQUEST_TEMPLATE.md`:** the verification checklist a PR body must address; evidence requirements (logs, screenshots, smoke output); links to the gates and their commands.

**In `KNOWN_ISSUES.md`:** issues you are deliberately deferring, each with one line on *why not now*; intent and good ideas for future work that would address shortcomings of the current system. Not a workaround log for current bugs (those are pitfalls in `AGENTS.md`), and not a dumping ground — an entry goes in only after the user confirms it is worth keeping.

**In `VISION.md` (only if your repo steers against a governing contract):** the end state it is converging toward, stated as though already true, and what it deliberately resists. Keep status out of it — what shipped belongs in the issue queue and in your ledgers, not here. See *Steering Toward a Governing Contract* above for the pattern and a worked example.

**Keep them short.** Twenty lines of actionable checklist beat two hundred lines of philosophy. Philosophy lives here in foundation; specifics live in your repo. **Update them when you learn something** — a bug that bit twice is a pitfall worth documenting; a new gate belongs in the template. These are living documents, and keeping them current is not the owner's job alone: every contributor captures lessons back as the work happens (see *Capturing what you learned* above).

---

## Cross-References

- [AGENT_AUTHORING.md](AGENT_AUTHORING.md) — how to author agents, including the discovery behavior described above.
- [PR_REVIEW_GUIDE.md](PR_REVIEW_GUIDE.md) — how to review PRs, using the repo's template as the baseline.
- [CONCEPTS.md](CONCEPTS.md) — foundation concepts that apply across all repos.
- `REPOSITORY_RULES.md` (in the `amplifier` repo) — the canonical ecosystem awareness rules: which repository types may know about which others. The authority for routing captured lessons across repo boundaries.

---

## TL;DR

Before — and during — work in a repo, read its conventions. The always-loaded files (`AGENTS.md`, `.github/PULL_REQUEST_TEMPLATE.md`, `CONTRIBUTING.md`) set the baseline; the contextual files (`VISION.md`, `PRINCIPLES.md`, `SMOKE_TESTS.md`, `KNOWN_ISSUES.md`) carry phase-specific knowledge. Read what exists before you start, and **re-read the relevant files at each major shift in what you are doing** — they fall out of context, and each reads differently through the current lens. Honor the PR template, run the gates, and when the repo's rules contradict your defaults, the repo wins. If the repo steers against a governing contract — an upstream spec, a schema, an API contract, a charter — check which way your change moves relative to it, and name that direction in the PR. Before you call work done, **capture the lessons it surfaced back into the file that owns them** — routing across repos only where awareness rules allow.

The repo owner has learned things you have not. Read what they wrote — and keep reading it.
