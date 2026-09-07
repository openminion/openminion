# GitHub tools

`openminion.tools.github` provides bounded GitHub REST tools for pull-request
inspection, branch-based changes, workflow dispatch, and release creation.
Read-only calls and mutating calls use the same provider boundary; policy and
tool scope decide which calls may run.

## Tools

Read-only:

- `github.list_prs`
- `github.fetch_pr`
- `github.fetch_diff`
- `github.fetch_comments`
- `github.fetch_checks`
- `github.list_workflow_runs`

Write-safe, policy-gated:

- `github.commit_files`
- `github.open_pr`
- `github.update_pr`
- `github.merge_pr`
- `github.dispatch_workflow`
- `github.create_release`
- `github.post_pr_review`
- `github.post_pr_comment`

There are no close-PR, delete-branch, force-push, or arbitrary GitHub API
tools. Merge and release actions require exact expected state and explicit
profile policy.

## Credentials and endpoint

Set a GitHub personal access token in `GITHUB_TOKEN`. A profile can select a
different environment variable with
`provider_config_overrides.github.token_env`.

```json
{
  "agents": {
    "github-agent": {
      "provider_config_overrides": {
        "github": {
          "token_env": "MY_GITHUB_TOKEN"
        }
      }
    }
  }
}
```

Optional process settings:

- `GITHUB_API_BASE_URL` defaults to `https://api.github.com`.
- `GITHUB_TIMEOUT_SECONDS` defaults to 30 seconds.

The credential boundary resolves the token at call time. Tool results and
errors must not include the token.

## Write policy

The default write policy is intentionally limited to the public smoke repo:

- repository: `openminion/test-repo-for-agent`
- branch prefix: `openminion-smoke/`
- path prefix: `.openminion-smoke/`
- base branch: `main`
- default-branch writes: disabled
- force push: disabled
- merge: disabled
- branch deletion: disabled
- workflow dispatch: no workflows allowed until configured

Override those values in `provider_config_overrides.github`. Enabling a tool
profile does not bypass the runtime policy checks.

```json
{
  "agents": {
    "github-agent": {
      "provider_config_overrides": {
        "github": {
          "allowed_repositories": ["owner/repository"],
          "allowed_branch_prefixes": ["agent/"],
          "allowed_path_prefixes": ["docs/"],
          "allowed_base_branches": ["main"],
          "allow_merge": false
        }
      }
    }
  }
}
```

Workflow dispatch additionally uses explicit workflow, ref, target, and input
allowlists. Release creation requires an existing exact tag and verifies its
target before creation.

## Pull-request review watches

The `github_pr_review` watch routine stores typed cursor state in the existing
scheduled-task payload. It fetches current pull-request facts, asks the model
for one typed review outcome, deduplicates by head SHA and finding hash, and
persists the rendered report before advancing the cursor.

Create scheduled work through the normal schedule or `task.watch` surfaces.
Inspect and control it with:

```bash
openminion schedule status
openminion schedule show <task-id>
openminion schedule pause <task-id>
openminion schedule resume <task-id>
openminion tasks list
openminion tasks cancel <task-id>
```

Task and schedule commands require exact IDs; the runtime does not guess from
prefixes or names.

## Registration

The package exports `REGISTRAR`, `register`, `register_provider`, and
`create_rest_provider`. Normal OpenMinion bootstrap discovers `REGISTRAR` and
registers the built-in REST provider. Direct embedding can register another
provider through `register_provider(...)`.

## Validation owners

- GitHub tool and policy tests: `tests/tools/github/`
- PR-review schema and dispatcher tests: `tests/tools/task/`
- Deterministic PR-review routine: `tests/routines/test_github_pr_review_e2e.py`
- Credential-gated live write coverage: `tests/e2e/test_live_github_write_actions.py`

Live tests mutate the configured repository and require explicit credentials
and quota authorization. They are not part of provider-free package checks.
