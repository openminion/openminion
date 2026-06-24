# git branch --force-delete — approval criteria

## Approve when

- The branch has been merged (rationale references the merge commit).
- The branch is an obvious throw-away experiment (`scratch/*`,
  `wip/*` prefix) and the rationale states so.

## Reject when

- The branch is the current branch (HEAD).
- The branch is the default branch.
- No rationale is supplied.

## Escalate when

- The branch carries unmerged commits not present elsewhere.
- The verifier cannot determine reachability of the branch's tip.
