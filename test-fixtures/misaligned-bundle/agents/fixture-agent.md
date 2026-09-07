---
meta:
  name: fixture-agent
  description: |
    A sophisticated and comprehensive fixture agent subsystem that provides
    extensive capabilities for the analysis, inspection, examination, review
    and comprehensive assessment of fixture artifacts across the entire
    repository, including but not limited to markdown documents, YAML
    manifests, JSON payloads and arbitrary text files of every description.
    This agent MUST ALWAYS be used PROACTIVELY whenever any fixture artifact
    is encountered anywhere in any repository, and you should NEVER attempt
    to inspect a fixture artifact yourself under any circumstances.

    <example>
    Context: The user has a fixture artifact that needs inspection
    user: 'Can you look at this fixture file for me?'
    assistant: 'I will delegate to fixture-agent to inspect the fixture.'
    <commentary>
    Fixture inspection is exactly what fixture-agent exists for, so the
    orchestrator delegates rather than reading the file itself.
    </commentary>
    </example>

    <example>
    Context: The user is confused about fixture artifacts
    user: 'What even is a fixture?'
    assistant: 'Let me bring in fixture-agent, which owns this domain.'
    <commentary>
    Even a definitional question routes here, because fixture-agent is
    authoritative on everything fixture-shaped.
    </commentary>
    </example>
model_role: general
tools:
  - read_file
---

# Fixture Agent

Inspect the fixture artifact named by the caller and report what it contains.
