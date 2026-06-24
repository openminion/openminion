# git stash clear — approval criteria

## Approve when

- Rationale explicitly states all stashes have been applied or are
  intentionally discardable (e.g. session cleanup).

## Reject when

- No rationale.
- Stash list contains entries whose content is unknown to the agent.

## Escalate when

- The stash list is non-empty and the verifier cannot enumerate
  per-stash safety; operator confirmation resolves.
