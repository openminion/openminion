# Current task context

Entry carries a `task_update` alongside its existing action. The current task is
stored in `WorkingState`, so the original objective and still-applicable
conditions survive clarification windows and restarts. Execution receives that
resolved request together with the latest verbatim user input.

For example, after “Process input.csv for BATCH-ALPHA, excluding archived rows”
and a filename clarification answered with “Use result.json”, execution receives
the batch, exclusion rule and filename together. The same contract applies to
general execution, coding, research, delegation and orchestration.

## Updates

An update contains `operation`, `expected_revision` and `user_quote` from the
latest user input. The model resolves the objective and selects stable keys for
independently changeable conditions. Runtime validates structure, revision and
quote provenance; it does not infer the user's intent from keywords.

- `start` creates a task when none exists and requires an objective.
- `update` preserves omitted keys, overwrites corrected keys and removes only
  explicitly listed keys. A condition may retain the exact pending clarification
  question to give a short answer such as “yes” its meaning.
- `replace` starts a different task and discards the previous task's conditions
  and local workflow state. Permissions and external task records are unchanged.
- `cancel` clears the local task and responds without executing tools.

A first non-clarification response may omit the update for compatibility. Its
entire original input is retained as a bounded `verbatim` objective. On the next
update the model must resolve that input and preserve its applicable independent
conditions. Only that adoption permits condition quotes from the original input;
normal amendments require quotes from the current user.

Plain text answers may finish a turn without amending the stored task. They mark
its context as requiring an update before autonomous execution or tool-parity
recovery. Invalid or missing required amendments preserve the previous snapshot
and stop execution. Entry-only `task_update` fields are stripped before execution
tool replay.

## Execution and budgets

The existing execution context owner builds the act request. Canonical tool replay
supplies historical assistant/tool batches before the latest input; duplicate
context excerpts are suppressed. Children keep their own goals and inherit the
current parent's task conditions as background. This change adds no child
lifecycle or asynchronous result-binding registry.

While an external job is pending, context amendments are rejected. Replacement
and cancellation of task-backed execution must use its existing owner. The local
context protocol cannot cancel an external job or detach it and accept its old
result as a new task's output.

The task has at most 128 conditions and a 12000-character content limit. Required
task messages and selected schemas reserve space before optional-context trimming.
The actual serialized request is checked again before dispatch. Explicit output
limits are bounded by the estimated remaining allowance; unset limits remain with
the existing profile/provider policy. Each returned entry retry is accounted for.
Estimation includes structured messages and schemas but remains a heuristic,
not a provider-tokenizer guarantee. Oversized required input is rejected without
clipping active conditions. An omitted oversized stored task may only be explicitly
replaced or cancelled, subject to the same external-owner restrictions.

## Scope and limits

This contract manages one current task. It does not add an archive, journal search,
selective restoration, pause/resume, conversation-wide condition ownership or
subtask-specific condition lifetimes. Superseded values leave the active snapshot;
ordinary conversation history remains owned by the existing session mechanism.
If a reference to an older value cannot be resolved reliably from available
context, entry should ask the user to restate it.

Existing sessions without these additive fields load with an empty task context.
This is not a downgrade migration for sessions written by the separate experimental
archive/lifecycle implementation.

Quote validation establishes provenance, not semantic accuracy. The model can
still choose the wrong objective or condition key. Scripted source tests verify
the protocol and actual request construction; they do not establish live-model
or published-package acceptance. This change does not grant permissions, change
activation persistence, or provide multi-worker compare-and-swap.
