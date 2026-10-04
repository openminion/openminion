# Search Tool

Owner: `openminion-tools`

`search/` is the category owner for web-search tooling.

1. The root package owns the shared search facade, provider chain resolution,
   family-level routing, and registration.
2. Provider implementations live under `search/providers/`.
3. Current providers: `brave`, `duckduckgo`, `firecrawl`, `serpapi`, `serper`,
   `tavily`, and `tinyfish`.
4. New search providers should land as `search/providers/<provider>/` and
   register through `search.register_provider(...)`.

`firecrawl` and `duckduckgo` work without local credentials. Firecrawl accepts
an optional API key for account-backed limits; DuckDuckGo uses its HTML search
surface as the final best-effort fallback. The default chain tries Tavily,
Brave, SerpAPI, Serper, and TinyFish before the keyless Firecrawl and DuckDuckGo
fallbacks. TinyFish is free but still requires an API key.
