# Bundle Lifecycle: Specs, Models, and Implementation

This document defines the artifacts and workflows for building and improving Amplifier bundles. Every bundle goes through a lifecycle. Skipping stages leads to bundles that break in scenarios nobody anticipated.

---

## The Three Artifacts

### Mechanism Spec

A **mechanism spec** is a prose document that records your design decisions. It names every mechanism in the bundle, its purpose, where it attaches, and how it interacts with other mechanisms.

A spec answers: "What mechanisms did I choose, and why?"

**Contents:**
- Each mechanism (mode, agent, skill, hook, recipe) with its name and purpose
- Tool policies per mode (what's safe, warned, blocked)
- Agent model roles and delegation boundaries
- Context architecture decisions (what loads where, token estimates)
- Design rationale -- why this mechanism and not another

**When to write one:** After mechanism selection (using the design guide), before generating a behavioral model. The spec is the output of your design thinking, written down so it can be verified.

A spec is NOT implementation. It contains no YAML, no file paths, no code. It describes intended behavior, not file structure.

### Behavioral Model

A **behavioral model** is a structured document (9 standard sections) that describes how a bundle's mechanisms are expected to behave at runtime. It is the **verification artifact** between design and implementation.

A model answers: "Given these mechanisms, what actually happens in real usage scenarios?"

**The 9 sections:**
1. **Overview** -- bundle identity, component inventory, objectives served
2. **Tool Governance** -- tool availability matrix per mode, enforcement rules
3. **Mode Behaviors** -- what each mode does, transitions, tool policies
4. **Agent Behaviors** -- agent roles, delegation patterns, context loading
5. **Skill Behaviors** -- on-demand knowledge, invocation triggers
6. **Context & Cross-Cutting Concerns** -- token budgets, context flow, delegation chains
7. **Recipe Workflows** -- multi-step pipelines, approval gates, data flow
8. **Behavioral Scenarios** -- concrete usage stories exercising the mechanisms
9. **Assumptions & Gaps** -- what the design doesn't cover, validation checkpoints

**Why you need one:** A spec says what you intended. A model shows what would actually happen. The gap between intent and reality is where bugs live. Scenarios expose that gap before you write any YAML.

**Caveat on scenario coverage:** Behavioral model scenarios are LLM-generated and may not cover all edge cases or failure modes. Treat the model as a verification aid that catches common design problems early, not as exhaustive proof of correctness. Critical or high-risk interactions should still be validated through manual review and real-world testing.

**When you don't need one:** Trivial bundles (a single agent with no modes, no recipes, no complex interactions) may not benefit from formal modeling. Use judgment -- if the bundle has mechanism interactions (modes + agents, recipes + skills, etc.), model it.

### Implementation

The actual bundle files: `bundle.md`, behavior YAMLs, agent `.md` files, context files, mode files, skill files, recipe YAMLs, and documentation.

Implementation answers: "Here are the files that make this bundle work."

**When to write it:** After the behavioral model has been reviewed and the scenarios verified. Not before.

---

## Why the Model Step Matters

Jumping from spec to implementation is the most common mistake in bundle design. It feels efficient -- you know what you want, so just write it. But it skips the step where you discover:

- A mode blocks a tool that an agent needs
- Two agents have overlapping scope with no clear delegation rule
- A skill loads 5K tokens that the user will rarely need
- A recipe's approval gate fires at the wrong point in the workflow
- An edge case (failed delivery, missing config, mode transition during operation) has no defined handling

These are not hypothetical. They are the exact class of problems that surface when you generate scenarios and read them carefully. A 30-minute model review prevents days of debugging after implementation.

**The anti-pattern:** "I know what I want, let me just write the YAML."
**The correct pattern:** "I know what I want. Let me verify it handles the cases I care about before I write anything."

---

## Workflows

### Building a New Bundle

```
Objectives
   |
   v
Mechanism Design (using the design guide)
   |
   v
Mechanism Spec (write down your decisions)
   |
   v
Behavioral Model (generate with spec-to-behavioral-model recipe)
   |
   v
Review Scenarios <-- Are the user's cases handled?
   |                   No -> revise spec, re-model
   v Yes
Implementation (write the bundle files)
```

**Step by step:**

1. **Start with objectives.** What should this bundle do? Who is it for? Write these down, even informally.

2. **Design mechanisms.** Use `designing-with-mechanisms.md` and the decision tree to select the right mechanisms. Don't default to "agent for everything" -- consider modes, skills, hooks, and recipes.

3. **Write a mechanism spec.** Record your choices: which mechanisms, what each one does, how they interact, what tool policies you need. This is a prose document, not YAML.

4. **Generate a behavioral model.** Run `spec-to-behavioral-model` recipe. This produces the 9-section model with scenarios.

5. **Review the scenarios.** This is the verification gate. Read each scenario and ask: "Is this what I want to happen?" Pay special attention to edge cases and cross-component interactions. If a scenario is wrong, revise the spec and re-generate.

6. **Implement.** Once the model looks right, build the actual bundle files. The model serves as your specification -- what each mechanism should do is already documented.

**Shortcut for simple bundles:** If you have clear objectives but haven't done mechanism selection yet, you can run `objectives-to-behavioral-model` to combine steps 2-4. The recipe designs mechanisms AND generates a model. You still review the scenarios before implementing.

### Improving an Existing Bundle

```
Existing Bundle
   |
   v
Generate Model (bundle-behavioral-model recipe)
   |
   v
Review Scenarios <-- Which scenarios fail or are missing?
   |
   v
Write Change Spec (what needs to change and why)
   |
   v
Model the Changes (change-spec-to-behavioral-model recipe)
   |    Takes BOTH: existing bundle composition + change spec
   |    Produces: merged model showing full system after changes
   v
Review Updated Scenarios <-- Do changes fix the issues
   |                           without breaking existing behavior?
   |                           No -> revise change spec, re-model
   v Yes
Implement Changes
```

**Step by step:**

1. **Model the existing bundle.** Run `bundle-behavioral-model` recipe with `registry_path: ~/.amplifier/registry.json`. This produces a model of what the bundle currently does, including scenarios.

2. **Identify failing scenarios.** Read the model diagnostically. Which scenarios don't work the way users expect? Which edge cases are unhandled? Which mechanisms interact badly? The model's "Assumptions & Gaps" section often surfaces these directly.

3. **Write a change spec.** Document the proposed changes: what mechanisms are being added, removed, or modified, and why. This is NOT a full spec from scratch -- it describes only the delta.

4. **Model the changes in context.** Run `change-spec-to-behavioral-model` with the bundle name, registry path, and change spec path. This recipe reads the existing bundle's full composition AND the change spec, then produces a merged model showing the complete system after changes. It includes impact analysis, regression risks, and scenarios that exercise both preserved and changed behavior.

5. **Review the merged model.** Check that the changes fix the identified issues without breaking existing scenarios. The model's impact analysis section flags regression risks and interaction effects. The scenarios are categorized as PRESERVED, NEW, MODIFIED, or BOUNDARY to make review targeted.

6. **Implement.** Apply the changes to the existing bundle files.

This loop works for any bundle improvement: adding new capabilities, fixing behavioral issues, refactoring mechanism choices, or extending coverage.

---

## Choosing the Right Recipe

| Situation | Recipe | Input |
|-----------|--------|-------|
| Starting from scratch with requirements | `objectives-to-behavioral-model` | Objectives document |
| Have a mechanism spec, need to verify before building | `spec-to-behavioral-model` | Mechanism spec document |
| Have an existing bundle, need to understand it | `bundle-behavioral-model` | Bundle name + `registry.json` path |
| Proposing changes to an existing bundle | `change-spec-to-behavioral-model` | Bundle name + `registry.json` + change spec document |

**The common patterns:**

- **New bundle, clear vision:** objectives -> `objectives-to-behavioral-model` -> review -> implement
- **New bundle, complex design:** objectives -> design guide -> spec -> `spec-to-behavioral-model` -> review -> implement
- **Improve existing bundle:** `bundle-behavioral-model` -> identify gaps -> write change spec -> `change-spec-to-behavioral-model` -> review scenarios + impact -> implement
- **Understand a bundle you didn't write:** `bundle-behavioral-model` -> read the model

---

## The Verification Gate

The model review step is the critical gate. It is where you catch design problems before they become implementation problems.

**What to check in each scenario:**

- Does the user get the outcome they expect?
- Are the right tools available (and the wrong ones blocked)?
- Does delegation go to the right agent?
- Does the mode transition make sense?
- Is context loaded efficiently (not too much, not too little)?
- What happens when something fails?

**Use the Mechanism Spec Review Checklist** (in `designing-with-mechanisms.md`) as a structured verification tool. It covers mechanism assignment, enforcement levels, context economics, execution design, and implementation anti-patterns.

**The gate rule:** Don't implement until you've read every scenario in the model and confirmed it matches your intent. If any scenario is wrong, the fix is cheap (revise the spec) rather than expensive (rewrite the implementation).

---

## Common Mistakes

| Mistake | Why It Happens | What To Do Instead |
|---------|---------------|-------------------|
| Spec -> Implementation (skip model) | "I already know what I want" | You know the intent, not the interactions. Model it. |
| Objectives -> Implementation (skip everything) | "It's a simple bundle" | If it has mechanism interactions, model it. If it's truly simple, fine. |
| Model without reviewing scenarios | "The recipe ran, we're done" | The recipe generates scenarios. Reading them IS the value. |
| Treating model as documentation | "Nice to have for the wiki" | The model is a verification tool, not documentation. Use it to find problems. |
| One model, never updated | "We modeled it when we built it" | Re-model when you change the bundle. The model should reflect current design. |
