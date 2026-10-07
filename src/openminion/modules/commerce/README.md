# Commerce and order lifecycle

Owner: `openminion-commerce`
Shape: `engine-owning`
Runtime peer: standalone (no `services/` peer)

`openminion.modules.commerce` owns closed commerce schemas, merchant-provider
contracts, redacted order records, and coordination of one-time physical-goods
orders. It covers preparation, exact placement, tracking, cancellation,
returns, refunds, and explicit merchant handoff.

## Boundaries

1. PolicyCtl remains the authorization and one-time grant owner.
2. SecretService remains the credential, buyer-profile, and payment-token owner.
3. RecordStore remains the persistence engine.
4. Task and cron remain the monitoring and scheduling owners.
5. Browser tools may support user handoff but never commit commerce mutations.
6. Commerce records and model-visible results contain only redacted references,
   stable digests, and safe labels.

## Package shape

`constants.py` owns the schema version, trusted local subject, and closed state
vocabularies. `models.py` contains the closed `commerce-v1` schemas. Provider,
runtime, storage, confirmation, and fixture owners are added as their tracker
slices land. The module uses direct composition and does not define a workflow
engine, provider registry, payment vault, retry manager, or service wrapper.

Focused tests live under `tests/commerce/`.
