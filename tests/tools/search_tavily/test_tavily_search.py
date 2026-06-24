from __future__ import annotations

from unittest.mock import patch, MagicMock
from openminion.tools.search.providers.tavily.search import (
    TavilySearchTool,
    _normalize_results,
    _normalize_search_depth,
    _format_web_search_content,
    _verify_web_search_payload,
    _coerce_int,
    _coerce_bool,
)


class TestTavilySearchTool:
    def test_missing_query_returns_error(self):
        tool = TavilySearchTool()
        result = tool.execute({}, None)
        assert result["ok"] is False
        assert "query" in result["error"].lower()

    @patch.dict("os.environ", {"TAVILY_API_KEY": ""})
    def test_missing_api_key_returns_error(self):
        tool = TavilySearchTool()
        result = tool.execute({"query": "test"}, None)
        assert result["ok"] is False
        assert "API key" in result["error"]

    @patch.dict("os.environ", {"TAVILY_API_KEY": "test-key"})
    @patch(
        "openminion.tools.search.providers.tavily.search._SEARCH_RETRY_BACKOFF",
        (0, 0),
    )
    @patch("openminion.tools.search.providers.tavily.search._MAX_SEARCH_RETRIES", 1)
    @patch("urllib.request.urlopen")
    def test_successful_search(self, mock_urlopen):
        mock_response = MagicMock()
        mock_response.read.return_value = b'{"results": [{"url": "https://example.com", "title": "Test", "content": "Test content"}], "answer": "Test answer"}'
        mock_urlopen.return_value.__enter__.return_value = mock_response

        tool = TavilySearchTool()
        result = tool.execute({"query": "test query"}, None)

        assert result["ok"] is True
        assert result["source"] == "tavily"
        assert result["data"]["query"] == "test query"

    @patch.dict("os.environ", {"TAVILY_API_KEY": "test-key"})
    @patch(
        "openminion.tools.search.providers.tavily.search._SEARCH_RETRY_BACKOFF",
        (0, 0),
    )
    @patch("openminion.tools.search.providers.tavily.search._MAX_SEARCH_RETRIES", 1)
    @patch("urllib.request.urlopen")
    def test_empty_results_returns_error(self, mock_urlopen):
        mock_response = MagicMock()
        mock_response.read.return_value = b'{"results": [], "answer": ""}'
        mock_urlopen.return_value.__enter__.return_value = mock_response

        tool = TavilySearchTool()
        result = tool.execute({"query": "test"}, None)

        assert result["ok"] is False
        assert "empty" in result["error"].lower()


class TestHelpers:
    def test_normalize_results_valid(self):
        raw = [
            {
                "url": "https://example.com",
                "title": "Test",
                "content": "Content here",
                "score": 0.9,
            }
        ]
        result = _normalize_results(raw)
        assert len(result) == 1
        assert result[0]["url"] == "https://example.com"
        assert result[0]["score"] == 0.9

    def test_normalize_results_empty(self):
        assert _normalize_results([]) == []
        assert _normalize_results(None) == []

    def test_normalize_search_depth(self):
        assert _normalize_search_depth("advanced") == "advanced"
        assert _normalize_search_depth("basic") == "basic"
        assert _normalize_search_depth("invalid") == "basic"
        assert _normalize_search_depth(None) == "basic"

    def test_coerce_int(self):
        assert _coerce_int("5", default_value=3, minimum=1, maximum=10) == 5
        assert _coerce_int("invalid", default_value=3, minimum=1, maximum=10) == 3
        assert _coerce_int("100", default_value=3, minimum=1, maximum=10) == 10

    def test_coerce_bool(self):
        assert _coerce_bool(True, default_value=False) is True
        assert _coerce_bool("true", default_value=False) is True
        assert _coerce_bool("yes", default_value=False) is True
        assert _coerce_bool("false", default_value=True) is False

    def test_format_web_search_content(self):
        payload = {
            "query": "test",
            "answer": "Test answer",
            "results": [
                {
                    "title": "Result 1",
                    "url": "https://example.com",
                    "snippet": "Snippet",
                }
            ],
        }
        content = _format_web_search_content(payload)
        assert "test" in content.lower()
        assert "tavily" in content

    def test_verify_web_search_payload_valid(self):
        payload = {"query": "test", "results": [{"url": "https://example.com"}]}
        assert _verify_web_search_payload(payload) is True

    def test_verify_web_search_payload_invalid(self):
        payload = {"query": "", "results": []}
        assert _verify_web_search_payload(payload) is False
