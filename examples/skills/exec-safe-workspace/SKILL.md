---
id: exec-safe-workspace
name: Exec (Workspace Safe)
version: 0.0.1
description: Run safe diagnostics commands inside workspace sandbox.
tags: [dev, diagnostics]
tools: [exec]
scopes_required: ["tool.execute", "tool.exec"]
risk: high
when_to_use:
  - "Run tests"
  - "Run lint"
  - "Show git status"
verification:
  - "Command executed in sandbox"
  - "Output includes next safe action"
inputs:
  - name: command
    type: string
outputs:
  - name: result
    type: string
---

# Skill Card
- **Goal:** Execute read-only or safe commands in sandbox.
- **Triggers:** Tests, lint, status checks, file listing.
- **Tools:** exec
- **Hard rules:** Deny rm/sudo/kill/chmod/chown/curl/wget by default.

# Procedure
## Step 1 - Validate command
Check command against denylist and safe patterns.

## Step 2 - Execute
Run via sandboxed exec with allowlist policy.

## Step 3 - Summarize
Report stdout/stderr, interpretation, and the next safe command.

# Checks
- Command executed in sandbox.
- Summary includes what happened and what to do next.

# Failure & Recovery
- If blocked, propose a safer equivalent command.
