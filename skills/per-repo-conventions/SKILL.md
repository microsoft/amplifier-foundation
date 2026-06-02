---
name: per-repo-conventions
description: >
  Use when starting work in any repository, and again at each work-phase
  transition (design, coding, debugging, verification, opening a PR). Loads the
  canonical per-repo-conventions guidance from
  foundation:docs/PER_REPO_CONVENTIONS.md — the discovery pattern for AGENTS.md,
  PR templates, CONTRIBUTING.md, and contextual files (PRINCIPLES.md,
  SMOKE_TESTS.md, KNOWN_ISSUES.md), plus the re-read cadence and verification
  gradient.
---

# Per-Repo Conventions

Before writing code, debugging, verifying, or opening a PR in any repository,
discover and honor that repo's local conventions. You are a guest — read the
house rules before changing the furniture.

**Read `foundation:docs/PER_REPO_CONVENTIONS.md` and follow it.** Re-read the
relevant parts at each major shift in what you are doing (design →
implementation, investigation → fix, coding → verification → PR): earlier reads
fall out of context, and each file reads differently through the lens of the
current objective.

That document is the single source of truth. It covers discovery order (repo
root + subdirectory walk, most-specific-wins), the always-loaded files
(`AGENTS.md`, `.github/PULL_REQUEST_TEMPLATE.md`, `CONTRIBUTING.md`,
`README.md`), the contextual files (`PRINCIPLES.md`, `SMOKE_TESTS.md`,
`KNOWN_ISSUES.md`), when to read and re-read them, the verification gradient,
and author guidance.
