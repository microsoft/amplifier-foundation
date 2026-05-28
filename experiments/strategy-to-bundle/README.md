# Strategy-to-Bundle

Generate complete Amplifier evaluation bundles from a strategy description.

One recipe takes a strategy name and a plain-English description of how agents
should interact, then produces a ready-to-use bundle with minimal token cost.

## What It Does

You describe a strategy:

> "Worker-critic-verifier pipeline. Worker implements changes, critic does
> read-only adversarial review, verifier runs tests. If critic rejects,
> worker revises. Max 3 revision cycles."

The recipe produces a complete bundle directory:

```
my-strategy/
  bundle.md           # Entry point with all infrastructure wired
  context/system.md   # Strategy-specific system prompt
  agents/worker.md    # One file per agent
  agents/critic.md
  agents/verifier.md
```

## How It Works

Four stages, one human decision point:

1. **Design** -- An LLM reads your strategy description plus domain constraints
   and produces a JSON mechanism spec. You review and approve the spec. This is
   the only human step.

2. **Generate** -- A Python script (`generate_bundle.py`) mechanically writes
   all bundle files from the spec. No LLM involved. Deterministic output.

3. **Compress and Converge** -- An LLM compresses text in all generated files
   to reduce token count. If a token measurement harness is available, measures
   actual tokens and loops until delta < 3% between passes (max 3 iterations).
   Without a harness, runs one compression pass.

4. **Deliver** -- Cleans up temporary artifacts, reports final token count and
   file listing.

## How to Run

In an Amplifier session:

```
recipes(operation="execute",
  recipe_path="experiments/strategy-to-bundle/recipes/strategy-to-bundle.yaml",
  context={
    "strategy_name": "critic-pipeline",
    "strategy_description": "Worker-critic-verifier pipeline. Worker implements changes. Critic does read-only adversarial review. Verifier runs tests. If critic rejects, worker revises. Max 3 cycles.",
    "output_dir": "./output",
    "domain_context_path": "experiments/strategy-to-bundle/bundle-creation-process.md"
  })
```

## Context Variables

| Variable | Required | What |
|---|---|---|
| `strategy_name` | Yes | kebab-case bundle name |
| `strategy_description` | Yes | 1-3 sentences: what agents exist, how they interact, what constraints matter |
| `output_dir` | Yes | Where to write the bundle directory |
| `domain_context_path` | Yes | Path to `bundle-creation-process.md` (this experiment's domain context) |
| `repo_root` | No | Root of repo containing a fingerprint harness. If empty, token measurement is skipped |

## Token Measurement

The recipe has built-in support for a fingerprint harness that measures real
token counts by running the bundle in an isolated DTU container and reading the
provider's own tokenizer output. This harness is not included in this
experiment -- it lives in a separate evaluation infrastructure repository.

**Without the harness:** The recipe still works end-to-end. It generates the
bundle, compresses it once, and reports "unmeasured" for token counts. The
generated bundle is fully functional.

**With the harness:** Set `repo_root` to the repo containing
`fingerprints/run.sh`. The recipe will measure tokens after each compression
pass and loop until convergence (delta < 3% between passes).

## Files

| File | What |
|---|---|
| `bundle-creation-process.md` | Domain context fed to the LLM during design. Contains baseline capabilities, token constraints, framework constraints, anti-patterns, and a full reference catalog of validated module source URLs |
| `recipes/strategy-to-bundle.yaml` | The 4-stage recipe orchestrating the pipeline |
| `recipes/generate_bundle.py` | Mechanical Python generator that writes bundle files from a JSON spec |

## Key Design Decisions

**Build from zero, not by subtraction.** The recipe does not start with
Foundation and remove things. It constructs a bundle from explicit module
declarations with validated source URLs.

**LLM for design only.** The LLM decides the mechanism spec (which agents,
what tools, what rules). File generation is mechanical. This prevents the LLM
from inventing infrastructure or deviating from validated patterns.

**Structural enforcement over instructions.** Tool restrictions are enforced
by what's declared in YAML frontmatter, not by prose telling agents "don't use
X." Observed sessions showed prose instructions were ignored under context
pressure; structural declarations were enforced every time.

**Compression targets authored text only.** The convergence loop compresses
system prompts, agent descriptions, and rules. It never touches the YAML
frontmatter (includes, tools, hooks, session config).
