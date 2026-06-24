# git stash drop — approval criteria

## Approve when

- The dropped stash is explicitly named (`stash@{N}` or an explicit
  index) and the rationale references prior application of the same
  stash.

## Reject when

- No rationale supplied.
- The stash index is ambiguous (e.g. shorthand without a clear ref).

## Escalate when

- The stash contains uncommitted work that has not been applied
  elsewhere and the verifier cannot confirm safety.
