# Description rewrite proposals -- browser-tester-pre

Repo: `/tmp/refresh-demo/browser-tester-pre`
Findings: `/tmp/refresh-demo/out/violations.json` (3 violating files, all `kind: agent`)

Rules applied: V7 shape (trigger -> USE WHEN -> DO NOT USE WHEN naming the alternative), V3 zero `<example>`/`<commentary>`, V5 <=600 chars for agent `meta.description`, V6 absolutes only when 100% true, V4 delete advocacy.

---

## agents/browser-operator.md

### Current (819 chars)

```
General-purpose browser automation using agent-browser CLI. Handles navigation,
form filling, data extraction, screenshots, and UX testing. Accepts natural
language instructions and translates them to browser actions.

Use PROACTIVELY when user needs to interact with a live website, fill forms,
test UI flows, click buttons, or extract data from JavaScript-rendered pages.

<example>
Context: User needs to interact with a live website
user: 'Go to github.com and find the trending repositories'
assistant: 'I'll delegate to browser-operator for live web navigation and data extraction.'
</example>

<example>
Context: User needs form filling or UI testing
user: 'Fill the contact form with name=John, email=john@test.com'
assistant: 'I'll use browser-operator to navigate to the form and fill the fields.'
</example>
```

### Proposed (526 chars)

```
Browser automation via the agent-browser CLI: navigation, clicking, form filling, extraction from JavaScript-rendered pages, screenshots, and UX/UI flow testing, driven from natural-language instructions. USE WHEN the task requires acting on a live page -- interacting, filling, testing a flow, or reading content a static fetch cannot see. DO NOT USE WHEN the task is multi-site information gathering and synthesis (use browser-researcher), or is purely screenshot/viewport capture as the deliverable (use visual-documenter).
```

### Fidelity table

| Routing fact | In the current text | In the proposed text | Where it went |
|---|---|---|---|
| Authoritative surface: the `agent-browser` CLI | "browser automation using agent-browser CLI" | "Browser automation via the agent-browser CLI" | Kept, sentence 1 |
| Capability: navigation | "Handles navigation" | "navigation" | Kept, sentence 1 |
| Capability: form filling | "form filling" / "fill forms" | "form filling" plus "filling" in USE WHEN | Kept |
| Capability: clicking elements | "click buttons" | "clicking" | Kept, sentence 1 |
| Capability: data extraction | "data extraction" | "extraction from JavaScript-rendered pages" | Kept, merged with the JS-rendering fact |
| Capability: screenshots | "screenshots" | "screenshots" | Kept, sentence 1 |
| Capability: UX / UI flow testing | "UX testing", "test UI flows" | "UX/UI flow testing" plus "testing a flow" | Kept, deduped into one phrase |
| Interface: accepts natural-language instructions | "Accepts natural language instructions and translates them to browser actions" | "driven from natural-language instructions" | Kept, compressed |
| Trigger: target is a live website | "interact with a live website" | "acting on a live page" | Kept, USE WHEN |
| Scope boundary: content needs JS rendering (static fetch insufficient) | "extract data from JavaScript-rendered pages" | "JavaScript-rendered pages" plus "reading content a static fetch cannot see" | Kept, stated as the deciding factor |
| Absolute "PROACTIVELY" | "Use PROACTIVELY when..." | absent | Dropped -- V6. Not true 100% of the time (two sibling agents own overlapping work), so it became a decision rule: USE WHEN the task requires acting on a live page. |
| Example 1 (github.com trending repos) | `<example>` block | absent | Dropped -- V3, and it carried no fact beyond "live navigation + data extraction", already in USE WHEN. |
| Example 2 (fill contact form) | `<example>` block | absent | Dropped -- V3, and it carried no fact beyond "form filling", already in sentence 1 and USE WHEN. |
| Boundary vs. browser-researcher | not present | "DO NOT USE WHEN the task is multi-site information gathering and synthesis (use browser-researcher)" | ADDED -- V7 requires a named alternative; the sibling agent lives in this same bundle and "General-purpose" otherwise swallows its traffic. |
| Boundary vs. visual-documenter | not present | "purely screenshot/viewport capture as the deliverable (use visual-documenter)" | ADDED -- same reason; this agent lists screenshots as a capability, so the split had to be stated. |

### Notes

- Inside the cap: 526 / 600.
- Nothing dropped as obsolete except the two `<example>` blocks and the `PROACTIVELY` absolute; both are rule-mandated removals and neither carried a fact not already stated elsewhere.
- Two boundaries were ADDED, not preserved: without them, "General-purpose browser automation" is the broadest description in the bundle and out-competes both siblings on every browser request. The added text is verifiable from the sibling agent files in this repo.

---

## agents/browser-researcher.md

### Current (877 chars)

```
Research-focused browser agent for finding and extracting information from websites.
Optimized for multi-page exploration, data extraction, and summarization.

Use PROACTIVELY when user needs to research topics across multiple websites,
compare competitors, look up documentation, or extract structured data from
the web. Preferred over web_fetch when sites require JavaScript rendering.

<example>
Context: User needs competitive research
user: 'Research the pricing of top 3 competitors in the CRM space'
assistant: 'I'll delegate to browser-researcher to visit each competitor site and extract pricing data.'
</example>

<example>
Context: User needs documentation lookup from JS-rendered sites
user: 'Find the API rate limits from Stripe's documentation'
assistant: 'I'll use browser-researcher to navigate Stripe's docs and extract the rate limit information.'
</example>
```

### Proposed (564 chars)

```
Research-focused browser agent using the agent-browser CLI: multi-page exploration, extraction, and summarization of information from websites, reported with source URLs and confidence. USE WHEN the task spans multiple sites or pages -- competitor comparison, documentation lookup, structured data extraction -- or when the source needs JavaScript rendering that web_fetch cannot see. DO NOT USE WHEN a static page's text suffices (use web_fetch), or when the task is acting on a page rather than reading it -- form filling, UI flow testing (use browser-operator).
```

### Fidelity table

| Routing fact | In the current text | In the proposed text | Where it went |
|---|---|---|---|
| Role: research-focused browser agent | "Research-focused browser agent for finding and extracting information from websites" | "Research-focused browser agent ... extraction ... of information from websites" | Kept, sentence 1 |
| Authoritative surface: the `agent-browser` CLI | implied only (body uses it; description never names it) | "using the agent-browser CLI" | Kept and made explicit -- the body's Core Commands are entirely `agent-browser` |
| Capability: multi-page exploration | "Optimized for multi-page exploration" | "multi-page exploration" | Kept, sentence 1 |
| Capability: data extraction | "data extraction" | "extraction" / "structured data extraction" | Kept |
| Capability: summarization / synthesis | "summarization" | "summarization" | Kept, sentence 1 |
| Output contract: findings carry source URLs and confidence | not in description (body's Output Format mandates Source / Data / Confidence) | "reported with source URLs and confidence" | ADDED -- routing-relevant deliverable shape; it is what distinguishes this agent from a raw fetch |
| Trigger: research across multiple websites | "research topics across multiple websites" | "the task spans multiple sites or pages" | Kept, USE WHEN |
| Trigger: competitor comparison | "compare competitors" | "competitor comparison" | Kept, USE WHEN |
| Trigger: documentation lookup | "look up documentation" | "documentation lookup" | Kept, USE WHEN |
| Trigger: extract structured data from the web | "extract structured data from the web" | "structured data extraction" | Kept, USE WHEN |
| Named alternative + tiebreak: preferred over `web_fetch` when JS rendering is required | "Preferred over web_fetch when sites require JavaScript rendering" | "when the source needs JavaScript rendering that web_fetch cannot see" plus "DO NOT USE WHEN a static page's text suffices (use web_fetch)" | Kept, stated in both directions so the router can also reject this agent |
| Absolute "PROACTIVELY" | "Use PROACTIVELY when..." | absent | Dropped -- V6. Not true 100% of the time; `web_fetch` legitimately wins on static pages. Replaced by the JS-rendering decision rule. |
| Example 1 (CRM competitor pricing) | `<example>` block | absent | Dropped -- V3; its only fact is "compare competitors", already in USE WHEN. |
| Example 2 (Stripe API rate limits from JS-rendered docs) | `<example>` block | absent | Dropped -- V3; its facts ("documentation lookup", "JS-rendered site") are both already in USE WHEN. |
| Boundary vs. browser-operator | not present | "acting on a page rather than reading it -- form filling, UI flow testing (use browser-operator)" | ADDED -- V7 named alternative; without it this agent and browser-operator overlap on "extract data from a live site". |

### Notes

- Inside the cap: 564 / 600.
- The `web_fetch` tiebreak is the most load-bearing fact in the original and is stated twice on purpose (once as a positive trigger, once as a rejection rule) -- that duplication is deliberate, not slack.
- Two facts were added: the output contract (source URLs + confidence, taken from the file's own Output Format section) and the browser-operator boundary.

---

## agents/visual-documenter.md

### Current (761 chars)

```
Screenshot and visual documentation agent. Creates visual records of websites,
UI states, and workflows. Perfect for documentation, QA evidence, and change tracking.

Use PROACTIVELY when user needs screenshots, visual documentation, responsive
testing across viewports, before/after comparisons, or QA evidence capture.

<example>
Context: User needs responsive screenshots
user: 'Screenshot our landing page at desktop, tablet, and mobile widths'
assistant: 'I'll delegate to visual-documenter to capture the page at multiple viewport sizes.'
</example>

<example>
Context: User needs workflow documentation
user: 'Document the checkout flow step by step'
assistant: 'I'll use visual-documenter to walk through the checkout and capture each step.'
</example>
```

### Proposed (554 chars)

```
Screenshot and visual-documentation agent using the agent-browser CLI: captures websites, UI states, and step-by-step workflows, including multi-viewport (desktop/tablet/mobile) sets and before/after pairs, with named files and a capture index. USE WHEN the images themselves are the deliverable -- QA evidence, visual change tracking, responsive checks, flow documentation. DO NOT USE WHEN screenshots are incidental to interacting with or testing a page (use browser-operator), or when the deliverable is extracted information (use browser-researcher).
```

### Fidelity table

| Routing fact | In the current text | In the proposed text | Where it went |
|---|---|---|---|
| Role: screenshot / visual documentation agent | "Screenshot and visual documentation agent" | "Screenshot and visual-documentation agent" | Kept, sentence 1 |
| Authoritative surface: the `agent-browser` CLI | implied only (body uses it; description never names it) | "using the agent-browser CLI" | Kept and made explicit -- the body's Core Commands are entirely `agent-browser`, including `set viewport` |
| Subject: websites and UI states | "Creates visual records of websites, UI states" | "captures websites, UI states" | Kept, sentence 1 |
| Subject: workflows / multi-step flows | "and workflows"; example 2 (checkout flow step by step) | "step-by-step workflows" | Kept, sentence 1 -- absorbs example 2 |
| Trigger: plain screenshot requests | "user needs screenshots" | "the images themselves are the deliverable" | Kept, USE WHEN |
| Trigger: documentation | "Perfect for documentation"; "visual documentation" | "flow documentation" | Kept, USE WHEN |
| Trigger: QA evidence | "QA evidence", "QA evidence capture" | "QA evidence" | Kept, USE WHEN |
| Trigger: change tracking | "change tracking" | "visual change tracking" | Kept, USE WHEN |
| Trigger: responsive testing across viewports | "responsive testing across viewports"; example 1 (desktop/tablet/mobile widths) | "multi-viewport (desktop/tablet/mobile) sets" plus "responsive checks" | Kept -- the three concrete viewport tiers were promoted out of the example into the capability sentence |
| Trigger: before/after comparisons | "before/after comparisons" | "before/after pairs" | Kept, sentence 1 |
| Output contract: named files plus an index of what was captured | not in description (body defines a naming convention and a Screenshots Captured table) | "with named files and a capture index" | ADDED -- the deliverable shape a router needs in order to know what it is getting |
| Advocacy: "Perfect for..." | "Perfect for documentation, QA evidence, and change tracking" | the three use cases survive; the word "Perfect" does not | Persuasive framing deleted -- V4. The three named use cases inside it were treated as routing facts and kept. |
| Absolute "PROACTIVELY" | "Use PROACTIVELY when..." | absent | Dropped -- V6. Not true 100% of the time; browser-operator also takes screenshots. Replaced by the deciding factor: are the images the deliverable? |
| Example 1 (landing page at desktop/tablet/mobile) | `<example>` block | absent | Dropped -- V3; its facts (responsive, three viewport tiers) were promoted into the capability sentence. |
| Example 2 (document the checkout flow step by step) | `<example>` block | absent | Dropped -- V3; its fact ("step-by-step flow") was promoted into "step-by-step workflows". |
| Boundary vs. browser-operator | not present | "screenshots are incidental to interacting with or testing a page (use browser-operator)" | ADDED -- V7 named alternative; browser-operator also lists screenshots, and its own body tells it not to screenshot by default, so the split needed stating. |
| Boundary vs. browser-researcher | not present | "when the deliverable is extracted information (use browser-researcher)" | ADDED -- V7 named alternative |

### Notes

- Inside the cap: 554 / 600.
- Nothing was lost from the two example blocks: each was reduced to the facts it carried (three viewport tiers; step-by-step flow capture) and those facts were promoted into the capability sentence rather than deleted with the block.
- "Perfect for..." is the clearest V4 case in this repo -- the adjective went, the three use cases behind it stayed.

---

## Cap summary

| File | Current | Proposed | Cap (600) |
|---|---|---|---|
| agents/browser-operator.md | 819 | 526 | inside |
| agents/browser-researcher.md | 877 | 564 | inside |
| agents/visual-documenter.md | 761 | 554 | inside |

No rewrite needed to exceed the cap: every routing fact in all three files survived inside 600 chars once the `<example>` blocks and advocacy framing were removed. Agent description cost drops from 2,457 chars to 1,644 (-33%).
