from __future__ import annotations

import io
import socket
from collections.abc import Iterator, Mapping
from threading import Lock
from urllib import error as urllib_error
from urllib import request as urllib_request

import httpx


class _ProviderHTTPResponse:
    def __init__(self, response: httpx.Response) -> None:
        self._response = response
        self.status = response.status_code

    def read(self) -> bytes:
        return self._response.read()

    def __iter__(self) -> Iterator[bytes]:
        for line in self._response.iter_lines():
            yield line.encode("utf-8")

    def __enter__(self) -> "_ProviderHTTPResponse":
        return self

    def __exit__(self, *args: object) -> None:
        self._response.close()


class ProviderHTTPClient:
    """Provider-owned HTTP client with persistent connection reuse."""

    transport_name = "httpx_pool"

    def __init__(self) -> None:
        self._client: httpx.Client | None = None
        self._lock = Lock()

    def urlopen(
        self,
        request: urllib_request.Request,
        timeout: float | None = None,
    ) -> _ProviderHTTPResponse:
        client = self._get_client()
        try:
            response = client.send(
                client.build_request(
                    request.get_method(),
                    request.full_url,
                    content=request.data,
                    headers=dict(request.header_items()),
                    timeout=timeout,
                ),
                stream=True,
            )
        except httpx.TimeoutException as exc:
            raise urllib_error.URLError(socket.timeout(str(exc))) from exc
        except httpx.RequestError as exc:
            raise urllib_error.URLError(str(exc)) from exc

        if response.status_code >= 400:
            body = response.read()
            response.close()
            raise urllib_error.HTTPError(
                request.full_url,
                response.status_code,
                response.reason_phrase,
                response.headers,
                io.BytesIO(body),
            )
        return _ProviderHTTPResponse(response)

    def close(self) -> None:
        with self._lock:
            client = self._client
            self._client = None
        if client is not None:
            client.close()

    def _get_client(self) -> httpx.Client:
        with self._lock:
            if self._client is None:
                self._client = httpx.Client(follow_redirects=True, trust_env=True)
            return self._client


def http_client_for_config(
    client: ProviderHTTPClient,
    config: Mapping[str, object],
) -> ProviderHTTPClient | None:
    return client if config.get("http_connection_reuse_enabled", False) else None
