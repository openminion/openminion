# Current task context

Entry can carry a `task_update` alongside its selected action. This maintains
one bounded current-task projection in `WorkingState`, separate from temporary
clarification and next-turn hints. It does not grant tools or authorize an action.

The entry update is required for a clarification and whenever an existing
resolved task receives new user input. First-turn non-clarification actions remain
compatible with responses that omit it. Their entire initial input is stored as
a bounded `verbatim` objective, including when entry responds directly. On the
next update the model must resolve that objective and preserve its applicable
independent conditions. Only this adoption allows condition quotes from the
stored verbatim input, with their original source hash; other updates require
current-user quotes. An oversized initial request is rejected without clipping.

An update has an operation (`start`, `update`, `replace`, `cancel`, `pause`, `complete`, `resume`), the
`expected_revision`, and a `user_quote` taken exactly from the current input.
An optional `objective` resolves the task purpose using the previous task and
new input. Independent conditions use stable keys and exact source quotes.
Omitted condition keys survive `update`; setting an existing key corrects it;
`remove_conditions` withdraws named keys. `replace` discards the previous task's
task/subtask conditions (explicit conversation conditions survive) and detaches its local workflow state. External task records and tool
authorization are not modified by that detachment. Context cancellation is rejected
while an external task or pending job is attached; cancelling that execution
requires its existing task-control owner.

For example, given the user request:

> Process input.csv for BATCH-ALPHA, excluding archived rows. Write JSON.

The entry action can include:

```json
{
  "task_update": {
    "operation": "start",
    "expected_revision": 0,
    "user_quote": "Process input.csv for BATCH-ALPHA, excluding archived rows. Write JSON.",
    "objective": "Process input.csv",
    "set_conditions": [
      {"key": "batch", "quote": "BATCH-ALPHA"},
      {"key": "exclusion", "quote": "excluding archived rows"},
      {"key": "format", "quote": "JSON"}
    ]
  }
}
```

After `Use BATCH-BETA instead. Save result.json.`, the next action can update
`batch` and add `filename`, without restating the exclusion or format. If a short
answer such as `yes` depends on a question, its condition can include
`question_quote` matching the exact pending clarification question. This context
is replaced or removed with that condition.

Accepted updates and the complete current user message are stored in the existing
session event log, alongside before/after task snapshots. A committed-head chain
in WorkingState excludes orphan events if a state save fails after the event write.
This is recoverable journaling, not a transaction spanning multiple sessions or
workers. Normal session state persistence remains the commit boundary.

The model receives current applicable conditions and a small reference cache of
prior values (at most first/latest evidence per changed key within 2,000 content
characters). No evidence is deleted from the durable journal when this cache is
trimmed. `history_available` identifies a task with journaled history;
`history_revisions` (up to eight exact revision numbers) attaches older snapshots
as reference-only `history_context`. Select clarify/respond for this lookup before
choosing execution based on previously unavailable evidence. The entry model can now call `task.history.search` to read committed task
conditions and continue its decision in the same user turn. Search is structured
and lexical; no graph or semantic search dependency is added. A legacy store
without both event write/read ports retains the original full bounded history;
it refuses overflow instead of silently dropping evidence.

Conditions have an explicit owner. Omitted scope means `task` for a new key and
preserves ownership for an existing key. `conversation` requires `scope_quote`
from the current user explicitly extending the rule to the conversation; changing
that scope or withdrawing such a condition requires current-user scope evidence.
The backend checks the exact quote, while the model must interpret its meaning.
`subtask` requires a registered `owner_id`. Unknown owners are rejected. Children
receive shared conditions plus only their own open local conditions. Completed
or cancelled subtask conditions remain stored but are omitted from execution;
other siblings do not inherit them or their history snapshots.

Tasks have stable random identities and `active`, `paused`, `completed`, or
`cancelled` lifecycle. Pause/complete/cancel must select respond, without tool
execution. Actual execution closure also marks the task completed; an entry
acknowledgement while awaiting an answer does not. Inactive tasks cannot run on
an autonomous tick. Replacement archives the old task and detaches local
execution state. `resume` requires both the target task ID and its revision,
restores its conditions with the current conversation rules, applies optional
current-user amendments atomically to the restored task, and starts a new
revision with a fresh local execution plan. It does not restore a live call stack
or restart external jobs. Entry sees a compact archive index, not all archived
condition bodies. The archive is bounded to 64 tasks and the workspace to 512,000
serialized characters; exceeding either limit explicitly rejects the transition.

Async jobs and child results carry task identity/revision bindings. Results for
an obsolete owner cannot advance the current plan. Detached async handles and
their bindings remain available in the workspace (up to 128); external jobs are
not cancelled by context replacement. Any accepted user revision conservatively
invalidates an older binding, even if the amendment was small. This is an owner
check, not a distributed compare-and-swap or rollback of tool side effects.

The runtime validates structure, revision, current-user quotes, condition keys,
and capacity before committing the update. The objective is a model-authored
interpretation: a valid quote does not prove that the model chose the right
operation, interpreted intent correctly, or retained every requested condition.
Semantic inference requires separate model evaluation.

The projection permits at most 128 conditions and 12,000 characters of objective,
condition keys/quotes, answer-question context and condition history. These are state capacity
limits, not a guarantee of fit in every provider context window. The existing
context estimator and remaining turn budget still apply. A failed update leaves
the previous projection intact and blocks autonomous continuation until a valid
update. If a full snapshot cannot fit, an explicitly marked recovery request can
only replace or cancel it; it cannot update the omitted contents. If even recovery
cannot fit, the runtime asks for a larger budget.

Task updates are consumed before canonical tool replay, so entry-only arguments
never become runtime-tool arguments. The execution pack requests
`canonical_tool_replay=true` to omit duplicate tool-event excerpts; the existing
canonical replay owner preserves tool roles, pairing, ordering and caps.

General execution, coding planning/resume, research dispatch, delegation input
and orchestration synthesis use the same resolved task text. Child states detach
the parent's current-task projection and retain their explicitly assigned goals.
Ordinary conversation history can still contain superseded wording; it is not
the current-task source.
The latest consumed user input also accompanies the snapshot verbatim at the
execution boundary, so a detail omitted by the model's delta is still available
on that turn. This does not recover omissions from older turns automatically.

No additional summarization call is added. Existing entry retry behavior remains.
These source changes and deterministic tests do not establish live-model quality,
parallel same-session conflict resolution, or installed-release verification.

The wire representation omits generated schema annotations and JSON whitespace.
Historical evidence omits null question fields. When exact repeated evidence is
smaller as a pool, `evidence` holds unique quote/question pairs and `sequence`
holds their zero-based indexes in chronological order. This compacts only the evidence currently projected into the request. Complete
accepted transitions remain in the session journal; the legacy fallback still
counts every stored historical event against its state capacity.

Generated task schema definitions are merged without overwriting definitions
owned by the runtime tool. Only colliding generated names and their references
are renamed; existing runtime references and validation rules stay intact.

The task-aware entry and shared adaptive runtime check every outgoing request
against the remaining turn token budget, including the selected tool schemas,
structured message parts and tool arguments/results. This check runs after retry
messages and tool selection, and is repeated for later calls. Input estimates
include a serialized-size fallback even when an adapter estimator fails or
underestimates. This is still a heuristic, not an exact provider tokenizer,
image-token calculation or a model-context-window guarantee.

A fitting task request retains an explicitly requested output cap, bounded by
the estimated remaining allowance. An unset output cap remains unset, leaving
defaults and configured limits to the existing LLM/profile budget owner. If input alone
does not fit, no provider request is sent and the owner returns waiting_user,
keeping the current task. The guard also applies to the first entry request, before a task snapshot
exists. Runtime users that do not attach a budget state retain their previous
request options. Entry debits every returned response, including
clarification reconsiderations, before attempting another request.


## Automatic journal lookup and selective restoration

`task.history.search` is an internal read tool available when a journal head is
present. It takes an optional task ID from the current workspace, an exact
condition key, an optional lexical query, a selection (`first`, `previous`, `all`,
`revision`), and a result limit of at most eight. Without a key it returns a
bounded catalog; ambiguous catalogs do not issue restoration references. The
model first uses conversational focus and the catalog's `recent_conditions`
(revision, presence, bounded quote preview) to identify the latest relevant
parameter. Unqualified previous/as-before normally means that parameter's
previous distinct value; original means its first value. It asks only when
there is no unique relevant referent. Equal revision numbers remain a tie,
and a display sort order does not make one simultaneous change more recent.
Queries never search another session or arbitrary external task identifiers.

`previous` means the preceding distinct quote/question value, so JSON→CSV→JSON
returns CSV. If a key is currently removed, its most recent stored value is
eligible. `first` is unavailable if the journal does not establish the task's
original start; restoring/resuming a task is not treated as its original start.
Missing exact revisions do not fall back to a neighboring revision.

The entry runtime permits at most three serial history reads per decision. Reads
must be selected alone, without a task_update or another tool call. Their results
are appended as paired assistant/tool messages, and entry continues automatically.
Every returned lookup response is debited immediately; each subsequent request
passes the existing remaining-token/output guard and model-call limit. No lookup
means no extra model call. A limit, changed task owner, or insufficient budget
stops before external execution. A read failure returns unavailable evidence,
not an invented value.

After reading, entry can include `restore_conditions: [{"key": "format",
"reference": "<returned hash>"}]` in an update or resume. The reference must exist
in this runtime's ephemeral read set, bound to the current input digest and
foreground task identity/revision. Its historical task and key must match the
restored task and requested key. Values and original source hashes come from the
trusted read receipt, not model-supplied historical quotes. Unrelated current
conditions survive. `restored_from` records each restored key's source revision;
the journal also records the current user message authorizing the change.
Overwriting/removing a key clears its marker. Duplicate keys and conflicts with
set/remove lists are rejected atomically. Historical evidence never supplies new
permissions. Restoring a removed conversation-wide rule needs current scope
citation; completed/cancelled subtask rules cannot be reactivated this way.

Canonical historical tool batches now precede the latest user input. The task
snapshot is inserted between complete batches and the current input, rather than
inside a tool call/result pair. The current input remains verbatim with its
content parts when available. This preserves transcript validity and avoids
presenting old file effects as if they followed the newest user restriction.

Limitations: key/intent interpretation remains model work. Lookup scans the full
committed journal chain; stored-history reads and scan cost are not bounded.
It is not indexed semantic search. Lookup rounds are capped at three per entry
decision, catalogs at 32 keys and value lists at eight entries, with explicit
result truncation. Raw conversation messages without an
accepted task-condition projection are not automatically recovered as structured
conditions. There is no distributed multi-worker CAS, and prior external side
effects cannot be rolled back by restoring conditions.
