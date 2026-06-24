---
id: perf-profile-basic
name: Perf Profile (Basic)
version: 0.0.1
description: Profile CPU and memory usage and summarize bottlenecks.
tags: [devops, performance]
tools: [exec, file]
scopes_required: ["tool.execute", "tool.exec"]
risk: high
when_to_use:
  - "App is slow and needs bottleneck analysis"
verification:
  - "Report includes evidence, hypothesis, and next steps"
  - "Command list is captured"
inputs:
  - name: target
    type: string
outputs:
  - name: report_md
    type: artifact_ref
---

# Skill Card
- **Goal:** Produce quick profiling findings with actionable next steps.
- **Triggers:** Performance triage requests.
- **Tools:** exec, file
- **Hard rules:** Prefer non-invasive profiling first; log commands used.

# Procedure
## Step 1 - Identify runtime
Detect runtime stack (python/node/go/etc).

## Step 2 - Gather lightweight evidence
Collect top/ps style stats before heavy profiling.

## Step 3 - Targeted profiling
Run focused profiler if needed.

## Step 4 - Write report
Summarize evidence, hypothesis, and next actions.

# Checks
- Report includes evidence, hypothesis, and next steps.

# Failure & Recovery
- If required tooling is missing, stop and provide install guidance.
