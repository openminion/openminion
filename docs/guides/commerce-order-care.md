# Commerce Order Care

Status: local foundation; production unavailable
Last updated: 2026-10-06

OpenMinion has a config-disabled commerce foundation for inspecting products,
preparing and placing exact orders, tracking accepted orders, and preparing and
applying order-care actions. The current repository has no production commerce
provider, so `runtime.tools.commerce.enabled=true` is rejected. Local fixture
evidence does not authorize a merchant account, payment, refund, or shipment
claim.

## Exposure states

Commerce uses three ordered flags. Each later flag requires the earlier ones:

| State | Exposed tools | Meaning |
| --- | --- | --- |
| omitted or `enabled: false` | none | Default and only public configuration currently accepted. |
| `enabled: true`, writes disabled | inspect, prepare order | Reserved for a selected provider; unavailable today. |
| writes enabled, actions disabled | inspect, prepare order, place order | Reserved for sandbox and production conformance; unavailable today. |
| order actions enabled | all five tools, including prepare/apply action | Reserved for a provider that proves the complete contract; unavailable today. |

The deterministic test fixture injects these states directly for acceptance
tests. `provider: fixture` is not a supported user configuration.

## User-visible lifecycle

- **No order:** research and inspection do not imply checkout, payment, or an
  accepted merchant order.
- **Prepared:** OpenMinion shows exact items, totals, merchant, destination,
  payment label, expiry, and consequence. Preparation requires one exact
  approval and still does not place the order.
- **Accepted:** placement requires a separate exact approval. The returned
  local order reference joins the durable record to the provider result.
- **Outcome unknown:** OpenMinion preserves the attempt for recovery and does
  not submit it again blindly.
- **Tracking:** read-only order inspection can record material lifecycle
  changes and suppress unchanged notifications.
- **Pending action:** a cancellation, return, or refund request remains active
  until the provider reports a terminal result. Another action is blocked.
- **Handoff:** authentication, unsupported operations, ambiguous refund
  destinations, and label or QR retrieval stop with a typed reason and a
  credential-free trusted URL when one is available.

## Approval and privacy boundaries

Preparation, placement, and action application use exact, one-time approvals.
An approval is bound to the local subject and session, the typed request,
current revision, total or refund facts, destination label and digest, expiry,
and preparation digest. Denial does not call the provider. Changed material
facts require a new preparation and approval.

The order store keeps local references, lifecycle facts, safe labels and
digests, idempotency keys, and recovery state. Provider credentials, payment
tokens, bearer links, and raw personal records are excluded from tool results,
notifications, and commerce audit events.

## Recovery and rollback

OpenMinion reserves a durable attempt before a mutation and reuses its stable
idempotency identity for recovery. `pending` and `outcome_unknown` remain
active; terminal completion, rejection, or failure releases the action slot.
Disabling commerce removes its tools after restart without deleting local
records. Merchant-side reversal depends on the selected provider and is not
claimed by this local foundation.

## Monitoring controls

Order monitoring reuses `task.watch` with a typed `commerce_order` routine. Its
config binds the local subject, local order reference, expiry, terminal policy,
and failure limit; its cursor stores the last material revision plus open
shipment and action IDs. The daemon must be running for scheduled checks.
Unchanged checks stay quiet, material revisions request one delivery, and the
routine stops on expiry, subject denial, repeated failure, or its configured
terminal condition.

In Focus, use `/tasks` to list watches, `/tasks <task-id>` to inspect one, and
`/tasks pause <task-id>`, `/tasks resume <task-id>`, or
`/tasks cancel <task-id>` to control it. These controls exist in the shared task
owner, but commerce monitoring cannot be created from public configuration
until a provider is selected.

## Evidence levels

| Level | Current result |
| --- | --- |
| Models, storage, policy, runtime, and CLI rendering | Deterministic tests pass. |
| Complete local scenario matrix | Fixture-ledger runner and negative evidence validator pass. |
| Real Focus with MiniMax and fixture commerce | Runner exists; a valid `MINIMAX_API_KEY` is required for execution. |
| Selected merchant sandbox | Blocked until a target, endpoint, credential owner, trusted origins, and recovery contract are recorded. |
| Production | Unavailable. |

The implementation tracker is
`docs/trackers/wip/openminion-commerce-order-lifecycle-readiness-2026-10-06-tracker.md`
in the workspace documentation repository.
