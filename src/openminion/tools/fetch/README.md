# Fetch Tool

Owner: `openminion-tools`

`fetch/` is the category owner for HTTP/document retrieval tooling.

1. The root package owns the fetch facade, artifact formatting, and provider
   selection.
2. Provider implementations live under `fetch/providers/`.
3. Current providers: `core_http`, `scrapling`.
4. New fetch providers should land as `fetch/providers/<provider>/` and
   register through `fetch.register_provider(...)`.
