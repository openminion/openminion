from __future__ import annotations

import httpx
import pytest

from openminion.base.config import OpenMinionConfig
from openminion.modules.llm.errors import LLMCtlError
from openminion.modules.llm.providers.transport.client import ProviderHTTPClient
from openminion.modules.llm.providers.transport.http import http_json_get
from openminion.modules.llm.providers.transport.sse import iter_sse_post_lines


def _client_with_transport(handler) -> tuple[ProviderHTTPClient, httpx.Client]:
    client = ProviderHTTPClient()
    httpx_client = httpx.Client(transport=httpx.MockTransport(handler))
    client._client = httpx_client
    return client, httpx_client


def test_provider_http_client_reuses_one_client_for_json_requests(monkeypatch) -> None:
    requests: list[httpx.Request] = []
    client_configs: list[dict[str, object]] = []
    httpx_client_type = httpx.Client

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json={"ok": True})

    def build_client(**kwargs: object) -> httpx.Client:
        client_configs.append(kwargs)
        return httpx_client_type(transport=httpx.MockTransport(handler), **kwargs)

    monkeypatch.setattr(
        "openminion.modules.llm.providers.transport.client.httpx.Client",
        build_client,
    )
    client = ProviderHTTPClient()
    try:
        for _ in range(2):
            assert http_json_get(
                url="https://provider.example/models",
                headers={},
                timeout_seconds=1,
                provider_name="provider",
                http_client=client,
            ) == {"ok": True}
    finally:
        httpx_client = client._client
        client.close()

    assert client_configs == [{"follow_redirects": True, "trust_env": True}]
    assert len(requests) == 2
    assert httpx_client is not None and httpx_client.is_closed


def test_provider_http_client_preserves_http_error_mapping() -> None:
    client, _ = _client_with_transport(
        lambda _request: httpx.Response(401, json={"error": "bad key"})
    )
    try:
        with pytest.raises(LLMCtlError) as exc_info:
            http_json_get(
                url="https://provider.example/models",
                headers={},
                timeout_seconds=1,
                provider_name="provider",
                http_client=client,
            )
    finally:
        client.close()

    assert exc_info.value.code == "AUTH_ERROR"
    assert exc_info.value.details["status_code"] == 401


def test_provider_http_client_preserves_timeout_mapping() -> None:
    def timeout(_request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("timed out")

    client, _ = _client_with_transport(timeout)
    try:
        with pytest.raises(LLMCtlError) as exc_info:
            http_json_get(
                url="https://provider.example/models",
                headers={},
                timeout_seconds=1,
                provider_name="provider",
                http_client=client,
            )
    finally:
        client.close()

    assert exc_info.value.code == "TIMEOUT"


def test_provider_http_client_streams_sse_lines() -> None:
    client, _ = _client_with_transport(
        lambda _request: httpx.Response(
            200,
            content=b"data: first\n\ndata: [DONE]\n\n",
        )
    )
    try:
        lines = list(
            iter_sse_post_lines(
                url="https://provider.example/stream",
                payload={"stream": True},
                headers={},
                timeout_seconds=1,
                provider_name="provider",
                http_client=client,
            )
        )
    finally:
        client.close()

    assert lines == ["data: first", "", "data: [DONE]", ""]


def test_provider_connection_reuse_config_defaults_on_with_rollback() -> None:
    default_config = OpenMinionConfig.from_dict({})
    rollback_config = OpenMinionConfig.from_dict(
        {
            "providers": {
                "openrouter": {"http_connection_reuse_enabled": False},
            }
        }
    )

    assert default_config.providers.openrouter.http_connection_reuse_enabled is True
    assert rollback_config.providers.openrouter.http_connection_reuse_enabled is False
