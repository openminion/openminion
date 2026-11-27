# Session timeline

Status: active
Last updated: 2026-11-27

`openminion sessions timeline <session-id>` prints a compact, payload-free
chronology for one session in the configured runtime store. Inside the
interactive CLI, `/timeline [<session-id>]` prints the same view for the active
session or an explicit one.

The command is read-only. It does not write events, messages, approval records,
or agent bindings.

## Usage

```bash
openminion sessions timeline <session-id>
openminion session timeline <session-id> --limit 100
openminion sessions timeline <session-id> --json
```

```text
/timeline
/timeline <session-id>
```

`sessions timeline` reads at most `--limit` recent messages and at most
`--limit` recent events from the same session, builds one ordered timeline, and
shows the latest `--limit` entries. `--limit` defaults to 500 and is clamped to
1 through 5000. A nonnumeric value is rejected by the argument parser.

The `session` alias applies to the entire `sessions` command group, not only
`timeline`: `openminion session list`, `openminion session show <id>`,
`openminion session delete <id>`, and every other `sessions` subcommand accept
either name.

## Where entries come from

The timeline reads the configured runtime store through its existing owners:

1. Messages from the session store's `messages` table. `inbound` renders as
   `user` and `outbound` renders as `assistant`; any other role keeps its role
   as the actor.
2. Tool rows from the outbound message metadata. `tool_results` is preferred
   and `tool_calls_cumulative` is the fallback; both may be a JSON string or a
   list. A malformed or non-list value keeps the message and adds no tool
   rows. A row is a recorded result fact, not an execution event: it reads the
   exact `call_id`, `tool_name`, and `ok` keys and at most a bounded
   `error_code` (the top-level key, or `data.error_code` for a failed result).
   Tool `data`, `content`, `error`, and every other metadata value are never
   summarized. The summary is `tool <tool_name> reported ok|failed`, plus
   `(<error_code>)` when a failed result records one. The row's event type is
   the timeline label `tool.result.reported`; no such event is written.
3. Approval prompts from `policy_confirmation_prompt` events. The summary is
   the event payload's `body`, and the entry is shown as a pending `policy`
   approval.
4. Agent handoffs from `agent.switched` events, rendered as
   `<from_agent_id> -> <to_agent_id>` with the reason when present.
5. Agent bindings from `agent.bound` events, rendered as
   `agent bound <agent_id> (profile <profile_version>)` and kept as an event,
   not a handoff.

Tool rows have the outbound message's observation time, not a per-call
execution time: the outbound message `created_at` is used, and every tool row
from one outbound transcript shares it. The gateway emits `run.waiting_tool`
once, after that response's tool activity, as an aggregate observation, so it
is not a per-call timestamp. A transcript that records
`tool_execution_count="0"`, as the existing invalid-tool-arguments metadata
does, is still shown as `reported failed`; the timeline does not claim that a
tool executed.

Six exact duplicate or progress event types are omitted: `run.queued` (the
turn-start transition already represented by the inbound message),
`run.running` and `run.responding` (run state progress between queued and the
response), `run.waiting_tool` (the single aggregate post-tool observation
already summarized by the tool rows), and `response.persisted` /
`response.delivered` (delivery bookkeeping already represented by the outbound
message row). No prefix or wildcard filter is used. Every other event type,
including `run.failed`, `run.cancelled`, `run.completed`, `turn.outcome`,
`thread.decision`, session-context, memory and knowledge-graph observations,
and any unknown or future type, stays an `event` row with the event type as
its summary. Event payloads are never included.

## Output

Text lines are `<timestamp>  <kind padded to 8>  <summary>`; message summaries
prefix the actor (`user: ...`). Entries are sorted by their parsed timestamp,
with a stable source order for ties and malformed timestamps. The header shows
the number of fetched entries and marks the output `(latest N)` when only the
most recent N entries are shown. It never reports a session total for records
the command did not fetch.

`--json` returns `ok`, `session_id`, `event_count`, `shown_count`, and entries
with `seq`, `timestamp`, `kind`, `actor`, `summary`, `status`, `event_type`, and
`event_id`. Raw message metadata and raw event payloads are never included; a
tool row carries only the tool name, its reported outcome, and an optional
bounded error code.

## Related commands

`openminion debug timeline --session <id>` is unchanged. It remains the raw
ordered durable-event view with event payloads; the compact session timeline is
a separate, safer summary and not a replacement for it.
