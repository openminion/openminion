import os

from openminion.base.config import OTELExporterConfig

LOGFIRE_OTLP_ENDPOINT = "https://logfire-api.pydantic.dev/v1/traces"
_LOGFIRE_TOKEN_ENV = "LOGFIRE_TOKEN"
_OTLP_HEADERS_ENV = "OTEL_EXPORTER_OTLP_HEADERS"


def build_logfire_otel_config(
    *,
    service_name: str = "openminion",
    sample_rate: float = 1.0,
    token: str | None = None,
    env: dict[str, str] | None = None,
) -> OTELExporterConfig | None:
    """Build logfire otel config helper."""

    env_dict = env if env is not None else os.environ
    resolved_token = (token or env_dict.get(_LOGFIRE_TOKEN_ENV, "")).strip()
    if not resolved_token:
        return None

    # Forward the bearer token to the OTLP SDK via the OTel standard envvar.
    # This is the documented Logfire integration path for OTLP-compatible
    # exporters that do not surface a `headers=` kwarg themselves.
    env_dict[_OTLP_HEADERS_ENV] = f"Authorization=Bearer {resolved_token}"

    return OTELExporterConfig(
        enabled=True,
        endpoint=LOGFIRE_OTLP_ENDPOINT,
        protocol="http",
        service_name=service_name,
        sample_rate=sample_rate,
    )
