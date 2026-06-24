---
id: browser-playwright-login
name: Browser Login (Playwright)
version: 0.0.1
description: Create a persistent headed session for login-required sites.
tags: [browser, login]
tools: [browser, file]
scopes_required: ["tool.execute", "tool.browser.control", "tool.net.connect", "tool.fs.write"]
risk: high
when_to_use:
  - "Site requires login or MFA"
verification:
  - "Logged-in indicator exists"
  - "Domain and timestamp note was written"
inputs:
  - name: login_url
    type: string
outputs:
  - name: session_note
    type: string
---

# Skill Card
- **Goal:** Establish persistent browser session with minimal automation.
- **Triggers:** Login-required tasks.
- **Tools:** browser, file
- **Hard rules:** User confirms login; do not type secrets without explicit approval.

# Procedure
## Step 1 - Start session
Launch headed browser with persistent profile.

## Step 2 - Navigate
Open login URL.

## Step 3 - User confirmation
Ask user to confirm login completion.

## Step 4 - Verify session
Snapshot and record logged-in indicator.

## Step 5 - Store note
Write note with domain, indicator, and timestamp.

# Checks
- Logged-in indicator exists.
- Domain and time are recorded.

# Failure & Recovery
- Retry headed mode with slower pace, or fallback to ref-based provider.
