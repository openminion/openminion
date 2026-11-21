from __future__ import annotations

import io
import ipaddress
import logging
import re
import socket
from collections.abc import Iterator, Mapping
from threading import Lock
from urllib import error as urllib_error
from urllib import request as urllib_request

import httpx

logger = logging.getLogger(__name__)

# A no_proxy entry that is exactly a bracketed IPv6 literal without a port,
# e.g. "[::1]" or "[fd8b:4f84::1]". Entries with an explicit port
# ("[::1]:8080") do not match and are left alone.
_BRACKETED_IPV6_LITERAL = re.compile(r"^\[(?P<host>[0-9A-Fa-f:]*:[0-9A-Fa-f:]*)\]$")


def _sanitize_no_proxy_value(value: str) -> tuple[str, list[str]]:
    """Rewrite bracketed IPv6 literals in a no_proxy value to unbracketed form.

    httpx parses every no_proxy entry as a URL pattern and raises
    ``InvalidURL: Invalid port: ':1]'`` for a bracketed IPv6 literal without
    a port, although the unbracketed form (``::1``) is accepted and names
    the same host. Returns the rewritten value plus the entries that changed.
    """
    sanitized: list[str] = []
    changed: list[str] = []
    for raw_entry in value.split(","):
        entry = raw_entry.strip()
        match = _BRACKETED_IPV6_LITERAL.match(entry)
        if match:
            sanitized.append(match.group("host"))
            changed.append(entry)
        else:
            sanitized.append(entry)
    return ",".join(sanitized), changed


def _bypass_pattern(host: str) -> str:
    """Return the mount pattern httpx uses to mark a no_proxy host as bypassed.

    Mirrors the pattern shapes ``httpx`` builds from no_proxy entries, so a
    locally rebuilt proxy configuration keeps the same matching behavior.
    """
    if "://" in host:
        return host
    try:
        ip = ipaddress.ip_address(host.split("/")[0])
    except ValueError:
        ip = None
    if ip is not None:
        return f"all://{host}" if ip.version == 4 else f"all://[{host}]"
    if host.lower() == "localhost":
        return f"all://{host}"
    return f"all://*{host}"


def _client_with_corrected_no_proxy() -> httpx.Client | None:
    """Build a client from a locally corrected proxy configuration.

    Fallback for the one construction failure this transport corrects: a
    bracketed IPv6 literal in no_proxy. The environment proxy map is
    rebuilt with those entries unbracketed and passed as explicit mounts
    for this client only; the process environment is only read, never
    written. ``trust_env=False`` stops httpx re-reading that proxy
    environment, so the default transport is built explicitly with
    httpx's own defaults, which keep the environment's TLS trust
    (``SSL_CERT_FILE`` / ``SSL_CERT_DIR``) for direct requests, exactly
    as in the default client. Returns None when no entry needed
    rewriting, i.e. the construction failure is unrelated to no_proxy.
    """
    proxies = urllib_request.getproxies()
    sanitized, changed = _sanitize_no_proxy_value(proxies.get("no", ""))
    if not changed:
        return None
    logger.warning(
        "no_proxy entries %s are bracketed IPv6 literals that httpx cannot "
        "parse; building this client with a locally corrected proxy "
        "configuration instead.",
        changed,
    )
    mounts: dict[str, httpx.BaseTransport | None] = {}
    entries = [entry.strip() for entry in sanitized.split(",") if entry.strip()]
    if "*" not in entries:
        for scheme in ("http", "https", "all"):
            proxy_url = proxies.get(scheme)
            if proxy_url:
                if "://" not in proxy_url:
                    proxy_url = f"http://{proxy_url}"
                mounts[f"{scheme}://"] = httpx.HTTPTransport(proxy=proxy_url)
        for host in entries:
            mounts[_bypass_pattern(host)] = None
    return httpx.Client(
        follow_redirects=True,
        trust_env=False,
        transport=httpx.HTTPTransport(),
        mounts=mounts,
    )


class _ProviderHTTPResponse:
    def __init__(self, response: httpx.Response) -> None:
        self._response = response
        self.status = response.status_code
        self.headers = response.headers

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
        try:
            client = self._get_client()
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
        except httpx.InvalidURL as exc:
            raise urllib_error.URLError(str(exc)) from exc
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
                try:
                    self._client = httpx.Client(follow_redirects=True, trust_env=True)
                except httpx.InvalidURL:
                    # See _client_with_corrected_no_proxy for the one
                    # failure this fallback corrects; anything else
                    # propagates unchanged.
                    client = _client_with_corrected_no_proxy()
                    if client is None:
                        raise
                    self._client = client
            return self._client


def http_client_for_config(
    client: ProviderHTTPClient,
    config: Mapping[str, object],
) -> ProviderHTTPClient | None:
    return client if config.get("http_connection_reuse_enabled", False) else None
