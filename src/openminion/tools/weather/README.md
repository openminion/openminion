# Weather Tool

Owner: `openminion-tools`

`weather/` is the category owner for weather lookup tooling.

1. The root package owns the shared facade, provider chain resolution, and
   family-level contract.
2. Provider implementations live under `weather/providers/`.
3. Current providers: `openmeteo`, `weatherapi`.
4. New weather providers should land as `weather/providers/<provider>/` and
   register through `weather.register_provider(...)`.

The default chain tries Open-Meteo before WeatherAPI. Static sample data is
disabled by default and is never marked as verified live weather when an
operator explicitly enables it.
