---
id: cli-chat-smoke-debug
name: CLI Chat Smoke Test - Debugging
version: 0.0.1
description: Simple debugging skill for CLI chat smoke testing with systematic error triage.
tags: [debugging, triage, smoke-test, cli]
tools: [file, run_command]
scopes_required: ["tool.execute"]
risk: low
when_to_use:
  - "User asks to debug an error"
  - "User requests help triaging a failure"
  - "Smoke testing debugging skill domain"
inputs:
  - name: error_description
    type: string
    description: Description of the error or failure to debug
outputs:
  - name: debug_report
    type: artifact_ref
    description: Reference to the debugging checklist and findings
---

# Skill Card
- **Goal:** Systematically debug and triage errors for CLI chat smoke testing.
- **Triggers:** Error triage, debugging requests, smoke test scenarios.
- **Tools:** file, run_command
- **Hard rules:** Follow 5-step checklist; document each finding; end with recommendation.

# Procedure

## Step 1 - Capture error context
Record the error message, stack trace, and environment details.

## Step 2 - Identify failure category
Classify: syntax error, runtime error, logic error, or external dependency failure.

## Step 3 - Formulate hypotheses
List 2-3 possible root causes based on the failure category.

## Step 4 - Test hypotheses
For each hypothesis, describe a quick test to confirm or rule it out.

## Step 5 - Recommend fix
State the most likely cause and specific fix steps.

# Checks
- Follows 5-step debugging checklist
- Documents each finding clearly
- Ends with specific recommendation

# Failure & Recovery
- If error is unclear, ask for logs or reproduction steps.
- If stuck after 3 hypotheses, escalate with findings summary.
