# Typed-Channel Recovery Pipeline

This package is the shared owner for deterministic typed-channel recovery.
It exists for cases where provider output is structurally close to the target
schema but needs bounded decoding before strict validation.

## Stages

1. `RAW_RECEIVE`
   Accept the provider payload as `dict | str | bytes`.
2. `STRUCTURAL_NORMALIZATION`
   Apply only closed-enum `RepairType` transformations.
3. `VALIDATION`
   Run strict Pydantic validation and convert failures into
   `TCRPValidationError`.
4. `RETRY_EMISSION`
   Build deterministic typed retry feedback from the validation error.
5. `BUDGET_ENFORCEMENT`
   Stop retrying when the per-channel typed budget is exhausted.

## Allowed repair types

- `STRINGIFIED_JSON`
- `TRAILING_COMMA`
- `FIELD_ALIAS`
- `TYPE_COERCION`
- `CODE_FENCE_STRIP`
- `SMART_QUOTE_NORMALIZE`
- `WHITESPACE_NORMALIZE`

The list is closed. Additions require a spec amendment, a new enum member,
registered implementation, tests, and Stage 8 validator coverage.

## Using the pipeline

```python
from openminion.modules.brain.runtime.recovery import (
    TCRPContext,
    TCRPRetryBudget,
    validate_payload,
)

result = validate_payload(
    payload,
    model=MyTypedModel,
    ctx=TCRPContext(channel_name="my.channel"),
    retry_budget=TCRPRetryBudget(channel_name="my.channel", max_retries=0),
)

if result.structured_payload is not None:
    typed = result.structured_payload
else:
    first = result.validation_errors[0]
    print(first.field_path, first.error_code.value)
```

## Aggregates

Use `aggregate_stage_events(...)` to compute typed operator metrics:

- `repair_rate`
- `repair_type_distribution`
- `validation_failure_rate`
- `retry_depth_p95`
- `fail_closed_rate`
- `repair_rate_delta`

These metrics are facts derived from typed stage events. The runtime does not
summarize them with prose or infer intent from them.

## Extension protocol

If a new typed channel wants to consume TCRP:

1. Build a `TCRPContext` with a stable `channel_name`.
2. Provide a strict Pydantic model.
3. Pass explicit `alias_map` / `type_coercions` only when they are
   structural and unit-testable.
4. Keep retry budgets typed and bounded.
5. Do not add prose rescue. If you think you need a new repair type,
   amend the spec first.
