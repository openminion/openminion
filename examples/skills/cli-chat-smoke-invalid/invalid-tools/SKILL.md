---
id: cli-chat-smoke-invalid-tools
name: Invalid Skill - Invalid Tool References
version: 0.0.1
description: Invalid skill fixture referencing non-existent tools for negative testing.
tags: [invalid, negative-test, bad-tools]
tools: [nonexistent_tool_123, another_fake_tool, invalid_tool_name]
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
- **Goal:** Test that invalid tool references trigger validation errors.
- **Triggers:** Negative test scenario.
- **Tools:** nonexistent_tool_123, another_fake_tool, invalid_tool_name

# Procedure

## Step 1 - Attempt to use non-existent tools
Try to invoke tools that don't exist in the tool registry.

## Step 2 - Expect failure
The skill should fail validation due to invalid tool references.

# Checks
- Tool names do not exist in registry.
- Validation should catch and report invalid tools.

# Failure & Recovery
- If tools don't exist, report clear error about invalid tool references.
