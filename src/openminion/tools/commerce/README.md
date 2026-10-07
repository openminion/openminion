# Commerce Tool Integration

This package owns merchant adapters, typed tool arguments, exact approval facts,
transaction persistence, idempotency, and recovery. The historical `commerce`
database identity and migration revisions remain unchanged.

Commerce procedures are authored skills composed through the existing skill,
brain, task, and session owners. They are not a framework module or a new
workflow engine. See `examples/skills/commerce-order-care/SKILL.md`.

Shared execution receives trusted tool resources and tool-declared callbacks;
it does not interpret commerce payloads or choose business steps. Merchant
requests and exact financial authorization remain enforced at this boundary.
