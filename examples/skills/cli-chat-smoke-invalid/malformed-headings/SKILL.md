---
id cli-chat-smoke-invalid-malformed-headings
name Invalid Skill - Malformed YAML and Headings
version 0.0.1
description Invalid skill fixture with malformed frontmatter and headings for negative testing
tags [invalid negative-test malformed]
tools [invalid_tool_reference]
scopes_required ["tool.execute"]
risk low
when_to_use
  - "Negative test fixture"
inputs
  - name goal
    type string
outputs
  - name result
    type string
---

Skill Card
- Goal Test that malformed YAML triggers validation errors
- Triggers Negative test scenario
- Tools invalid_tool_reference

Procedure

Step 1 Do something
This step exists without proper markdown heading syntax

Checks
- YAML frontmatter is malformed (missing colons)
- Headings lack proper markdown syntax
