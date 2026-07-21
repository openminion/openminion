# Review Memory Changes Before Applying Them

Status: Current operator guidance

OpenMinion can export memory into a deterministic JSON review artifact, build a
no-write import plan, record an explicit human decision, and apply only the
approved unchanged plan.

## Source Of Truth

The configured memory database remains the durable source of truth. Review
JSON and Markdown files are evidence artifacts, not retrieval sources or a
second memory backend. Markdown is display-only and is never accepted by
`review plan` or `review apply`.

The existing low-level `memory export` and `memory import` commands remain
available and keep their `memory_bundle.v1` behavior. Use the review workflow
when a human approval boundary is required.

## Workflow

Export canonical JSON and an optional display companion:

```bash
memctl review export \
  --scope agent:source \
  --out ./memory-review.json \
  --markdown-out ./memory-review.md \
  --db ./source-memory.db
```

Inspect versions, digests, counts, and warnings without printing raw memory:

```bash
memctl review inspect --artifact ./memory-review.json
```

Plan against the current target without writing to it:

```bash
memctl review plan \
  --artifact ./memory-review.json \
  --out ./memory-plan.json \
  --scope-rewrite agent:source=agent:target \
  --conflict error \
  --db ./target-memory.db
```

Record an explicit decision:

```bash
memctl review decide \
  --plan ./memory-plan.json \
  --out ./memory-receipt.json \
  --reviewer operator@example \
  --decision approve \
  --db ./target-memory.db
```

Apply the exact approved artifact and plan:

```bash
memctl review apply \
  --artifact ./memory-review.json \
  --plan ./memory-plan.json \
  --receipt ./memory-receipt.json \
  --db ./target-memory.db
```

## Safety And Recovery

Reviewed apply v1 supports only OpenMinion's built-in SQLite backend. Planning
remains available for other backends, but apply fails before mutation.

Before SQLite apply, OpenMinion writes both a portable target bundle and a
consistent SQLite backup under:

```text
<generated-root>/memory-review/<plan-id>/
```

If an operation fails, OpenMinion restores the SQLite backup. A successful
restore reports `rollback_succeeded`; a failed restore reports
`rollback_failed` and keeps the backup path for operator recovery. The command
never reports a partial import as successfully applied.

Approval binds the artifact digest, plan digest, normalized import options, and
target fingerprint. Editing the JSON, changing options, changing target memory,
omitting the receipt, or using a rejected receipt fails before the first
mutation.

## Audit Evidence

The memory audit database records `memory.review.*` events for export, plan,
decision, apply, failure, and rollback. Events contain identifiers, digests,
reviewer identity, counts, and reason codes. They do not contain raw memory
contents.

Review files are written with owner-only permissions where the platform
supports them. Keep them in an operator-controlled directory because the JSON
artifact itself contains the memory payload being reviewed.
