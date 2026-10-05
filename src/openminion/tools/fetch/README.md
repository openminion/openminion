# Fetch Tool

Owner: `openminion-tools`

`fetch/` is the category owner for HTTP/document retrieval tooling.

1. The root package owns the fetch facade, artifact formatting, and provider
   selection.
2. Provider implementations live under `fetch/providers/`.
3. Current providers: `core_http`, `scrapling`, `firecrawl`, and `tinyfish`.
4. New fetch providers should land as `fetch/providers/<provider>/` and
   register through `fetch.register_provider(...)`.

`core_http` remains the no-configuration default. Firecrawl scrape also works
without a local credential and accepts an optional API key for account-backed
limits.

Automatic selection follows the configured provider order and fallback policy.
An explicitly requested backend is exact. If every automatic backend fails,
the error details include the ordered backend chain and each failed attempt.
