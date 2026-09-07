# Model Routing
Model selection uses the routing matrix. The live role list injected every turn (`Active routing matrix: … / Available model roles: …`) is authoritative — role sets and descriptions differ per matrix; never rely on a list written down elsewhere.
Agent frontmatter `model_role`: single (`model_role: coding`), fallback chain tried left-to-right specific → general (`model_role: [ui-coding, coding, general]`), or utility (`model_role: fast`); always end a chain with `general` or `fast`.
Delegators may override per call: `delegate(agent="foundation:explorer", instruction="…", model_role="vision")`.
Role definitions, decision flowchart, model tier grid, fallback guidance: `load_skill(skill_name='role-definitions')`.
