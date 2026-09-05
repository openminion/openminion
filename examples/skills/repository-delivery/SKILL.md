---
id: repository-delivery
name: Repository Delivery
version: 0.0.1
description: Carry an approved repository change through review and delivery evidence.
tags: [coding, repository, delivery]
tools: []
risk: medium
when_to_use:
  - "An approved repository task needs implementation, review, and delivery"
  - "Work must survive correction, restart, and CI verification"
---

# Skill Card
- **Goal:** Complete one approved repository change with traceable evidence.
- **Commands:** Read exact commands and release details from the repository instructions and runbooks.
- **Approval:** Remote mutations remain manually approved through the active project policy.
- **Completion:** The project verifier and public TaskPlan own completion; Skill text never does.

# Procedure
## Step 1 - Bind the work
Confirm the approved objective, one execution repository, current revision, and
the repository instructions or runbook that owns validation and delivery.

## Step 2 - Assess and track
Inspect current state, record the smallest executable tracker item, and keep
unsupported assumptions or missing inputs explicit.

## Step 3 - Implement and review
Make the smallest complete change. When work is delegated, review the exact
artifact digest, reject stale or failed work, and accept only the corrected,
verified digest.

## Step 4 - Verify
Run the repository-owned checks. Treat failures as evidence for correction;
never turn prose, a Skill step, or an unverified diff into completion.

## Step 5 - Deliver
Commit the verified change. Perform push, pull-request, merge, workflow, tag,
or release actions only with their exact current approval and action identity.
Reconcile an uncertain remote result before any repeat.

## Step 6 - Close or resume
Record revisions, approvals, receipts, review evidence, verifier results, and
the next action. Close only when the public TaskPlan and verifier agree.

# Checks
- The objective, repository, revision, and active tracker item are explicit.
- Accepted delegated work matches the reviewed and verified digest.
- Repository-owned validation passed for the final revision.
- Every remote effect has an exact approval, receipt, or unresolved state.
- Completion comes from the project verifier and public TaskPlan.

# Stop Conditions
- Stop when the repository is ambiguous or outside the approved boundary.
- Stop when repository instructions or required validation commands are missing.
- Stop before a remote mutation when exact approval is missing.
- Stop after failed verification and return the failure facts for correction.
- Stop after an uncertain remote mutation until readback reconciles it.
- Stop when required evidence cannot be preserved truthfully.
