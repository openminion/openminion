from __future__ import annotations

from openminion.modules.brain.loop.tools.engine import (
    _repair_stale_exact_date_search_args,
    _stale_exact_date_query_reason,
)


def test_rejects_stale_explicit_year_for_exact_date_search() -> None:
    reason = _stale_exact_date_query_reason(
        user_input="check latest iran news and build a stock basket",
        require_exact_date=True,
        tool_name="web.search",
        tool_args={"query": "latest Iran news 2025"},
        current_year=2026,
    )

    assert reason is not None
    assert "2025" in reason
    assert "2026" in reason


def test_allows_exact_date_search_when_current_year_is_present() -> None:
    reason = _stale_exact_date_query_reason(
        user_input="check latest iran news and build a stock basket",
        require_exact_date=True,
        tool_name="web.search",
        tool_args={"query": "defense stocks Iran conflict 2025 2026"},
        current_year=2026,
    )

    assert reason is None


def test_rejects_stale_explicit_year_for_external_search_alias() -> None:
    reason = _stale_exact_date_query_reason(
        user_input="check latest iran news and build a stock basket",
        require_exact_date=True,
        tool_name="web_search",
        tool_args={"query": "latest Iran news 2025"},
        current_year=2026,
    )

    assert reason is not None
    assert "2025" in reason
    assert "2026" in reason


def test_allows_historical_year_when_user_requested_it_explicitly() -> None:
    reason = _stale_exact_date_query_reason(
        user_input="compare 2025 iran news with today and build a basket",
        require_exact_date=True,
        tool_name="web.search",
        tool_args={"query": "latest Iran news 2025"},
        current_year=2026,
    )

    assert reason is None


def test_ignores_non_search_tools_for_exact_date_guard() -> None:
    reason = _stale_exact_date_query_reason(
        user_input="check latest iran news",
        require_exact_date=True,
        tool_name="weather",
        tool_args={"location": "Tehran"},
        current_year=2026,
    )

    assert reason is None


def test_repairs_runtime_invented_stale_year_for_exact_date_search() -> None:
    repaired = _repair_stale_exact_date_search_args(
        user_input="check latest iran news and build a stock basket",
        require_exact_date=True,
        tool_name="web.search",
        tool_args={"query": "latest Iran news 2025"},
        current_year=2026,
    )

    assert repaired == {"query": "latest Iran news"}


def test_does_not_repair_when_user_requested_historical_year() -> None:
    repaired = _repair_stale_exact_date_search_args(
        user_input="compare 2025 iran news with today and build a basket",
        require_exact_date=True,
        tool_name="web.search",
        tool_args={"query": "latest Iran news 2025"},
        current_year=2026,
    )

    assert repaired is None
