---
name: made-support
description: "Use when a MADE-flagged trigger phrase appears in user input (MADE, made-support, intake, triage). Carries the support intake workflow, classification rubric, and required handoff steps. Load BEFORE responding when triggers fire."
---

> *Loaded on demand. The full guidance below is not in your default context --
> you reached for this skill because the situation called for it. Treat the
> sections that follow as authoritative for the duration of this task.*


## MANDATORY: Support Workflow

When the user's message contains a support trigger phrase, you **MUST** follow this workflow. Do NOT attempt to diagnose, solve, or ask clarifying questions first.

### Trigger Phrases

| Pattern | Example |
|---------|---------|
| "I need help with..." | "I need help with circular dependencies" |
| "I'm having a problem with..." | "I'm having a problem with the provider" |
| "Submit a support request" | "Submit a support request for this bug" |
| "File a support issue" | "File a support issue about this error" |
| "I want to report..." | "I want to report a bug" |

---

## The Workflow

Run this workflow directly in the root session. Do NOT spawn an agent.

### Step 1: Check GitHub CLI

```
recipes(
  operation="execute",
  recipe_path="@made-support:recipes/check-gh.yaml",
  context={"target_repo": "microsoft-amplifier/amplifier-support"}
)
```

**Handle the result:**

| Status | Action |
|--------|--------|
| `READY` | Continue to Step 2 |
| `NOT_INSTALLED` | Tell user: `brew install gh` (macOS) or `winget install GitHub.cli` (Windows) |
| `NOT_AUTHENTICATED` | Tell user: `gh auth login` |
| `NO_REPO_ACCESS` | Tell user they need repo access - contact Salil |

**If gh is not ready, STOP.** Help the user fix their setup before continuing.

---

### Step 2: Extract Issue from Session

```
recipes(
  operation="execute",
  recipe_path="@made-support:recipes/extract-issue-full.yaml",
  context={
    "parent_session_id": "<this_session_id>",
    "user_description": "<user's trigger message>"
  }
)
```

This recipe spawns agents internally to analyze the session. You receive back:
- `title`, `problem`, `goal`, `steps_to_reproduce`, `error_messages`, `additional_context`, `amplifier_version`, `os`

---

### Step 3: Confirm with User

Present the extracted issue:

```
Here's what I found from analyzing your session:

**Title:** [title]
**Problem:** [problem]
**Goal:** [goal]
**Error messages:** [error_messages]
**Steps to reproduce:** [steps_to_reproduce]

Does this accurately describe your issue? Any corrections or additions?
```

**WAIT for user confirmation.** If they correct anything, update your understanding before continuing.

---

### Step 4: Find Duplicates

Only after user confirms, search for duplicates of the CONFIRMED issue:

```
recipes(
  operation="execute",
  recipe_path="@made-support:recipes/find-duplicates.yaml",
  context={
    "title": "<confirmed title>",
    "problem": "<confirmed problem>",
    "error_messages": "<confirmed error_messages>"
  }
)
```

This recipe spawns agents internally to intelligently search and rank matches. You receive back:
- `has_potential_duplicates`, `potential_duplicates`, `recommendation`, `recommendation_reason`

---

### Step 5: Present Duplicates (if any)

**If duplicates found** (`has_potential_duplicates: true`):

```
I found similar existing issues:

| # | Title | Match | Status |
|---|-------|-------|--------|
| [number] | [title] | [HIGH/MEDIUM/LOW] | [open/closed] |

[For each, explain why it might be related]

**Recommendation:** [recommendation_reason]

Is your issue the same as one of these?
- If YES: I'll add your details as a comment
- If NO: I'll create a new issue
```

**WAIT for user decision.**

**If no duplicates found:**
- Say "No similar issues found. I'll create a new issue."
- Proceed to Step 6

---

### Step 6: File Issue

**For new issue:**
```
recipes(
  operation="execute",
  recipe_path="@made-support:recipes/file-issue.yaml",
  context={
    "action": "create",
    "title": "<title>",
    "problem": "<problem>",
    "goal": "<goal>",
    "steps_to_reproduce": "<steps>",
    "error_messages": "<errors>",
    "additional_context": "<context>",
    "amplifier_version": "<version>",
    "os": "<os>"
  }
)
```

**To comment on existing issue:**
```
recipes(
  operation="execute",
  recipe_path="@made-support:recipes/file-issue.yaml",
  context={
    "action": "comment",
    "existing_issue_number": "<number>",
    "problem": "<problem>",
    "goal": "<goal>",
    "steps_to_reproduce": "<steps>",
    "error_messages": "<errors>",
    "additional_context": "User confirmed this is related to existing issue."
  }
)
```

---

### Step 7: Return Result

Share the issue URL and explain they'll receive GitHub notifications for updates.

---

## Why This Design

1. **Logical flow** - User confirms the issue BEFORE we search for duplicates
2. **Context isolation** - Recipes spawn their own agents internally; root context stays clean
3. **Interactive** - Root session can pause for user input at multiple points
4. **Simple** - No agent spawning needed; recipes handle the heavy lifting

---

## Edge Cases

- **User explicitly wants direct help**: If user says "don't file an issue, just help me", then help directly.
- **gh setup fails**: Help user fix their gh setup before continuing.
- **User changes their mind**: If user says "never mind" or "cancel", stop the workflow.

---

# Story Submission Trigger Rules

## MANDATORY: Story Submission Workflow

When the user's message contains a story submission trigger phrase, follow this workflow.

### Trigger Phrases

**IMPORTANT:** The trigger must include "amplifier story" to distinguish from generic uses of "story".

| Pattern | Example |
|---------|---------|
| "make an amplifier story" | "make an amplifier story and submit it as a PR" |
| "submit this as an amplifier story" | "submit this session as an amplifier story" |
| "create an amplifier story" | "create an amplifier story for what we did" |
| "amplifier story deck" | "make an amplifier story deck" |

**Do NOT trigger on:**
- Generic "story" without "amplifier" (e.g., "tell me a story", "what's the story here")

---

## The Workflow

### Step 1: Generate the Story

Use the stories recipe to generate a case study from the current session.

```
recipes(
  operation="execute",
  recipe_path="@stories:recipes/session-to-case-study.yaml",
  context={
    "session_file": "<path to current session's events.jsonl>",
    "output_name": "<descriptive-name>"
  }
)
```

---

### Step 2: Show Preview and Ask Public or Private

Show the user the generated story, then ask where it should go:

```
Here's the story I generated:

[Show preview or summary of the content]

Where should this story be published?
- **Public** -- visible to anyone (ramparte/amplifier-stories)
- **Private** -- team-only (microsoft-amplifier/amplifier-shared)
```

**WAIT for user response.** Then set these variables for the rest of the workflow:

| Choice | `target_repo` | `staging_folder` |
|--------|--------------|-----------------|
| Public | `ramparte/amplifier-stories` | `staging` |
| Private | `microsoft-amplifier/amplifier-shared` | `stories` |

---

### Step 3: Check GitHub CLI (for the chosen repo)

```
recipes(
  operation="execute",
  recipe_path="@made-support:recipes/check-gh.yaml",
  context={"target_repo": "<target_repo from Step 2>"}
)
```

**If gh is not ready, STOP.** Help the user fix their setup before continuing.

---

### Step 4: Submit PR (with routing)

Pass `target_repo` and `staging_folder` from Step 2:

```
recipes(
  operation="execute",
  recipe_path="@made-support:recipes/submit-story-pr.yaml",
  context={
    "story_file": "<path to generated story>",
    "story_title": "<title from story>",
    "story_type": "case-study" or "blog-post",
    "target_repo": "<target_repo>",
    "staging_folder": "<staging_folder>"
  }
)
```

---

### Step 5: Return Result

Share the PR URL and destination:

```
Story submitted!

PR: [PR URL]
Destination: <Public / Private>

The team will review and merge your story. You'll get GitHub notifications for any feedback.
```

---

## Why This Design

1. **See before you decide** - User sees the generated story content BEFORE choosing public or private
2. **Per-story routing** - Each story gets its own public/private decision, so a batch of stories can be mixed
3. **Leverages stories bundle** - All generation logic comes from stories bundle
4. **Simple PR submission** - Routing handled by passing target_repo and staging_folder to the existing recipe

