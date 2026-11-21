# Retries and tool-call redispatch

Three independent retry layers exist, and they do not share configuration or
state. Reading one of them tells you nothing about the other two.

## 1. Provider transport retries

Owned by the LLM runtime (`modules/llm/runtime/flow.py`). `max_retries` and
`backoff_ms` come from `llmctl.retries` and can be overridden per profile. When
a provider response carries a `retry_after` fact, `error_facts.retry_after_seconds`
turns it into the wait instead of the configured backoff.

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

A single failed tool call does not retry inside its iteration. The model decides
whether to issue it again; only a repeated batch is eligible for redispatch.

## Two vocabularies that are not interchangeable

| Field | Set by | Read by |
| --- | --- | --- |
| `retryable` on a tool error | the producer, as a judgement about the failure: `TIMEOUT` is retryable, `REMOTE_ERROR` and `NOT_FOUND` are not (`modules/tool/runtime/plugin.py`) | redispatch eligibility, the protocol envelope round-trip (`base/protocol.py`), and channel delivery retries |
| `idempotent` on a tool declaration | the tool author | the action policy, where it sets reversibility and the default confirmation requirement (`modules/policy/runtime/action_policy.py`); elsewhere it is carried through authoring and CLI metadata without a decision |

`retryable` answers "is this failure worth repeating". `idempotent` answers "is
repeating this call safe". Redispatch consults the first and not the second.

## What redispatch covers today

Eligibility is an explicit set of local read tools: `code.grep`, `file.find`,
`file.list_dir`, `file.read`, `file.read_range` and `file_read`. In the same
catalog, 117 tools are declared idempotent and 118 are read-only, so most
idempotent reads are not redispatch-eligible: network fetches, cloud and
Kubernetes reads, search providers and git inspection all sit outside the set.

That is a cost and quota boundary rather than an oversight. For those tools
`idempotent` means "no side effect", not "free to repeat", and retrying a paid
search call or a rate-limited fetch on every timeout would turn a read retry
into a spend decision. Widening the set is a policy question, not a
consistency fix.

## Attempt counts multiply

A structured-output correction is a new request, and that request can itself be
transport-retried. The worst case for one logical call is therefore the product
of the structured-retry allowance and the transport `max_retries`, not the
larger of the two. Neither limit is documented as a product anywhere, so budget
arithmetic that adds them together underestimates the calls a failing turn can
make.

## Where to look

- transport retries, backoff and route cooldown: `modules/llm/runtime/flow.py`
- `retry_after` parsing: `modules/llm/providers/transport/error_facts.py`
- structured retry eligibility and messages: `modules/brain/retry.py`
- retryable error facts: `modules/tool/runtime/plugin.py`
- redispatch eligibility and reset: `modules/brain/loop/tools/duplicate_batch.py`
- retry eligibility policy input: `modules/policy/runtime/action_policy.py`
