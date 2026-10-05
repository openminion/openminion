from __future__ import annotations

from collections.abc import Mapping
from html.parser import HTMLParser
from typing import Any
from urllib import error as urllib_error
from urllib import parse as urllib_parse
from urllib import request as urllib_request

from openminion.tools.search.providers import SearchProviderError

from .config import DuckDuckGoSearchProviderConfig
from .constants import (
    DEFAULT_DUCKDUCKGO_SEARCH_TIMEOUT_SECONDS,
    DEFAULT_DUCKDUCKGO_SEARCH_URL,
    DUCKDUCKGO_SEARCH_DISPLAY_NAME,
    DUCKDUCKGO_SEARCH_PROVIDER_ID,
)


def _error_code_for_status(status: int) -> str:
    if status in {400, 422}:
        return "INVALID_REQUEST"
    if status == 429:
        return "RATE_LIMITED"
    return "UPSTREAM_ERROR"


def _result_url(href: str) -> str:
    value = href.strip()
    if value.startswith("//"):
        value = f"https:{value}"
    parsed = urllib_parse.urlparse(value)
    if parsed.path.startswith("/l/") and parsed.hostname in {None, "duckduckgo.com"}:
        target = urllib_parse.parse_qs(parsed.query).get("uddg", [""])[0]
        return target.strip() or value
    return value


class _DuckDuckGoHTMLParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._current: dict[str, Any] | None = None
        self._capture: str | None = None
        self._capture_tag: str | None = None
        self.results: list[dict[str, str]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = dict(attrs)
        classes = set((attributes.get("class") or "").split())
        if tag == "a" and "result__a" in classes:
            self._finish_result()
            self._current = {
                "url": _result_url(attributes.get("href") or ""),
                "title": [],
                "description": [],
            }
            self._capture = "title"
            self._capture_tag = tag
        elif self._current is not None and "result__snippet" in classes:
            self._capture = "description"
            self._capture_tag = tag

    def handle_endtag(self, tag: str) -> None:
        if tag == self._capture_tag:
            self._capture = None
            self._capture_tag = None

    def handle_data(self, data: str) -> None:
        if self._current is not None and self._capture is not None:
            self._current[self._capture].append(data)

    def finish(self) -> list[dict[str, str]]:
        self._finish_result()
        return self.results

    def _finish_result(self) -> None:
        if self._current is None:
            return
        url = str(self._current["url"] or "").strip()
        if url:
            title = " ".join("".join(self._current["title"]).split())
            description = " ".join("".join(self._current["description"]).split())
            self.results.append(
                {
                    "title": title or "Untitled",
                    "url": url,
                    "description": description,
                }
            )
        self._current = None


class DuckDuckGoSearchProvider:
    provider_id = DUCKDUCKGO_SEARCH_PROVIDER_ID
    display_name = DUCKDUCKGO_SEARCH_DISPLAY_NAME

    def __init__(self, config: DuckDuckGoSearchProviderConfig | None = None) -> None:
        self.config = config or DuckDuckGoSearchProviderConfig()

    def _api_url(self) -> str:
        return self.config.endpoint.strip() or DEFAULT_DUCKDUCKGO_SEARCH_URL

    def _timeout_seconds(self) -> float:
        if self.config.timeout_s > 0:
            return self.config.timeout_s
        return DEFAULT_DUCKDUCKGO_SEARCH_TIMEOUT_SECONDS

    def healthcheck(self, ctx: Any | None = None) -> bool:
        del ctx
        return True

    def _request(self, *, query: str) -> str:
        request = urllib_request.Request(
            self._api_url(),
            data=urllib_parse.urlencode({"q": query}).encode("utf-8"),
            headers={
                "Accept": "text/html",
                "Content-Type": "application/x-www-form-urlencoded",
                "User-Agent": "OpenMinionSearch/1.0",
            },
            method="POST",
        )
        try:
            with urllib_request.urlopen(
                request, timeout=self._timeout_seconds()
            ) as response:
                return str(response.read().decode("utf-8", errors="replace"))
        except urllib_error.HTTPError as exc:
            status = int(exc.code)
            raise SearchProviderError(
                f"DuckDuckGo request failed with status {status}",
                code=_error_code_for_status(status),
                details={"status": status},
            ) from exc
        except urllib_error.URLError as exc:
            raise SearchProviderError(
                "DuckDuckGo request failed",
                code="UPSTREAM_ERROR",
                details={"reason": str(getattr(exc, "reason", exc))},
            ) from exc

    def search(
        self,
        query: str,
        *,
        max_results: int,
        args: Mapping[str, Any],
        ctx: Any,
    ) -> Mapping[str, Any]:
        del args, ctx
        query_text = query.strip()
        if not query_text:
            raise SearchProviderError("query is required", code="INVALID_REQUEST")

        parser = _DuckDuckGoHTMLParser()
        parser.feed(self._request(query=query_text))
        parsed_results = parser.finish()
        results = [
            {"rank": rank, **result}
            for rank, result in enumerate(parsed_results[:max_results], start=1)
        ]
        return {
            "provider": self.provider_id,
            "query": {
                "original": query_text,
                "more_results_available": len(parsed_results) > len(results),
            },
            "results": results,
            "warnings": [],
        }


__all__ = ["DuckDuckGoSearchProvider", "DuckDuckGoSearchProviderConfig"]
