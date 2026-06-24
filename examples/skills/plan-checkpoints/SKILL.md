---
id: plan-checkpoints
name: Plan with Checkpoints
version: 0.0.1
description: Turn a goal into small tasks with verification checkpoints.
tags: [planning, execution]
tools: [file]
scopes_required: ["tool.execute"]
risk: low
when_to_use:
  - "User asks for an implementation plan"
  - "Task is multi-step and needs tracking"
inputs:
  - name: goal
    type: string
outputs:
  - name: plan_md
    type: artifact_ref
---

# Skill Card
- **Goal:** Produce a checkpointed plan with verifiable tasks.
- **Triggers:** Implementation planning, decomposition, execution tracking.
- **Tools:** file
- **Hard rules:** Tasks are 2-10 minutes; each task has Action + Verify; end with Next action.

# Procedure
## Step 1 - Restate the goal
Write a one-sentence goal statement in user language.

## Step 2 - Build checkpoints
Define 2-5 checkpoints with clear milestone names.

## Step 3 - Define tasks
For each checkpoint, list small tasks (2-10 minutes) with Action + Verify.

## Step 4 - Add risks and fallback
List top risks and one fallback per risk.

## Step 5 - Output
Produce Markdown with checkpoints and a final Next action.

# Checks
- Contains 2-5 checkpoints.
- Every task has Action and Verify.
- Ends with Next action.

# Failure & Recovery
- If goal is vague, make assumptions explicit and ask one clarifying question at the end.
