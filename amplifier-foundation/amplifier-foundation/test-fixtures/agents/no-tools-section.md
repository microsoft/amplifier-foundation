---
meta:
  name: no-tools-section
  description: |
    MUST be used for testing threshold boundaries. This agent has strong trigger,
    examples, and sufficient length, but NO tools section in frontmatter at all.
    
    <example>
    Context: Testing implicit tools
    user: 'Test no-tools-section agent'
    assistant: 'I will test.'
    </example>
---

# No Tools Section Test Agent

This agent is missing the tools: section entirely (implicit inheritance).
Should be classified as "needs_work" (WARNING level).
