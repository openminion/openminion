# GitHub Tool Family + PR Review Routine — Operator Runbook (V1/L3)

This is the operator runbook for the GitHub tool family and the
`github_pr_review` routine. Keep it short and operationally grounded; deeper
design lives in:

- background routine PR reviewer spec
- background routine PR reviewer tracker
- L3 write tracker/spec

The repo now has two bounded GitHub surfaces:

1. V1 read-only facts + `github_pr_review` routine.
2. L3 write-authorized smoke actions:
   - `github.commit_files`
   - `github.open_pr`
   - `github.post_pr_review`
   - `github.post_pr_comment`

L3 remains deliberately narrow. There are still no `github.merge_pr`,
`github.close_pr`, `github.delete_branch`, direct default-branch write, or
force-push tools.

---

## 1. Required environment

Set one PAT in the env that the runtime will read for github.* tool calls:

```
export GITHUB_TOKEN=<personal-access-token>
```

Operators may override the env name on a per-agent-profile basis by setting
`provider_config_overrides.github.token_env` in the agent config:

```json
{
  "agents": {
    "my-agent": {
      "provider_config_overrides": {
        "github": {
          "token_env": "MY_AGENT_GITHUB_TOKEN"
        }
      }
    }
  }
}
```

For the bounded L3 write smoke, the PAT must be scoped only to
`openminion/test-repo-for-agent` with:

- `contents: read/write`
- `pull_requests: read/write`
- `metadata: read`

Optional overrides:

- `GITHUB_API_BASE_URL` — defaults to `https://api.github.com`.
- `GITHUB_TIMEOUT_SECONDS` — defaults to `30`.

Centralized env helper: `openminion.tools.github.env`. Direct
`os.environ.get` reads are forbidden by the env-guard CI script.

`openminion.tools.github.rest.GithubRestProvider` owns both the read surface and
the bounded L3 write surface. The tool runtime still fails closed until a call
site registers it with `openminion.tools.github.register_provider(...)`.

## 2. L3 write policy sandbox

Default L3 write policy:

- allowed repository: `openminion/test-repo-for-agent`
- allowed branch prefix: `openminion-smoke/`
- allowed path prefix: `.openminion-smoke/`
- direct default-branch writes: denied
- force push: denied
- merge-like actions: denied
- delete-like actions: denied

The runtime enforces these rules in code before any network mutation. The model
may request a write tool call, but policy owns the allow/deny decision.

## 3. Starting a PR review routine

A routine is a `task.watch` with a typed `routine` payload. The model can
emit it directly from a chat session, or an operator can post a typed
`task.watch` argument blob. Minimum shape:

```json
{
  "description": "Review open PRs in octocat/hello-world every 30 minutes",
  "check_instruction": "Look at the supplied PR facts and emit a routine_outcome trailer.",
  "interval_minutes": 30,
  "alert_condition": "any PR has new commits since last review",
  "delivery": "announce",
  "routine": {
    "routine_kind": "github_pr_review",
    "routine_version": 1,
    "config": {
      "owner": "octocat",
      "repo": "hello-world",
      "state_filter": "open"
    }
  }
}
```

Notes:

- `interval_minutes` must be `>= 5` for `routine_kind = "github_pr_review"`
  (V1 routine-side validation; plain `task.watch` still accepts `>= 1`).
- The cursor is initialized empty by the runtime; do not pre-populate it.
- `routine` survives `TaskWatchArgs` validation because the field is
  declared explicitly. Other unknown top-level keys are still dropped by
  `extra="ignore"`.

## 4. Inspecting routine state

The routine cursor lives at
`cron_jobs.payload._openminion_watch.routine.cursor` in the cron store.
Useful fields:

| Field | Meaning |
| --- | --- |
| `last_check_iso` | ISO timestamp of the most recent tick (success or trailer-fail). |
| `last_review_per_pr["<n>"].head_sha` | Last reviewed head SHA per PR. Drives head-SHA dedupe. |
| `seen_pr_numbers` | All PRs the routine has observed. Drives `newly_opened_prs` / `closed_since_last_check`. |
| `delivered_findings_hashes["<n>"]` | Per-PR finding hashes already delivered. Drives finding dedupe. |
| `consecutive_failures` | Counter for trailer-parse / outcome-validation failures. V1 records only; threshold action is a follow-up. |

CLI-level inspection commands (e.g. `openminion routine list`,
`openminion routine show`) ship in the follow-up tracker
`routine-product-cli-surface`. For V1, inspect via direct cron-job SQLite
reads.

## 5. Reading routine output

Each successful tick that produces actionable findings emits:

1. A rendered markdown artifact body (produced by
   `openminion.tools.task.pr_review.renderer`) held on the
   `CronRunRoutineSink` and surfaced via a synthetic
   `artifact://routine/<routine_id>/run-<n>` id on the cron run
   summary (V1 placeholder per spec D6.2). Operator-readable
   artifact-store persistence (canonical file path, retention,
   downloads, indexed metadata) is deferred to the follow-up
   tracker `routine-artifact-store-delivery`.
2. A single `announce` summary line delivered to the originating
   session, of shape:
   `"PR review run for <repo>: reviewed <N> PR(s), <M> finding(s)."`

Idempotent ticks (no head_sha changes) write nothing.

To inspect the rendered markdown body for a given run in V1, read
the cron run row's `summary` field for the synthetic artifact id
and either re-run with verbose logging or query the in-process
sink during a test harness run. The `routine-artifact-store-delivery`
follow-up will land an operator-readable file path.

## 6. L3 live smoke flow

The bounded smoke path is:

1. verify `GET /repos/openminion/test-repo-for-agent` returns `200`
2. `github.commit_files` writes one `.openminion-smoke/<run_id>.md` file to
   `openminion-smoke/<run_id>`
3. `github.open_pr` opens a PR from that branch to the repo default branch
4. `github.post_pr_review` posts a `COMMENT` review
5. `github.post_pr_comment` posts a harmless PR-thread issue comment

The branch/PR are intentionally left behind as live evidence. L3 does not
merge, close, or delete anything.

## 7. Stopping a routine

Use the regular task lifecycle commands:

- List: `openminion task list` (or `task.list` tool).
- Cancel: `openminion task cancel <task_id>` (or `task.cancel` tool).

`task.cancel` requires the **exact** `task_id` (post-TCEE-07 anti-LLM
contract). Look it up with `task.list` first; runtime no longer resolves
prefixes or name-like tokens.

## 8. Anti-LLM rules

The runtime owns:

1. Calling `github.list_prs` and assembling typed PR facts.
2. Head-SHA dedupe (PR head_sha unchanged → not in actionable list).
3. Validating the model's `<routine_outcome>` trailer JSON against
   `ReviewOutcomePayloadV1`.
4. Finding-hash dedupe before artifact rendering.
5. Cursor persistence via `replace_cron_job_payload`.

The model owns:

1. Reading the typed PR facts.
2. Emitting one `<routine_outcome>...</routine_outcome>` trailer with the
   typed review outcome.

The model is **never** asked "have you reviewed this before" — that's
runtime-owned dedupe. Free prose outside the trailer is recorded but never
actionable. Missing trailer / malformed JSON / failed schema validation
each map to a distinct deterministic error code:
`trailer_missing` / `trailer_malformed_json` / `outcome_validation_failed`.

## 9. Failure handling

Each trailer-fail or outcome-validation-fail bumps `consecutive_failures`.
A successful tick resets the counter to `0`. V1 records only — there is no
automatic clamp/pause. For threshold-based clamp/pause and rate-limit-aware
backoff, see follow-up tracker `routine-rate-limit-and-backoff`.

## 10. Limits and trade-offs

- **One PAT per process.** Multi-account routing and per-routine secret
  rotation belong to `routine-multi-repo-multi-account-auth`.
- **Polling, not webhooks.** Webhook-driven triggering belongs to
  `routine-webhook-trigger`.
- **Bounded write only.** Only smoke-branch commit/open-PR/comment flows are in
  scope. Merge/close/delete/default-branch-write/force-push remain out of scope.
- **No named-routine identity.** Routines run as the calling agent's
  identity. See `routine-named-background-agent-identity` for the L4
  follow-up.

## 11. Validation suites

- Read-only github tool surface: `openminion/tests/tools/github/`.
- Bounded write github tool surface:
  `openminion/tests/tools/github/test_write_policy.py`,
  `openminion/tests/tools/github/test_rest_provider.py`,
  `openminion/tests/e2e/test_live_github_write_actions.py`.
- Routine schemas + outcome validation: `openminion/tests/tools/task/test_pr_review_schemas.py`.
- Renderer snapshot: `openminion/tests/tools/task/test_pr_review_renderer.py`.
- Trailer parsing + dispatcher: `openminion/tests/tools/task/test_routine_dispatcher.py`.
- Four-tick deterministic E2E: `openminion/tests/routines/test_github_pr_review_e2e.py`.

Live test (gated on credentials): see BRPR-09 in the tracker.
