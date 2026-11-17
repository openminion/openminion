# Configuration compatibility notes

Status: public preview

Some settings are accepted, parsed and re-serialized by the configuration loader
without changing runtime behaviour, because the behaviour they once described was
removed or is owned elsewhere. This page is the single canonical note for each
one, so operators can tell a supported knob from a compatibility field without
reading the source.

## `self_improvement.max_applied_notes`

Top-level key under `self_improvement` in the stored configuration.

- **Accepted and preserved.** The parser reads it and the serializer writes it
  back. The parser normalizes `0` to `1`; serializer formatting is a separate
  concern from the value itself.
- **No current production consumer in the note-selection path.** The effective
  cap is `CONTEXT_IMPROVEMENT_NOTE_LIMIT = 6` in `modules/context/config.py`,
  applied when the context pack is ranked for the `decide` purpose in
  `modules/context/retrieval/bundle.py`. This setting does not change it.
- **Operator guidance:** leave it unset. Changing the effective cap is a source
  change in the context owner, not a configuration change.

## `self_improvement.min_token_overlap`

Top-level key under `self_improvement` in the stored configuration.

- **Accepted and preserved**, but inert.
- **Historical note:** it selected notes by token overlap before the lexical
  overlap selector was removed under SIRH-03. It does not re-enable that
  behaviour, and current matching is structural: active status, agent ownership,
  and `tool:*` / `error:*` tags.
- **Operator guidance:** leave it unset. Setting it cannot restore lexical
  matching, and no current in-tree runtime note-selection path reads it. The
  parser and serializer do handle the key; out-of-tree consumers were not
  audited.

## Measured behaviour these fields do not change

With eight active matching notes and candidate/foreign-agent neighbours present:

| setting | overlay cards | final note references |
| --- | --- | --- |
| default (`max_applied_notes: 3`) | 8 | 6 |
| `max_applied_notes: 1` | 8 | 6 |
| `max_applied_notes: 100` | 8 | 6 |
| `min_token_overlap: 999` | 8 | 6 |

Disabling self-improvement produces no overlay at all, candidate and
foreign-agent notes stay out, and a turn with no matching tool context produces
no overlay. The context-owner limit of six is what bounds the references.

## Related

- [`runtime-surfaces.md`](runtime-surfaces.md) for the supported runtime
  surfaces these settings sit behind.
- [`turn-budgets.md`](turn-budgets.md) for the context and turn budgets that the
  effective cap belongs to.
