# Tools Package

Owner: `openminion-tools`

This package hosts tool plugin implementations under the `openminion.tools`
namespace.

Layout contract:

1. Single-provider tool categories stay flat at the package root
   (for example `file/`, `exec/`, `code/`, `time/`).
2. Multi-provider tool categories keep the category owner at the root and place
   provider implementations under `providers/`.
3. Category roots own shared registries, schemas, routing, and family-level
   behavior.
4. Provider packages own provider-specific config, constants, interfaces, and
   plugin registration glue.

Current multi-provider categories:

1. `search/providers/`
2. `browser/providers/`
3. `weather/providers/`
4. `fetch/providers/`

When adding a new provider to an existing category, place it under that
category's `providers/` package and register it through the category owner.

Contributor-surface rule:

1. top-level category packages are the navigation and extension-point owners,
2. `providers/` is the supported provider slot only for categories that
   actually use the multi-provider pattern,
3. deep helper paths such as backends, registrars, repair helpers, and other
   support modules remain internal unless a category README says otherwise.

For contributor authoring rules beyond layout — including family
classification (`declarative candidate` vs `provider-only minimal` vs
`intentionally bespoke`), typed execution-fact guidance, and shared
formatter / approval / provenance ownership — see
the package-local contributor and code-quality guidance.
