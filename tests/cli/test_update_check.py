from __future__ import annotations

import json

from openminion.cli.bootstrap.update import (
    OPENMINION_NO_UPDATE_CHECK_ENV,
    OPENMINION_UPDATE_CHECK_ENV,
    check_update_available,
)


def test_update_check_reports_newer_version(tmp_path) -> None:
    result = check_update_available(
        current_version="0.0.1",
        cache_path=tmp_path / "update.json",
        fetcher=lambda package, timeout: "0.2.0",
        now=100,
    )

    assert result is not None
    assert result.update_available is True
    assert "0.0.1 -> 0.2.0" in result.render_notice()


def test_update_check_uses_fresh_cache(tmp_path) -> None:
    cache = tmp_path / "update.json"
    cache.write_text(
        json.dumps({"checked_at": 100, "latest_version": "0.3.0"}),
        encoding="utf-8",
    )
    calls = []

    result = check_update_available(
        current_version="0.0.1",
        cache_path=cache,
        fetcher=lambda package, timeout: calls.append(package) or "0.4.0",
        now=200,
    )

    assert result is not None
    assert result.latest_version == "0.3.0"
    assert calls == []


def test_update_check_disabled_by_env(tmp_path) -> None:
    result = check_update_available(
        current_version="0.0.1",
        cache_path=tmp_path / "update.json",
        env={OPENMINION_UPDATE_CHECK_ENV: "0"},
        fetcher=lambda package, timeout: "0.2.0",
    )

    assert result is None


def test_update_check_disabled_by_no_update_env(tmp_path) -> None:
    result = check_update_available(
        current_version="0.0.1",
        cache_path=tmp_path / "update.json",
        env={OPENMINION_NO_UPDATE_CHECK_ENV: "1"},
        fetcher=lambda package, timeout: "0.2.0",
    )

    assert result is None


def test_update_check_silent_on_fetch_failure(tmp_path) -> None:
    def _raise(package: str, timeout: float) -> str:
        raise OSError("network down")

    result = check_update_available(
        current_version="0.0.1",
        cache_path=tmp_path / "update.json",
        fetcher=_raise,
    )

    assert result is None


def test_update_check_ignores_invalid_cache_and_refetches(tmp_path) -> None:
    cache = tmp_path / "update.json"
    cache.write_text("{not-json", encoding="utf-8")
    calls: list[str] = []

    result = check_update_available(
        current_version="0.0.1",
        cache_path=cache,
        fetcher=lambda package, timeout: calls.append(package) or "0.2.0",
        now=100,
    )

    assert result is not None
    assert result.latest_version == "0.2.0"
    assert calls == ["openminion"]
