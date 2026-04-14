# Problem Solving Philosophy

This document captures core principles for systematic problem-solving that apply to all work: bug fixes, feature development, architecture decisions, and general development.

---

## Core Principles

### 1. **Investigation Before Action**

**Never start coding until you understand the complete picture.**

- Use specialized agents to gather information
- Trace the actual code paths involved
- Compare working vs broken scenarios
- Identify the EXACT divergence point

**Anti-pattern:** Jump to fixes based on assumptions  
**Correct pattern:** Investigate → understand → design → implement → test

---

### 2. **Evidence-Based Testing**

**Define specific, measurable proof requirements BEFORE testing.**

Each fix must have concrete evidence it works:
- "Command exits with code 0" ✓
- "No error message X appears in output" ✓
- "Output contains expected behavior" ✓
- "Specific keywords present in result" ✓

**Anti-pattern:** "I think it works"  
**Correct pattern:** "Here's the evidence it works: [specific outputs]"

---

### 3. **User Time is Sacred**

**The user's time is more valuable than tokens or agent time.**

Before presenting work to the user:
- Complete the investigation fully
- Test the fix thoroughly
- Gather all evidence
- Have a complete story, not partial findings

**Only bring design/philosophy decisions to the user, not missing research.**

---

## Anti-Patterns to Avoid

❌ **"I'll fix it and see if it works"** → Investigate first, understand, then fix  
❌ **"The tests probably pass"** → Actually run them with evidence requirements  
❌ **"I think this is done"** → Test proves it's done  
❌ **"Let me make one more change"** → Commit, test, then make next change  
❌ **"This might be related"** → Find the exact relationship  
❌ **"I'll ask the user to test it"** → You test it first, present working solution

---

## Remember

> "My time is cheaper than yours. I should do all the investigation, testing, and validation before bringing you a complete, proven solution. Only bring you design decisions, not missing research."

> "Commit locally before testing. Test until proven working. Present complete evidence, not hopes."

> "If I discover something three times and it's still not working, I don't understand the problem yet. Keep investigating."

---

## Full Methodology

For comprehensive orchestration workflows (6-phase process, multi-agent coordination patterns, case studies):

**Root session orchestrators:** See foundation:context/ISSUE_HANDLING.md  
**Investigation specialists:** Methodology included in agent-specific context
