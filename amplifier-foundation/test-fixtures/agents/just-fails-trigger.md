---
meta:
  name: just-fails-trigger
  description: |
    Use when testing threshold boundaries. This description is exactly long
    enough but uses passive language without any strong imperative triggers.
    
    <example>
    Context: Testing threshold boundaries
    user: 'Test the just-fails-trigger agent'
    assistant: 'I will use just-fails-trigger to validate.'
    <commentary>
    Tests that agents without strong triggers get classified as "polish".
    </commentary>
    </example>

tools: []
---

# Just Fails Trigger Test Agent

This agent has examples, tools, and length, but NO strong trigger (MUST/ALWAYS/REQUIRED/PROACTIVELY/DO NOT).
Should be classified as "polish" not "good".
