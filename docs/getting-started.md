# OpenMinion Getting Started

Status: active
Last updated: 2026-06-20

Purpose: give contributors and automation authors a package-local bootstrap and
execution summary for work inside the `openminion` repo.

## Fast bootstrap

```bash
cd openminion
python3.11 -m venv .venv
source .venv/bin/activate
make dev-install
make hooks-install
```

If you are running the CLI locally, also set:

```bash
export OPENMINION_HOME=.
export OPENMINION_DATA_ROOT="$OPENMINION_HOME/.openminion"
```

## Read first

Before substantial code changes, read:

1. [`engineering-patterns.md`](engineering-patterns.md)
2. [`code-quality-enforcement.md`](code-quality-enforcement.md)
3. [`source-tree-owner-map.md`](source-tree-owner-map.md)
4. [`runtime-surfaces.md`](runtime-surfaces.md)

## Normal execution loop

1. Pick one focused change.
2. Implement code and docs together when the public surface changes.
3. Add or update tests for the behavior you changed.
4. Run focused validation while iterating.
5. Run `make lint` before calling the work ready.
6. Record validation commands in the PR description.

## Pull request shape

Preferred PR shape:

1. short, GitHub-native title,
2. flat line-item bullets that summarize what changed,
3. plain `Validation` label followed by exact command bullets.

Example:

`Add package-local workspace sync helpers`

- add typed workspace sync planning
- add explicit apply/status helpers
- align public docs

Validation
- `make lint`
- `python -m pytest -q tests/<target>`

## Commit message shape

Use commit messages in the form:

1. `<type>(<scope>): <summary>`

Approved current types are:

1. `feat`
2. `fix`
3. `docs`
4. `refactor`
5. `test`
6. `chore`
7. `style`
8. `build`

Guidance:

1. include a scope by default in `openminion`,
2. choose a real owner scope such as `agent`, `api`, `cli`, `e2e`, `gateway`,
   `runtime`, `telemetry`, `tool`, `tools`, or `tui`,
3. keep the summary specific to the landed change,
4. avoid vague subjects like `update`,
5. prefer the most specific truthful type; do not use `chore` when `docs`,
   `test`, `refactor`, or `build` is more accurate,
6. do not use local shorthand or planning labels as normal commit types.

## Boundary reminder

1. `README.md` is the package contract and install surface.
2. `API_COMPATIBILITY.md` is the public import/export promise.
3. `docs/` is the package-local public docs layer.
4. `tests/` and `scripts/` are important, but they are not public library API.
