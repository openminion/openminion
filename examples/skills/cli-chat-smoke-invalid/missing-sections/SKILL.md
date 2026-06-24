---
id: cli-chat-smoke-invalid-missing-sections
name: Invalid Skill - Missing Required Sections
version: 0.0.1
description: Invalid skill fixture missing required SKILL.md sections for negative testing.
tags: [invalid, negative-test, malformed]
tools: [file]
scopes_required: ["tool.execute"]
risk: low
when_to_use:
  - "Negative test fixture"
inputs:
  - name: goal
    type: string
outputs:
  - name: result
    type: string
---

# Skill Card
- **Goal:** Test that missing sections trigger validation errors.
- **Triggers:** Negative test scenario.
- **Tools:** file

# Procedure

## Step 1 - Do something
This step exists.

# Checks
- This section exists but others are missing.

# NOTE: This fixture intentionally lacks:
# - Complete Procedure section (only has 1 step)
# - Failure & Recovery section
