# Skills (this bundle)

Skills are domain knowledge packages, loaded on demand. In this bundle the
skill catalog is **not** auto-injected each turn -- the Discovery Layer
(`Available Knowledge`) lists what exists, and you reach for what fits.

**Operations** (`load_skill` tool):
- `load_skill(list=true)` -- enumerate every available skill (call this
  once early if you're unsure what's loaded; cheaper than guessing).
- `load_skill(search="keyword")` -- filter by keyword.
- `load_skill(info="skill-name")` -- metadata only, no body.
- `load_skill(skill_name="skill-name")` -- load the full skill content.

Skills follow progressive disclosure: the body you load is Level 2; some
skills reference companion files (Level 3) which they tell you to open
with `read_file` as needed.

Default behavior: if a request maps to one of the situations in
`Available Knowledge`, **load the skill before answering**.
