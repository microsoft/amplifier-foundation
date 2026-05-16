---
name: model-routing
description: "Use when about to delegate and need to pick a model_role (general, fast, coding, reasoning, etc.). Pairs with delegation-patterns. Maps task shapes to the routing-matrix roles."
---

> *Loaded on demand. The full guidance below is not in your default context --
> you reached for this skill because the situation called for it. Treat the
> sections that follow as authoritative for the duration of this task.*


This session uses the routing matrix system for model selection.

## Available Roles (13)

| Role | Description |
|------|-------------|
| `general` | Versatile catch-all, no specialization needed |
| `fast` | Quick utility tasks — parsing, classification, file ops, bulk work |
| `coding` | Code generation, implementation, debugging |
| `ui-coding` | Frontend/UI code — components, layouts, styling, spatial reasoning |
| `security-audit` | Vulnerability assessment, attack surface analysis, code auditing |
| `reasoning` | Deep architectural reasoning, system design, complex multi-step analysis |
| `critique` | Analytical evaluation — finding flaws in existing work |
| `creative` | Design direction, aesthetic judgment, high-quality creative output |
| `writing` | Long-form content — documentation, marketing, case studies, storytelling |
| `research` | Deep investigation, information synthesis across multiple sources |
| `vision` | Understanding visual input — screenshots, diagrams, UI mockups |
| `image-gen` | Image generation, visual mockup creation, visual ideation |
| `critical-ops` | High-reliability operational tasks — infrastructure, orchestration |

## For Agent Authors

Use `model_role` in agent frontmatter to declare what kind of model your agent needs:

```yaml
model_role: coding                           # single role
model_role: [ui-coding, coding, general]     # fallback chain (specific → general)
model_role: fast                             # utility agent
```

Fallback chains are tried left-to-right. Always end with `general` or `fast`.

## For Delegating Agents

When delegating to sub-agents, you can override the model role:

```json
{
  "agent": "foundation:explorer",
  "instruction": "Analyze these UI screenshots...",
  "model_role": "vision"
}
```

For detailed role definitions, decision flowchart, model tier grid, and fallback chain guidance, use `load_skill(skill_name='role-definitions')`.

