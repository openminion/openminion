# Task-context extensions: separate owner/contract proposal

Status: proposed; maintainer scope approval required before upstream integration.
This branch preserves the complete experiment at `f04fddbd12d5e9119941c8f260813924852364ed`.
It is a reference implementation, not an approved follow-up ready to merge.
PR #625 is being narrowed to current-task clarification and execution handoff.
The eventual extension patch must be adapted to the accepted core, preserving
its child-goal, short-answer, budget and external-task-owner guarantees.

## Problem beyond the first fix

The first fix preserves the original task and current requirements through
clarifications, corrections, replacement and restart. It intentionally does not
promise recovery of an overwritten requirement, resumption of an archived task,
conversation-wide rules or independent subtask condition lifetimes.

Extensions would support explicit requests such as “restore only the original
format, keeping the latest filename”, “resume that completed task”, and “apply
this rule to all documents in this conversation”. A reference to old information
must not silently reactivate unrelated conditions or grant tool authorization.

## Owners and contracts

| Owner | Responsibility | Boundary |
| --- | --- | --- |
| Existing entry owner | Interpret update/replace/lifecycle/scope/reference intent; emit typed deltas | Model interpretation remains fallible; runtime checks provenance and structure, not semantic truth |
| Existing WorkingState owner | Bounded current execution projection, task identity/revision, compact archive index and committed journal head | One persisted state-save boundary; no claim of distributed compare-and-swap |
| Existing session journal owner | Durable accepted amendments, user provenance, before/after snapshots and predecessor links | Only events reachable from the saved committed head are accepted; orphan writes are not restored |
| Entry history-read owner | Read committed evidence with task/key/version selection, same-turn read/result budgets and bound references | Historical text is data; it cannot execute a tool or alter active conditions by itself |
| Existing execution/child owners | Preserve child objectives; project shared and explicitly owned conditions | Never pass a sibling's local conditions or close the parent merely because a child completed |
| Existing job/task owners | Decide acceptance of results by task identity/revision and retain reachable external handles | Context replacement is not external cancellation; no rollback of completed effects |
| Existing LLM/profile owner | Model defaults/output policy; actual request allowance accounting | No arbitrary fallback output cap, raised production budget or unaccounted lookup retries |

## Proposed state transitions

- Keep start/update/replace/cancel from the core. Add explicit pause/complete/resume
  only through the entry owner; inactive tasks cannot execute autonomously.
- Resume identifies an archived task and exact revision, and may atomically amend
  selected conditions using the current user's evidence. It restores requirements,
  not a live call stack or external job execution.
- Conditions default to the current task. Conversation scope needs a user quote
  supporting that scope, with the model interpreting its meaning. Subtask scope
  needs a registered owner. Do not infer global scope from every ordinary rule.
- Replacement removes old task/subtask conditions from execution. Explicit
  conversation conditions survive. Completed local conditions remain historical
  evidence and do not become another child's active instructions.
- A restore names exact selected values through references bound to the current
  input, foreground task/revision and owner. Unrelated current keys stay current.
  Stale, ambiguous, missing or cross-owner references require clarification.
- Pending jobs require an explicit owner policy before relaxing the core's
  amendment rejection. The experiment conservatively invalidates older bindings
  on any accepted revision. Detached handles must stay discoverable and must not
  advance a replacement task. This policy needs maintainer agreement.

## Persistence and failure behavior

The journal and current projection use existing session persistence. Writing an
event before saving WorkingState does not make a cross-store transaction. Readers
must verify the committed-head chain; a failed save cannot make an orphan event
look accepted. Invalid deltas, restore references, capacity overflow or persistence
errors must preserve the previously committed task and prevent external effects.

Existing core sessions load additive empty workspace/active lifecycle defaults.
Their current task identity, revision and conditions survive. The next accepted
amendment starts journaling; exact recorded revisions can be read, while unknown
pre-upgrade history is reported incomplete. No acceptance is manufactured from
raw transcript messages. Downgrading an extended workspace is not supported.

## Retrieval and cost

The experiment limits returned entries, archive/catalog size and lookup rounds.
Its committed-chain reader still scans the full stored history (`limit=None`).
Those limits do not bound scan work. Before claiming bounded retrieval, propose
an index/checkpoint strategy at the session owner, with completeness guarantees
for first/previous/revision lookups and measured large-history costs. A graph is
not a prerequisite; exact task/key/revision lookup should be evaluated first.

Required task conditions must fit the request without silent clipping. Optional
reference caches may shrink only while all durable evidence remains reachable.
Actual schemas, structured messages and lookup exchanges contribute to request
checks. Every returned lookup/retry response contributes to usage and call budgets.

## Separate acceptance gates

- Natural-model dialogues: ambiguous “previous”, two documents with independent
  format/filename edits, explicit conversation scope and task replacement.
- Many repeated corrections, original-value recovery after restart and history
  larger than the transcript window; restore one key while retaining newer keys.
- Parent/child/grandchild and sibling isolation, short-answer question meaning,
  completed-subtask conditions, paused/resumed tasks and stale async results.
- Failure injection: event append versus state-save failure, orphan chains,
  stale references/revisions, capacity limits and multi-worker contention limits.
- Affordable successful trimming, oversized required-input rejection, configured
  and unset output policy, exact lookup/retry usage and no unauthorized effects.
- Existing approval/denial/callback, freshness, delivery and PTY journeys.
- Large-history scan/index performance and a clean package install with the public
  runtime. Recorded-response/source controls and private-adapter observations
  must remain distinguished from live/installed/published acceptance.

The earlier experiment's test results are evidence about its specific source
snapshot. They do not approve this contract or validate a future rebased extension.
