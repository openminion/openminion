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
2. Tool entries from the outbound message metadata. `tool_results` is preferred
   and `tool_calls_cumulative` is the fallback; both may be a JSON string or a
   list. A malformed or non-list value keeps the message and adds no tool
   entries. Each entry uses the exact keys `call_id`, `content`, `data`,
   `error`, `error_code`, `ok`, `source`, `tool_name`, `turn_scope_id`, and
   `verified`; the summary is `tool <tool_name> <ok|failed>` plus a compact
   result from `data`, `content`, or `error`.
3. Approval prompts from `policy_confirmation_prompt` events. The summary is
   the event payload's `body`, and the entry is shown as a pending `policy`
   approval.
4. Agent handoffs from `agent.switched` events, rendered as
   `<from_agent_id> -> <to_agent_id>` with the reason when present.
5. Agent bindings from `agent.bound` events, rendered as
   `agent bound <agent_id> (profile <profile_version>)` and kept as an event,
   not a handoff.

Tool timestamps come from the `run.waiting_tool` event whose payload `run_id`
matches the outbound message metadata `run_id`. Without that match the tool
entry uses the outbound message timestamp and still sorts before it.

Known internal plumbing is skipped: `run.*` (including `run.waiting_tool`),
`response.*`, `thread.*`, `turn.outcome`, `session.context.*`, `memory.*`, and
`knowledge_graph.*`. Any other event type stays an honest `event` row with the
event type as its summary.

## Output

Text lines are `<timestamp>  <kind padded to 8>  <summary>`; message summaries
prefix the actor (`user: ...`). Entries are sorted by their parsed timestamp,
with a stable source order for ties and malformed timestamps. The header shows
the number of fetched entries and marks the output `(latest N)` when only the
most recent N entries are shown. It never reports a session total for records
the command did not fetch.

`--json` returns `ok`, `session_id`, `event_count`, `shown_count`, and entries
with `seq`, `timestamp`, `kind`, `actor`, `summary`, `status`, `event_type`, and
`event_id`. Raw message metadata and raw event payloads are never included.

## Related commands

`openminion debug timeline --session <id>` is unchanged. It remains the raw
ordered durable-event view with event payloads; the compact session timeline is
a separate, safer summary and not a replacement for it.
