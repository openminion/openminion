---
name: commerce-order-care
description: Review a merchant checkout, place an explicitly approved order, or care for an existing order through the commerce tools.
id: commerce-order-care
version: 1.0.0
risk: high
tools:
  - commerce.inspect
  - commerce.prepare_order
  - commerce.place_order
  - commerce.prepare_order_action
  - commerce.apply_order_action
  - task.watch
verification:
  - Match the returned order or action reference to the reviewed preparation.
  - Report the typed lifecycle and unresolved outcome without inferring success.
recipe:
  objective: Complete only the requested merchant operation with exact approval and verified transaction facts.
  preflight:
    - Require configured commerce tools and a trusted local subject and session.
    - Select purchase or existing-order care from the user's request; do not perform both by default.
  steps:
    - step_id: inspect-product
      tool_id: commerce.inspect
      instruction: For a purchase, inspect the requested product and use returned offer, variant, availability, and price facts.
    - step_id: prepare-order
      tool_id: commerce.prepare_order
      instruction: For a purchase, request one-time preparation approval for the chosen items; preparation does not authorize placement.
    - step_id: place-order
      tool_id: commerce.place_order
      instruction: Only if placement was requested, submit the exact returned preparation, reference, and digest through a separate one-time order approval.
    - step_id: inspect-order
      tool_id: commerce.inspect
      instruction: Inspect the subject-owned local order reference; retain its current revision and independent payment, fulfillment, shipment, and refund states.
    - step_id: prepare-care
      tool_id: commerce.prepare_order_action
      instruction: Only for requested order care, prepare the exact supported action against the current order revision, affected items, quantities, and refund method.
    - step_id: apply-care
      tool_id: commerce.apply_order_action
      instruction: Only after reviewing fees, refund destination, deadlines, and consequences, submit the exact action preparation through its own one-time approval.
    - step_id: verify-order
      tool_id: commerce.inspect
      instruction: Verify the returned order lifecycle; distinguish pending, declined, unknown, and completed outcomes rather than claiming payment or refund completion from submission.
    - step_id: watch-order
      tool_id: task.watch
      instruction: Only when the user requests monitoring, create an existing commerce_order routine for the local order with bounded checks or expiry and material-change delivery.
  verification:
    - Confirm exact references and typed lifecycle facts using commerce.inspect.
    - A denied approval must cause no merchant mutation.
  rollback:
    - An order cannot be locally rolled back; a requested cancellation or return needs its own preparation and approval.
  stop_conditions:
    - Stop on denial, missing trusted ownership, stale preparation, or changed merchant terms.
    - For outcome_unknown, preserve the original transaction identity and require recovery; never submit with a new identity to force a retry.
    - For handoff_required, present only the safe returned handoff; do not substitute browser checkout or payment actions.
  idempotency_notes: Transaction tools own durable reservation and recovery. A plan resume or skill version is not a new transaction or an authorization grant.
  safety_notes:
    - Skill admission and workflow selection confer no transaction permission.
    - Keep secrets in configured credential storage; never request raw payment tokens in tool arguments.
---

# Summary
Use the structured recipe as guidance for a model-authored plan, not an executable
purchase engine. Select only the steps needed for the requested purchase or care
operation. An existing order does not require the purchase steps again.

# Procedure
Use exact typed commerce results to fill subsequent tool arguments. Keep each
preparation and mutation separate so ordinary policy confirmation remains in
control. Do not infer an order, payment, shipment, or refund outcome from prose.
If terms change, stop and obtain a newly reviewed preparation and approval.
Monitoring uses the existing task routine; it must never place or modify orders.

# Verification
Report the local reference and observed lifecycle, including pending or unknown
states. A successful tool invocation alone is not proof of a completed refund.

# Rollback
Do not delete durable transaction history or invent a compensating purchase.
Offer supported order-care preparation only when the user requests that action.
