# Browser Tool

Owner: `openminion-tools`

`browser/` is the category owner for browser automation tooling.

1. The root package owns shared browser models, routing, dispatch, and the
   provider registry.
2. Provider implementations live under `browser/providers/`.
3. Current providers: `pinchtab`, `playwright`.
4. New browser providers should land as `browser/providers/<provider>/` and
   register through `browser.register_provider(...)`.

## Provider selection priority order

`BrowserRouter.select_provider(...)` resolves the provider id in this exact
precedence order. The first non-empty, registered candidate wins. The token
`""` or `"auto"` is treated as "not specified" at each layer so it falls
through to the next:

1. `requested_provider` — explicit `provider=...` on the tool call.
2. `agent_profile_provider` — profile-level binding.
3. `session_provider_override` — session-scope operator override.
4. `tab_id` affinity — tab previously bound to a provider via
   `remember_affinity`.
5. `instance_id` affinity — instance previously bound to a provider.
6. `runtime_default_provider` — process-level runtime default (e.g.
   composition-root override).
7. `runtime_provider_order` — process-level runtime preference list.
8. `BrowserRoutingConfig.default_provider` — config-level operator default.
9. `BrowserRoutingConfig.provider_order` — config-level preference list.
10. Built-in fallback: `pinchtab`, then `playwright`, then the first
    registered provider id (alphabetical via `list_provider_ids`).

If no provider id can be resolved and no providers are registered, the
router raises `KeyError("no browser provider specified and no default
configured")`.

Reproducible examples (see `openminion/tests/browser/test_router.py`):

1. `requested="arg"`, `agent_profile="profile"`, `session="session"`,
   `default="default"` → resolves to `arg`.
2. `requested=None`, `agent_profile="profile"`, `session="session"` →
   resolves to `profile`.
3. `requested=""`, `agent_profile=""`, `session="session"` → resolves to
   `session`.
4. `requested=""`, `agent_profile=""`, `session=""`, `tab_id="tab-1"`
   bound to `playwright` via `remember_affinity` → resolves to `playwright`
   even when `default_provider="pinchtab"`.
5. `requested=""`, `agent_profile=""`, `session=""`, no affinity,
   `runtime_default_provider="playwright"` → resolves to `playwright`.
6. `requested="auto"`, `runtime_default_provider="playwright"` → treated as
   implicit and resolves to `playwright`.
7. No registered providers → `select_provider(...)` raises `KeyError`.

PinchTab / Playwright coexistence guidance:

1. Operators that want PinchTab as the default but allow Playwright when an
   agent or tab is already pinned to it should set
   `default_provider="pinchtab"` and rely on the affinity layer — tabs/
   instances opened against Playwright retain Playwright on follow-up
   calls.
2. Operators that want Playwright as the default fallback while still
   honoring PinchTab pins should set `default_provider="playwright"`; the
   `requested -> profile -> session -> affinity` chain still wins for
   per-call routing.
3. To make selection deterministic in CI, set both `default_provider` and
   `provider_order=(default, alternate)` so the implicit-default branch
   never falls through to the alphabetical built-in fallback.
