---
id: cli-chat-smoke-plan
name: CLI Chat Smoke Test - Planning
version: 0.0.1
description: Simple planning skill for CLI chat smoke testing with checkpoint structure.
tags: [planning, smoke-test, cli]
tools: [file]
scopes_required: ["tool.execute"]
risk: low
when_to_use:
  - "User asks to plan a task"
  - "User requests an implementation plan"
  - "Smoke testing planning skill domain"
inputs:
  - name: goal
    type: string
    description: The goal or task to plan
outputs:
  - name: plan_md
    type: artifact_ref
    description: Reference to the generated plan markdown
---

# Skill Card
- **Goal:** Create a simple checkpointed plan for CLI chat smoke testing.
- **Triggers:** Planning requests, smoke test scenarios.
- **Tools:** file
- **Hard rules:** Produce 2-3 checkpoints; each with Action + Verify; end with Next action.

# Procedure

## Step 1 - Restate goal
Write the goal in one sentence.

## Step 2 - Define checkpoints
Create 2-3 checkpoints that divide the work into logical phases.

## Step 3 - List tasks per checkpoint
For each checkpoint, list small actionable tasks with Action and Verify statements.

## Step 4 - Output format
Produce Markdown with:
- Checkpoints as headers
- Tasks as bullet points
- Final "Next action" line

# Checks
- Contains 2-3 checkpoints
- Every task has Action and Verify
- Ends with "Next action: ..."

# Failure & Recovery
- If goal is unclear, state assumptions and ask one clarifying question.
