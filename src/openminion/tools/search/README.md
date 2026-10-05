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
fallbacks. Empty provider results continue to the next configured provider.
TinyFish is free but still requires an API key. Provider credentials belong in
runtime config or environment variables, not `web.search` model arguments.
Required-key providers use `TAVILY_API_KEY`, `BRAVE_API_KEY`,
`SERPAPI_API_KEY`, `SERPER_API_KEY`, or `TINYFISH_API_KEY`; Firecrawl also reads
the optional `FIRECRAWL_API_KEY` for account-backed limits.
