---
id: browser-pinchtab-quick
name: Browser Quick Loop (PinchTab)
version: 0.0.1
description: Navigate, snapshot, click by ref, and resnapshot.
tags: [browser]
tools: [browser]
scopes_required: ["tool.execute", "tool.browser.control", "tool.net.connect"]
risk: high
when_to_use:
  - "Need deterministic click steps"
verification:
  - "Each action references a current snapshot ref"
  - "Notes include URL and title after final step"
inputs:
  - name: url
    type: string
outputs:
  - name: notes
    type: string
---

# Skill Card
- **Goal:** Navigate pages with low token usage using refs.
- **Triggers:** Deterministic browse-and-click loops.
- **Tools:** browser
- **Hard rules:** Always resnapshot after actions; never guess refs.

# Procedure
## Step 1 - Navigate
Open URL with PinchTab provider.

## Step 2 - Snapshot
Take compact interactive snapshot.

## Step 3 - Act by ref
Click using a known ref and resnapshot.

## Step 4 - Repeat
Continue snapshot-action loop as needed.

# Checks
- Every click references a known snapshot ref.
- Notes include URL and page title.

# Failure & Recovery
- If ref missing, resnapshot; if unstable, fallback to Playwright.
