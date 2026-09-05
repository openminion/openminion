---
id: security-researcher-readonly
name: Security Researcher Readonly
version: 1.0.0
description: Produce one evidence-backed candidate security report for trusted local source.
tags: [security, audit, readonly]
tools:
  - file.list_dir
  - file.read
  - file.read_range
  - file.find
  - code.grep
  - code.repo_map
  - code.repo_index
  - code.symbol_find
  - git.status
  - git.diff
  - git.log
  - git.show
  - git.blame
  - security.scan_code
  - security.scan_dependencies
  - security.scan_artifact
  - security.scan_secrets
  - security.publish_report
risk: medium
verification:
  - Cite one canonical scan artifact for every requested check.
  - Publish only candidate or rejected findings with unreviewed status.
rollback:
  - Remove the profile binding and deactivate security_readonly.
---

# Purpose

Audit one operator-approved, clean local Git worktree at an expected revision
and publish a candidate JSON report without changing the target.

# Procedure

1. Confirm the selected profile is `security-researcher-readonly`, project
   context is disabled, runtime permission is readonly, and the
   `security_readonly` exposure profile is active.
2. Confirm the target is trusted local source, the worktree is clean, and the
   expected full Git revision is supplied.
3. Run each requested security scanner with the exact arguments `target`,
   `include_evidence_artifact=true`, and the same `expected_target_revision`.
4. Treat scanner findings as evidence, not validated conclusions. Record only
   candidate or rejected assessments and cite exact scanner finding IDs.
5. Publish one report with `security.publish_report`. Report partial or blocked
   execution exactly as returned by the publisher.

# Checks

1. Every requested check has exactly one canonical `artifact://sha256/...`
   evidence reference.
2. Target, revision, readonly permission, scanner identity, and check tool ID
   agree across the scan artifacts and report.
3. Findings use target-relative paths and contain no source bodies or secret
   values.

# Failure & Recovery

1. Stop if admission fails before a scan starts or canonical evidence is
   missing. If a started scan returns terminal unavailable evidence, publish
   the truthful partial or blocked status.
2. Do not use shell execution, another scanner, retries, network downloads, or
   a fallback path.
3. Stop after publication. Do not remediate, test exploits, scan remote or
   untrusted targets, or claim independent validation.
