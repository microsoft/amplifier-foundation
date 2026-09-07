# `validate-bundle-repo` behavior-hygiene re-run on foundation main — BEFORE vs AFTER

Both runs execute the recipe's **own** `behavior-hygiene-validation` step body, extracted
verbatim and run with `VALIDATE_BUNDLE_REPO_PATH=<repo>`. Nothing re-implemented; **$0.00
API spend** (the step is deterministic Python, no LLM). Harness:
`docs/lanes/j05m-estimator-salvage/replay_step.py` (copied from lane `dfni`, unmodified).

Repo under test: `microsoft/amplifier-foundation` @ `aac89ea` (= `origin/main` at claim time).

Raw payloads: `behavior-hygiene-BEFORE-main.json`, `behavior-hygiene-AFTER-main-plus-fix.json`.

---

## BEFORE — recipe v3.13.0 (origin/main)

```
behaviors_checked 12   errors 0   warnings 2
WARN agents  context_tokens_high | context.include totals ~1000 tokens (>500 WARNING threshold)
WARN tasks   context_tokens_high | context.include totals ~1000 tokens (>500 WARNING threshold)
```

`~1000` is **2 includes × a flat 500-token guess**, landing exactly one token under the
`> 1000` ERROR gate. The true on-disk size of those same two files is **6,276 tokens
(chars/4) / 5,402 (o200k_base)** — see `tokenizer-calibration.txt`.

## AFTER — recipe v3.14.0 (estimator fix, this PR)

```
behaviors_checked 12   errors 2   warnings 1
ERR  agents  context_tokens_excessive | context.include totals ~6276 tokens (>1000 ERROR threshold)
ERR  tasks   context_tokens_excessive | context.include totals ~6276 tokens (>1000 ERROR threshold)
WARN foundation-expert context_tokens_high | context.include totals ~566 tokens (>500 WARNING threshold)
```

---

## Read this before merging — three honest consequences

1. **The two findings do not stay WARNINGs — they become ERRORs.** The goal text
   anticipated "the two `context_tokens_high` WARNINGs … now reporting the true ~6k
   magnitude". `6,276 > 1,000`, so they correctly escalate to `context_tokens_excessive`.
   The magnitude is the true one; the severity is the true one too.

2. **Therefore `validate-bundle-repo` now reports 2 ERRORs against foundation main.**
   That is the defect being surfaced, not introduced — the 6,276 tokens were already in
   every foundation-root system prompt; only the ruler changed. The fix that clears them
   is the delegation-context split held in **#369**, which this PR deliberately does not
   carry.

   **Checked, not assumed:** `validate-bundle-repo` is **not** wired into CI —
   `grep -rn "validate-bundle-repo" .github/ Makefile*` returns nothing. Merging this
   does not red the build.

3. **A third, previously invisible finding appears:** `behaviors/foundation-expert.yaml`
   includes `foundation:context/bundle-awareness.md`, which the old estimator also charged
   a flat 500 (→ silent). Measured, it is **566** → over the 500-token WARNING bar. New
   information, correctly surfaced, not a regression.
