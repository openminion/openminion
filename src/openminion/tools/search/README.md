# Search Tool

Owner: `openminion-tools`

`search/` is the category owner for web-search tooling.

1. The root package owns the shared search facade, provider chain resolution,
   family-level routing, and registration.
2. Provider implementations live under `search/providers/`.
3. Current providers: `brave`, `firecrawl`, `serpapi`, `serper`, `tavily`.
4. New search providers should land as `search/providers/<provider>/` and
   register through `search.register_provider(...)`.
