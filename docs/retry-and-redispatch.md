# Retries and tool-call redispatch

Three independent retry layers exist, and they do not share configuration or
state. Reading one of them tells you nothing about the other two.

## 1. Provider transport retries

Owned by the LLM runtime (`modules/llm/runtime/flow.py`). `max_retries` and
`backoff_ms` come from `llmctl.retries` and can be overridden per profile. When
a provider response carries a `retry_after` fact,
`error_facts.retry_after_seconds` returns a relative duration in seconds, not an
absolute instant. The runtime's `_hint_not_before` adds that duration to
`time.monotonic()` to produce a monotonic not-before deadline, and the wait
becomes the later of that deadline and the exponential backoff, not a
replacement for the backoff. A hinted wait is only taken when the turn already
has a finite deadline that it fits; otherwise the route returns its recorded
rate-limit error unchanged so eligible fallback can proceed. The hinted wait
polls turn cancellation every 50 ms while it runs; the unhinted path sleeps for
the backoff step.

A per-entry map keyed by `(provider, model)` holds a monotonic not-before
instant together with the error response that set it, so a route already cooling
down is not attempted again inside that entry call. Each attempt reports a
posture of `initial` or `retry` in its extra facts.

The cooldown map lives for one entry call. A later turn on the same route starts
with no memory of the earlier rate limit, which is the subject of the parked
provider-cooldown work rather than an accident of this layer.

## 2. Structured-output retries

Owned by `modules/brain/retry.py`. When a structured result is rejected,
`is_retryable_structured_result` decides whether another attempt is allowed, and
`build_structured_retry_message` renders guidance from the target schema: field
types, enum values and required fields, with a decision-schema variant for the
entry response.

Each correction is a new logical call. In usage reporting it counts as another
call, not as a retry attempt of the previous one (`docs/runtime-surfaces.md`).

## 3. Tool-call redispatch

Owned by `modules/brain/loop/tools/duplicate_batch.py`. If the model repeats the
same call batch and every result in the previous attempt was a retryable read,
the harness may redispatch the batch a bounded number of times instead of
returning the duplicate as a failure. Tracking resets when a successful file
mutation changed the workspace, because the previous result may no longer
describe it.

One narrower recovery is admitted first. A single repeated `file.write` is
retried once when a complete `file.read` of the same path afterwards returned
content whose digest contradicts what that write recorded.
`consume_replacement_recovery` attaches the recorded `expected_content_sha256`,
and `expected_version` when present, to that retry so it cannot overwrite a file
that changed in between, and the entry is consumed on use. Reads are therefore
not the only repeated-call path.

A single failed tool call does not retry inside its iteration. The model decides
whether to issue it again; only a repeated batch, or the guarded write above, is
eligible for redispatch.

## Two vocabularies that are not interchangeable

| Field | Set by | Read by |
| --- | --- | --- |
| `retryable` on a tool error | the producer, as a judgement about the failure: `TIMEOUT` is retryable, `REMOTE_ERROR` and `NOT_FOUND` are not (`modules/tool/runtime/plugin.py`) | redispatch eligibility, the protocol envelope round-trip (`base/protocol.py`), and channel delivery retries |
| `idempotent` on a tool declaration | the tool author | the action policy, where it sets reversibility and the default confirmation requirement (`modules/policy/runtime/action_policy.py`); elsewhere it is carried through authoring and CLI metadata without a decision |

`retryable` answers "is this failure worth repeating". `idempotent` answers "is
repeating this call safe". Redispatch consults the first and not the second.

## What redispatch covers today

Eligibility is an explicit set of local read tools: `code.grep`, `file.find`,
`file.list_dir`, `file.read`, `file.read_range` and `file_read`. Tools outside
that set, including network fetches, cloud and Kubernetes reads, search providers
and git inspection, are not redispatch-eligible even when declared idempotent.

No accepted decision in the tree records why the set stops here; this section
reports the observed scope only. `idempotent` does not mean "no side effect":
it is a declaration read by the action policy, and redispatch does not consult
it. Widening the set would therefore be a policy decision, not a consistency
fix.

## Attempt counts multiply

The transport loop is `range(max_retries + 1)`: an initial attempt is always
made and `max_retries` adds retries on top, so `max_retries=0` still permits one
attempt. A structured-output correction is a new request, and that request is
itself subject to that loop.

Scope the arithmetic to one route and one logical call. With `S` structured
requests for the call and `max_retries + 1` transport attempts available per
request, the ceiling on that route is `S * (max_retries + 1)` attempts. It
assumes every request exhausts its retries, that the call stays on one route,
and that no further corrections are requested. It is not a bound for an entire
turn: fallback routes and other logical calls add to it, and since neither limit
is documented as a product anywhere, adding the two together underestimates the
calls a failing route can make.

## Where to look

- transport retries, backoff and route cooldown: `modules/llm/runtime/flow.py`
- `retry_after` parsing: `modules/llm/providers/transport/error_facts.py`
- structured retry eligibility and messages: `modules/brain/retry.py`
- retryable error facts: `modules/tool/runtime/plugin.py`
- redispatch eligibility and reset: `modules/brain/loop/tools/duplicate_batch.py`
- retry eligibility policy input: `modules/policy/runtime/action_policy.py`
