# Contributing to OpenMinion

Thanks for contributing.

## Before coding

Read these docs before coding:

1. [Engineering Patterns](docs/engineering-patterns.md)
2. [Code Quality Enforcement](docs/code-quality-enforcement.md)
3. [Pre-Authoring Code Simplicity and Readability](docs/pre-authoring-code-simplicity-and-readability-guideline.md)
4. [Getting Started](docs/getting-started.md)
5. [OpenMinion Code Quality and Hygiene](./CODE_QUALITY.md)
6. [Testing and Validation](docs/testing-and-validation.md)

Treat items 1 and 2 as a pair:

1. env centralization,
2. `config.py` vs `constants.py` vs `paths.py`,
3. shared-owner and compatibility-wrapper rules,
4. explicit registration/routing patterns,
5. refactor validation discipline,
6. active CI gates, validator scripts, and cleanup categories.

Treat item 3 as the pre-write simplicity companion:

1. write direct code first,
2. avoid wrappers that add no policy or validation,
3. prefer concrete names and real owners,
4. avoid speculative event/callback abstraction when a direct call is clearer,
5. optimize for human readability before relying on cleanup later.

Use item 5 as the short contributor rulebook when you need a tighter package
boundary summary than the broader docs.

## Quick start

1. Fork and create a branch.
2. Make focused changes.
3. Add or update tests.
4. Open a PR with a clear summary.

## Development basics

1. Follow existing style and project conventions.
2. Keep PRs small and reviewable.
3. Include validation commands and results in the PR description.
4. Prefer a short GitHub-native PR title plus a flat bullet summary of what the
   commit set landed.
5. Keep PR descriptions easy to scan and easy to copy:
   1. short title
   2. bullet summary of changes
   3. validation commands/results
6. For broad cleanup/code-quality lanes, start from a fresh live file inventory instead of a hand-picked subset. Preferred command:
   ```bash
   cd openminion
   rg --files src/openminion -g '*.py' | sort
   ```
7. Keep temporary broad-sweep artifacts in the repository scratch area, not in this package root and not mixed into package source or docs surfaces.
8. Prefer task-scoped validation during slice work; reserve broad repo-wide suites like `make check` for integration closeout or when the tracker explicitly requires them.
9. Do not include unrelated refactors in the same PR.

Preferred PR shape:

1. `Title`
   - short and literal, for example `Add workspace persistence mode`
2. `Description`
   - `- add ...`
   - `- align ...`
   - `- polish ...`
3. `Validation`
   - `- <command>`
   - `- <command>`

## Legal basics (plain English)

To keep contribution friction low:

1. You keep ownership of your contributions.
2. By submitting a contribution, you license it under the project license (Apache License 2.0).
3. Apache-2.0 includes a patent license for your contribution, with the standard patent-termination condition in the license text.
4. Only submit code/content you have the right to contribute.
5. Do not add third-party code/assets unless their license is compatible and clearly documented.
6. Project names/logos are not granted for endorsement use.
7. OpenMinion is provided on an "as is" basis under the project license; there are no guarantees about performance, reliability, availability, cost outcomes, or malfunction-related consequences.
8. If you configure third-party providers or paid infrastructure while developing or testing, you are responsible for any resulting charges.
9. See [LICENSE](./LICENSE) for the full legal terms, disclaimers, and limitations of liability.

## Security

If you find a security issue, do not open a public issue with exploit details. Use the project security reporting process.

## Code of conduct

By participating, you agree to follow [CODE_OF_CONDUCT.md](./CODE_OF_CONDUCT.md).
