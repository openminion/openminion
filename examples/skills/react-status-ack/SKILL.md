---
id: react-status-ack
name: React Status Ack
version: 0.0.1
description: Add reaction acknowledgements to user messages for progress signals.
tags: [ux, status]
tools: [reactions]
scopes_required: ["tool.execute", "tool.reactions.write"]
risk: low
when_to_use:
  - "Long task started or finished"
outputs:
  - name: ack
    type: string
---

# Skill Card
- **Goal:** Provide visible progress signals.
- **Triggers:** Long-running operations.
- **Tools:** reactions
- **Hard rules:** React only to the latest user message in active channel.

# Procedure
## Step 1 - Start
React with start indicator.

## Step 2 - Success
React with success indicator.

## Step 3 - Failure
React with warning indicator and a short explanation.

# Checks
- Reaction corresponds to final outcome.

# Failure & Recovery
- If reaction write fails, send plain-text status message.
