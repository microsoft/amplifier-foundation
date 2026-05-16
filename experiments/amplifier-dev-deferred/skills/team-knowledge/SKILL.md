---
name: team-knowledge
description: "Use when about to query or publish to the team knowledge base -- capabilities, people, conventions. Covers search-before-build, naming, sync semantics, and category selection."
---

> *Loaded on demand. The full guidance below is not in your default context --
> you reached for this skill because the situation called for it. Treat the
> sections that follow as authoritative for the duration of this task.*

## Team Knowledge Base

The team knowledge base is your team's shared memory — it captures what your team
builds, who works on what, and what expertise exists across the group. It makes
your team's collective knowledge searchable and accessible to every team member's
AI assistant.

### Why It Matters

Without this, every AI session starts from zero — no knowledge of what the team
has built, no awareness of who can help with what, no institutional memory. With
it, when someone asks "do we have anything for authentication?", the answer is
instant.

### How to Explain It to Users

When introducing or discussing the knowledge base, frame it in terms of what the
user gains:

| Instead of saying... | Say... |
|---|---|
| "Let's add your capability entries" | "Let's get your projects into the team's shared knowledge so your teammates can find them" |
| "I'll generate capabilities from your repos" | "I'll scan your repos to see what you're working on and add that to the team's shared knowledge" |
| "The manifest has been updated" | "Your team can now see what you're working on" |
| "Let's publish an entry" | "Let's document this so your team can find and build on it" |
| "You have 20 capabilities" | "The team has 20 projects and areas of expertise tracked" |

**Rule:** Never use the term "capability entry" with users. Internally, the system
calls them capabilities, but to users they are "projects", "expertise", "what you
work on", or "your team's knowledge."

Mirror the user's own language. If they say "add my repos", use "repos." If they
say "what does the team know", use "know." Don't introduce system jargon they
didn't use.

**Anti-pattern:** "I've generated 3 capability entries and updated the manifest."
**Correct:** "I scanned your repos and found 3 projects to add to the team knowledge.
Your teammates' AI assistants will now be able to find these when searching."

### Language Guide

| Internal term | User-facing language |
|---|---|
| capability / capability entry | "project", "expertise", "what you work on" |
| manifest | "team directory" or just "the knowledge base" |
| generate | "scan your repos" or "import your projects" |
| publish | "share with the team" or "add to the knowledge base" |
| push / pull | "sync with the team" or "share your updates" |
| profile | "your info in the team" |
| category: person | "team member" or "teammate" |
| category: capability | "project" or "area of expertise" |

---

### Configuration

The tool is controlled by four configuration knobs in the bundle config:

| Knob | Type | Purpose |
|------|------|---------|
| `enabled` | bool | Enables or disables the tool entirely. Set to `false` to turn off without removing configuration. |
| `repo_path` | string | Path to the team knowledge base repository on disk. Required for the tool to function. |
| `context_injection` | bool | When `true`, automatically injects relevant knowledge base context into sessions at startup. |
| `auto_generate` | bool | When `true`, automatically scans and updates capabilities after git operations without manual prompting. |

---

### Interface Quick Reference

All operations go through the `team_knowledge` tool:

| Task | Operation | Example |
|------|-----------|---------| 
| Search capabilities | `search` | `team_knowledge(operation="search", query="...")` |
| List / browse | `list` | `team_knowledge(operation="list")` |
| Look up details | `lookup` | `team_knowledge(operation="lookup", name="...")` |
| Publish a convention | `publish` | `team_knowledge(operation="publish", name="...", content={...})` |
| Get KB path and metadata | `info` | `team_knowledge(operation="info")` |
| Scan repos → full generate | recipe | See **Generating Team Knowledge** below |

**Rule:** All knowledge base query/publish operations go through the `team_knowledge` tool. For generating knowledge from repos, use the recipe.

### Local vs Shared

Publishing and generating save to your **local** knowledge base. Changes sync
automatically via the git hook after git operations — no manual push step
required in normal workflows.

How auto-sync works:
1. After `publish` or the generate recipe — changes are saved locally
2. After a git commit/push — the hook syncs automatically to the team
3. No manual sync steps needed — everything is automatic

**Anti-pattern:** "Published! You need to manually push to share this." ← Hook handles it.
**Correct:** "Added to the team knowledge base. Your teammates' AI assistants will find this after your next git push."

---

### Protected Data — NEVER Edit Directly

The team knowledge data lives in `~/.amplifier/team-knowledge/`.

**NEVER read, write, create, or modify files under this path directly.**

Always use the tool operations or the generate recipe:
- To add projects: use the `generate-team-knowledge` recipe (see **Generating Team Knowledge** below)
- To share info: `team_knowledge(operation="publish", name="...", content={...})`
- To find info: `team_knowledge(operation="search", query="...")`
- To check configuration: `team_knowledge(operation="info")`

**Why this matters:** The tool operations and recipe maintain data integrity — they update
the index, validate schemas, handle merge conflicts, and keep the knowledge base
consistent. Directly editing files bypasses all of this and will cause sync failures.

**Anti-pattern:** User says "add my repos like [person] has." Agent reads [person]'s
profile.yaml and replicates the pattern manually.
**Correct:** Use the `generate-team-knowledge` recipe which scans, generates, and
indexes everything correctly.

**Scope:** This constraint applies ONLY to `~/.amplifier/team-knowledge/` paths.
You can freely read and write files in the user's normal workspace repos.

**Exception:** Scan exclusion config files (see below) are user-edited files inside
the KB path. Reading and writing `scan-config.yaml` and `scan-config.local.yaml`
in `<kb_root>/.team-knowledge/` is allowed. The global config at
`~/.amplifier/team-knowledge/scan-config.yaml` is also user-edited.

---

### Scan Exclusion

Three-tier exclusion config, all merging (later tiers can negate earlier ones
with `!` patterns):

| Tier | File | Location | Purpose |
|------|------|----------|---------|
| Global | `scan-config.yaml` | `~/.amplifier/team-knowledge/` | Exclusions across all teams |
| Shared | `scan-config.yaml` | `<kb_root>/.team-knowledge/` | Team-agreed exclusions (committed) |
| Personal | `scan-config.local.yaml` | `<kb_root>/.team-knowledge/` | Personal exclusions (gitignored) |

The `exclude:` key takes a list of patterns in `.gitignore` syntax. No absolute paths.

When a user asks to exclude folders from scanning:
1. Run `team_knowledge(operation="info")` to get `kb_root` and current config status
   (includes global, shared, and personal config paths and pattern counts)
2. Guide them to create/edit the appropriate config file:
   - Across all teams: `~/.amplifier/team-knowledge/scan-config.yaml`
   - For one team (shared): `<kb_root>/.team-knowledge/scan-config.yaml`
   - For one team (personal): `<kb_root>/.team-knowledge/scan-config.local.yaml`
3. Changes take effect on the next scan -- no restart needed

---

### Onboarding New Users

When a user needs to set up the knowledge base (hook says not configured, or user
asks to set up), follow this flow:

**Step 1: Explain the value** (don't skip this)
> "The team knowledge base is your team's shared memory. Once you're set up, your
> AI assistant will know what your team builds and who works on what — so when you
> need help or want to avoid duplicate work, it's instant."

**Step 2: Verify the setup**
```python
team_knowledge(operation="info")
```
This returns the configured KB path (`kb_root`), team name, and your handle.
If this fails, the `repo_path` config knob needs to be set first.

**Step 3: Import their projects**
Ask: "What repos do you work on? I'll scan them to get your projects into the
team knowledge."

First, get the KB root path:
```python
result = team_knowledge(operation="info")
kb_root = result["kb_root"]
```
Then run the generate recipe:
```
recipes(operation="execute",
  recipe_path="@team-knowledge:recipes/generate-team-knowledge.yaml",
  context={"kb_root": kb_root, "repos": "their-repos"})
```

**Step 4: Show them what they get**
```python
team_knowledge(operation="list")
```
Say: "Now when anyone on your team searches for [topic they work on], their AI
assistant will find you. And when you search, you'll find what everyone else is
working on too."

**Key principles:**
- Lead with value at every step, not mechanics
- Ask what repos they work on — don't guess or look at other people's profiles
- If they reference someone as an example ("like how [person] has it"), still use the tool flow
- End by demonstrating the payoff — show them a search result

---

### Tool Interface: team_knowledge

| Operation | When to use | Example |
|-----------|------------|---------|
| `search` | Find capabilities by meaning | `team_knowledge(operation="search", query="plugin architecture")` |
| `list` | Browse everything or filter by category | `team_knowledge(operation="list", category="capabilities")` |
| `lookup` | Get full details on a specific capability | `team_knowledge(operation="lookup", name="amplifier-chat")` |
| `publish` | Publish a team convention | `team_knowledge(operation="publish", name="...", content={...})` |
| `info` | Get KB root path, team name, and handle | `team_knowledge(operation="info")` |

### Layered Discovery

**Depth rule:** One named item → go deep (Layers 0→1→2 automatically).
Anything else → summaries only. A list of N results is the answer, not a
queue of N lookups.

**Layer 0 — Discovery:**
Use `search` or `list` to find what's relevant.

- `search(query="...")` — semantic search, returns ranked results with category
- `list()` — browse all entries (capabilities and people)
- `list(category="capability")` — filter to capabilities only
- `list(category="person")` — filter to people only

For browsing queries (no specific item named), present summaries and STOP.

**Layer 1 — Details:**
Use `lookup(name="...", category="...")` when the user named a specific item.
The `category` parameter is optional (auto-detected from manifest).

- Capability → description, repo (org/repo), type, owner, usage,
  dependencies, updated_at
- Person → handle, repos (org/repo), ownership (capability names)

Dependencies are capability names — you can lookup each one.
If results include artifact paths (`diagram:`), proceed to Layer 2 immediately.

**Layer 2 — Deep Detail:**
Loaded when the agent decides "this matters." Access artifact files referenced by
Layer 1 via `read_file` using paths returned by the tool — the tool resolves all
paths so you never need to know where the knowledge base lives.

- **Capability diagram** — if `lookup` returns a `diagram:` field, that is a
  semantic system architecture DOT file. Load it with `read_file(result["capability"]["diagram"])`.
  Use the `dot_graph` tool to traverse the graph structure for deeper analysis.
  Not all capabilities have diagrams.

- **Convention content** — `list(category="convention")` entries include a
  `path:` field pointing to the full markdown file (when the file exists).
  Load it with `read_file(entry["path"])`.
  Use conventions when the user asks about team standards, coding guidelines, or process documentation.

All Layer 2 paths are absolute and ready to pass directly to `read_file`.
Fields are omitted entirely when no artifact exists — check for the key before reading.

**Navigation between entities:**
- Capability's `owner` → lookup that person's profile
- Capability's `repo` → GitHub: https://github.com/{repo}
- Capability's `dependencies` → lookup each dependency
- Person's `ownership` → lookup any capability they own
- Person's `repos` → GitHub repos (org/repo format)

**Example 1 — finding existing work:**
1. search("plugin architecture") → finds "amplifierd-plugin-distro" (capability)
2. lookup("amplifierd-plugin-distro") → owner: samueljklee, repo: microsoft/amplifier-distro
3. lookup("samueljklee", category="person") → owns 6 capabilities, works on 4 repos

**Example 2 — finding who knows something:**
1. search("authentication") → finds "amplifierd-plugin-auth" (capability)
   and "samueljklee" (person, owns auth-related capabilities)
2. lookup("samueljklee", category="person") → see their full ownership list

---

## Publishing Conventions

Publish is how team knowledge gets shared conversationally. When a user wants
the team to know about a pattern, standard, or practice, publish captures it
as a convention in the knowledge base.

```python
team_knowledge(operation="publish",
    name="<kebab-case-name>",
    content={
        "description": "<one-liner for discoverability>",
        "body": "<full markdown convention text>",
        "authors": ["<github-handle>"]
    })
```

Always provide the complete body text. When updating an existing convention,
include the full updated content -- the tool replaces the body entirely.

---

## Generating Team Knowledge

To generate team knowledge (scan repos, write capabilities, rebuild index, and
optionally generate diagrams), use the `generate-team-knowledge` recipe — not
the tool directly. The `generate` operation has been removed from the tool.

**How to generate:**

Step 1: Call `team_knowledge(operation="info")` to get the resolved `kb_root` path:
```python
result = team_knowledge(operation="info")
kb_root = result["kb_root"]
```

Step 2: Execute the generate recipe:
```
recipes(operation="execute",
  recipe_path="@team-knowledge:recipes/generate-team-knowledge.yaml",
  context={"kb_root": "<kb_root from info>", "repos": "<user-specified repos>"})
```

Context variables for the recipe:
- `kb_root` (required): The resolved path from the info operation
- `repos` (required): Comma-separated repo paths or a folder containing repos
- `handle` (optional): GitHub handle (auto-detected if empty)
- `full_scan` (default: "true"): Rescan all repos regardless of SHA cache. Set "false" for CI/CD incremental runs.
- `diagrams` (default: "skip"): Controls diagram generation.
  "skip" -- don't generate diagrams (fast, default).
  "missing" -- generate for repos without an existing diagram.
  "all" -- regenerate all diagrams, overwriting existing ones.
- `auto_push` (default: "true"): Push changes to remote after commit. Set "false" to commit locally only.

**When to include diagrams:**
Only set diagrams to "missing" or "all" when the user explicitly asks for diagrams.
Default is "skip" -- most generate runs don't need diagrams.
- User says "generate with diagrams" or "add project with architecture diagram" → set `diagrams: "missing"`
- User says "regenerate all diagrams" or "refresh all diagrams" → set `diagrams: "all"`
- User says "generate" or "add project" without mentioning diagrams → leave default ("skip")
- Diagram generation is expensive (5-20 minutes per repo) -- only include when explicitly requested

**All generation goes through one recipe.** Do not call `generate-diagram-per-repo` or `generate-diagrams` directly. The `generate-team-knowledge` recipe handles everything: capabilities, profiles, index rebuild, optional diagrams, commit, and push.

