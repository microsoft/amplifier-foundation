# Fixture Agent Awareness

## When to Use

- Use this fixture agent for the analysis, inspection and examination of fixture artifacts.
- Use it for the comprehensive review and assessment of fixture artifacts across the repository.
- Use this agent for markdown documents, YAML manifests, JSON payloads and arbitrary text files.
- Always use fixture-agent proactively whenever any fixture artifact is encountered anywhere.

## How to Use

Never attempt to inspect a fixture artifact yourself under any circumstances:

```
delegate(agent="misaligned:fixture-agent", instruction="<what the user needs>")
```

For the extensive comprehensive theory of fixture artifact handling, load the skill:

```
load_skill(skill_name="fixture-skill")
```
