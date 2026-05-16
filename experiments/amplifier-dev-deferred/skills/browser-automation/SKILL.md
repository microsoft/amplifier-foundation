---
name: browser-automation
description: "Use when driving a browser, scraping a page, or running browser-based tests. Covers the browser-tester harness surface."
---

> *Loaded on demand. The full guidance below is not in your default context --
> you reached for this skill because the situation called for it. Treat the
> sections that follow as authoritative for the duration of this task.*


This bundle provides browser automation via [agent-browser](https://github.com/vercel-labs/agent-browser), a token-efficient CLI that uses accessibility-tree refs instead of DOM selectors.

## Prerequisites

agent-browser must be installed before any browser agent can work:

```bash
# Install CLI (requires Node.js 18+)
npm install -g agent-browser

# Download Chromium browser
agent-browser install

# Linux only: install system dependencies
agent-browser install --with-deps
```

**Verify installation:** `which agent-browser && agent-browser --version`

If "command not found": install with the commands above. If npm is not available, install Node.js 18+ first.

## Available Browser Agents

Delegate browser tasks to specialized agents:

| Agent | Use For | Example Triggers |
|-------|---------|-----------------|
| `browser-tester:browser-operator` | General automation: navigation, forms, data extraction, screenshots, UX testing | "Go to github.com", "Fill the contact form", "Test the login flow" |
| `browser-tester:browser-researcher` | Research: multi-page exploration, data synthesis, documentation lookup | "Research pricing of top 3 CRM competitors", "Find API rate limits from Stripe docs" |
| `browser-tester:visual-documenter` | Visual documentation: screenshots, QA evidence, change tracking, responsive testing | "Screenshot landing page at different viewports", "Document the checkout flow" |

## When to Use Browser Agents vs web_fetch

| Need | Use | Why |
|------|-----|-----|
| JavaScript rendering (SPAs, React, Vue) | Browser agent | Needs real browser engine |
| Form filling, clicking, navigation | Browser agent | Needs user interaction |
| Screenshots, visual verification | Browser agent | Needs rendering engine |
| Quick HTML/text retrieval from static pages | `web_fetch` | Faster, no browser overhead |
| API calls, JSON endpoints | `web_fetch` | Simpler, direct HTTP |

## Quick Command Reference

```bash
agent-browser open <url>              # Navigate to URL
agent-browser snapshot -ic            # Get interactive elements (compact)
agent-browser click @e1               # Click element by ref
agent-browser fill @e2 "text"         # Fill input by ref
agent-browser screenshot page.png     # Capture screenshot
agent-browser close                   # Clean up browser session
```

**Key concept:** After `snapshot -ic`, each interactive element gets a ref like `@e1`, `@e2`. Use these refs in subsequent commands. Always re-snapshot after page changes (navigation, clicks, form submissions).

## Troubleshooting Quick Reference

| Problem | Fix |
|---------|-----|
| "command not found: agent-browser" | `npm install -g agent-browser` |
| "Executable doesn't exist at chromium" | `agent-browser install` |
| "Browser closed unexpectedly" (Linux) | `agent-browser install --with-deps` |
| "Element not found: @e5" | Re-run `agent-browser snapshot -ic` for fresh refs |
| Page appears empty / no elements | Add `sleep 2` before snapshot (SPA hydration) |
| "Browser not running" | Run `agent-browser open <url>` first |
| Slow commands | Ensure daemon is running; use `-ic` flags for compact snapshots |

