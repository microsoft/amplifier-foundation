# Available Knowledge

This bundle keeps detailed guidance behind on-demand skills and expert agents.
Reach for them when the situation matches — don't guess.

## How to reach for knowledge
- Skill: `load_skill(skill_name="...")` for procedures and conventions.
  Use `load_skill(list=true)` or `load_skill(search="...")` to scan.
- Expert agent: delegate via the Task/agent dispatch when the work is heavy
  or domain-specialized. The agent loads its own deep context.
- Mode: `mode(operation="set", name="...")` when entering a sustained phase.

## When you need ___, do ___

**Delegating work or coordinating parallel agents**
→ skill `delegation-patterns` (taxonomy of when/how to delegate)
→ skill `multi-agent-patterns` (parallel coordination, fan-out/fan-in)
→ skill `model-routing` (choosing model_role for delegates)

**Working with bundles (composition, behaviors, agents-as-bundles)**
→ delegate to agent `bundle-design-expert` — full lifecycle expertise
→ skill `amplifier-recipes` if the task is recipe authoring

**Questions about the Amplifier ecosystem itself**
→ delegate to agent `foundation-expert` — ships with the inventory
→ skill `amplifier-ecosystem` for a quick map

**Editing files / using file tools well**
→ skill `editing-files`

**Python or other code work needing navigation / type info**
→ skill `code-intelligence` (LSP usage, incl. Python)
→ skill `python-development` (project conventions)

**Working in this repo specifically (amplifier-dev)**
→ mode `amplifier-dev` (sets up workflow, testing patterns, repo layout)
→ skill `amplifier-dev-map` (which repo holds what)
→ skill `amplifier-testing` (test patterns in this codebase)

**Isolated/ephemeral environments**
→ skill `digital-twin-universe` (DTU profiles, Incus)
→ skill `gitea` (ephemeral Git server)

**Browser automation, tester harness, MADE support**
→ skill `browser-automation`, `amplifier-tester`, `made-support`

**Superpowers philosophy and Amplifier idioms**
→ skill `using-superpowers` (idiom invocation)
→ skill `superpowers-philosophy` (when reasoning about design tradeoffs)

**Team knowledge: capabilities, people, conventions**
→ skill `team-knowledge`

## Default behavior
If a request maps to one of the situations above, load the skill or delegate
*before* answering. If unsure which knowledge applies, run
`load_skill(search="<keywords>")` first — that's cheaper than guessing.
