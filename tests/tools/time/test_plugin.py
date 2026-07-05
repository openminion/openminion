from __future__ import annotations

from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

from openminion.modules.tool.errors import ToolRuntimeError
from openminion.modules.tool.runtime.policy import Policy
from openminion.modules.tool.registry import ToolRegistry
from openminion.modules.tool.runtime import RuntimeContext
from openminion.tools.time.plugin import (
    _h_convert,
    _h_diff,
    _h_end_of_day,
    _h_in_zone,
    _h_next_cron,
    _h_now,
    _h_parse_iso,
    _h_start_of_day,
    _resolve_timezone,
    _timezone_from_explicit_location,
    register,
)


def _ctx(
    tmp_path: Path, *, context_metadata: dict[str, str] | None = None
) -> RuntimeContext:
    run_root = tmp_path / "run"
    run_root.mkdir(parents=True, exist_ok=True)
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    policy = Policy(
        raw={
            "workspace_root": str(workspace),
            "context_metadata": dict(context_metadata or {}),
            "paths": {
                "read_allow": [str(workspace)],
                "write_allow": [str(workspace)],
                "deny": [],
            },
            "commands": {"mode": "allowlist", "allow": ["echo"]},
            "tools": {"allow_prefix": [""]},
        }
    )
    return RuntimeContext(
        policy=policy,
        workspace=workspace,
        run_root=run_root,
        scope="READ_ONLY",
        confirm=False,
    )


def _parse_iso(value: str) -> datetime:
    return datetime.fromisoformat(str(value).replace("Z", "+00:00"))


def test_register_adds_time_tools() -> None:
    registry = ToolRegistry()
    register(registry)
    names = set(registry.list().keys())
    assert "time.now" in names
    assert "time.convert" in names
    assert "time.next_cron" in names


def test_now_returns_utc_and_local_with_offset(tmp_path: Path) -> None:
    payload = _h_now({"timezone": "America/Los_Angeles"}, _ctx(tmp_path))
    assert payload["timezone"] == "America/Los_Angeles"
    assert payload["utc"].endswith("Z")
    assert "T" in payload["local"]
    assert isinstance(payload["offset_seconds"], int)
    assert isinstance(payload["unix_seconds"], int)
    assert isinstance(payload["unix_millis"], int)


def test_now_uses_context_metadata_timezone_when_available(tmp_path: Path) -> None:
    payload = _h_now({}, _ctx(tmp_path, context_metadata={"timezone": "Asia/Tokyo"}))
    assert payload["timezone"] == "Asia/Tokyo"


def test_now_prefers_identity_timezone_over_context_metadata(
    monkeypatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(
        "openminion.tools.time.plugin._timezone_from_identity_profile",
        lambda _ctx: "Europe/Paris",
    )
    ctx = _ctx(tmp_path, context_metadata={"timezone": "Asia/Tokyo"})
    payload = _h_now({}, ctx)
    assert payload["timezone"] == "Europe/Paris"
    assert ctx.logs
    assert ctx.logs[-1].meta.get("defaulted_from_identity") is True


def test_now_explicit_timezone_overrides_identity_default(
    monkeypatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(
        "openminion.tools.time.plugin._timezone_from_identity_profile",
        lambda _ctx: "Europe/Paris",
    )
    ctx = _ctx(tmp_path)
    payload = _h_now({"timezone": "America/Los_Angeles"}, ctx)
    assert payload["timezone"] == "America/Los_Angeles"
    assert ctx.logs
    assert ctx.logs[-1].meta.get("defaulted_from_identity") is False


def test_now_explicit_timezone_does_not_use_location_fallback(
    monkeypatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(
        "openminion.tools.time.plugin._timezone_from_location_fallback",
        lambda _ctx: (_ for _ in ()).throw(
            AssertionError("location fallback should not run")
        ),
    )
    payload = _h_now({"timezone": "America/Los_Angeles"}, _ctx(tmp_path))
    assert payload["timezone"] == "America/Los_Angeles"


def test_now_resolves_explicit_location_when_timezone_missing(
    monkeypatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(
        "openminion.tools.time.plugin._timezone_from_explicit_location",
        lambda location, _ctx: "Asia/Tokyo" if location == "Tokyo" else "UTC",
    )

    payload = _h_now({"location": "Tokyo"}, _ctx(tmp_path))

    assert payload["timezone"] == "Asia/Tokyo"


def test_resolve_timezone_prefers_explicit_timezone_over_location(
    monkeypatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(
        "openminion.tools.time.plugin._timezone_from_explicit_location",
        lambda _location, _ctx: (_ for _ in ()).throw(
            AssertionError("explicit location should not be consulted")
        ),
    )

    timezone_name, defaulted = _resolve_timezone(
        explicit_timezone="America/Los_Angeles",
        explicit_location="Tokyo",
        ctx=_ctx(tmp_path),
    )

    assert timezone_name == "America/Los_Angeles"
    assert defaulted is False


def test_now_invalid_explicit_location_fails_closed_without_fallback(
    monkeypatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(
        "openminion.tools.time.plugin._timezone_from_explicit_location",
        lambda _location, _ctx: (_ for _ in ()).throw(
            ToolRuntimeError(
                "NOT_FOUND",
                "location not found: xyzzy",
                {"location": "xyzzy"},
            )
        ),
    )
    monkeypatch.setattr(
        "openminion.tools.time.plugin._timezone_from_identity_profile",
        lambda _ctx: (_ for _ in ()).throw(
            AssertionError("identity fallback should not run")
        ),
    )
    monkeypatch.setattr(
        "openminion.tools.time.plugin._timezone_from_context_metadata",
        lambda _ctx: (_ for _ in ()).throw(
            AssertionError("context metadata fallback should not run")
        ),
    )
    monkeypatch.setattr(
        "openminion.tools.time.plugin._timezone_from_location_fallback",
        lambda _ctx: (_ for _ in ()).throw(
            AssertionError("location fallback should not run")
        ),
    )

    with pytest.raises(ToolRuntimeError) as exc:
        _h_now({"location": "xyzzy"}, _ctx(tmp_path))

    assert exc.value.code == "NOT_FOUND"


def test_invalid_timezone_returns_invalid_timezone_error(tmp_path: Path) -> None:
    with pytest.raises(ToolRuntimeError) as exc:
        _h_in_zone({"timezone": "Mars/OlympusMons"}, _ctx(tmp_path))
    assert exc.value.code == "INVALID_TIMEZONE"


def test_parse_iso_normalizes_z_and_offset_to_same_utc(tmp_path: Path) -> None:
    first = _h_parse_iso({"iso": "2026-03-11T07:23:45Z"}, _ctx(tmp_path))
    second = _h_parse_iso({"iso": "2026-03-11T00:23:45-07:00"}, _ctx(tmp_path))
    assert first["instant"]["utc"] == second["instant"]["utc"]


def test_parse_iso_uses_timezone_hint_for_offset_less_value(tmp_path: Path) -> None:
    payload = _h_parse_iso(
        {
            "iso": "2026-03-11T07:23:45",
            "timezone_hint": "America/Los_Angeles",
        },
        _ctx(tmp_path),
    )
    assert payload["assumed_timezone"] is True
    assert payload["instant"]["timezone"] == "America/Los_Angeles"


def test_parse_iso_rejects_date_only_input(tmp_path: Path) -> None:
    with pytest.raises(ToolRuntimeError) as exc:
        _h_parse_iso({"iso": "2026-03-11"}, _ctx(tmp_path))
    assert exc.value.code == "INVALID_ISO8601"


def test_convert_preserves_utc_and_changes_timezone(tmp_path: Path) -> None:
    converted = _h_convert(
        {
            "iso": "2026-03-11T07:23:45Z",
            "to_timezone": "America/Los_Angeles",
        },
        _ctx(tmp_path),
    )
    assert converted["utc"] == "2026-03-11T07:23:45Z"
    assert converted["timezone"] == "America/Los_Angeles"
    assert converted["local"].endswith("-07:00") or converted["local"].endswith(
        "-08:00"
    )


def test_diff_returns_expected_seconds(tmp_path: Path) -> None:
    payload = _h_diff(
        {
            "a": "2026-03-11T07:00:00Z",
            "b": "2026-03-11T08:00:00Z",
            "unit": "seconds",
            "abs": True,
        },
        _ctx(tmp_path),
    )
    assert payload["seconds"] == 3600
    assert payload["value"] == 3600
    assert payload["unit"] == "seconds"


def test_day_boundaries_handle_dst_transition(tmp_path: Path) -> None:
    args = {
        "iso": "2026-03-08T12:00:00-07:00",
        "timezone": "America/Los_Angeles",
    }
    start_payload = _h_start_of_day(args, _ctx(tmp_path))
    end_payload = _h_end_of_day(args, _ctx(tmp_path))
    start = start_payload["start"]
    end = end_payload["end"]
    assert start["timezone"] == "America/Los_Angeles"
    assert end["timezone"] == "America/Los_Angeles"
    assert start["local"].endswith("-08:00")
    assert end["local"].endswith("-07:00")
    assert _parse_iso(start["utc"]) < _parse_iso(end["utc"])


def test_next_cron_returns_monotonic_future_instants(tmp_path: Path) -> None:
    payload = _h_next_cron(
        {
            "cron": "0 9 * * 1-5",
            "timezone": "America/Los_Angeles",
            "from_iso": "2026-03-11T07:00:00Z",
            "count": 3,
        },
        _ctx(tmp_path),
    )
    next_items = payload["next"]
    assert len(next_items) == 3
    utc_values = [_parse_iso(item["utc"]) for item in next_items]
    assert utc_values == sorted(utc_values)
    assert all(item > _parse_iso("2026-03-11T07:00:00Z") for item in utc_values)


def test_resolve_timezone_uses_metadata_when_identity_missing(tmp_path: Path) -> None:
    timezone_name, defaulted = _resolve_timezone(
        explicit_timezone=None,
        explicit_location=None,
        ctx=_ctx(tmp_path, context_metadata={"timezone": "Asia/Tokyo"}),
    )
    assert timezone_name == "Asia/Tokyo"
    assert defaulted is False


def test_resolve_timezone_uses_location_fallback_when_identity_and_metadata_missing(
    monkeypatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(
        "openminion.tools.time.plugin._timezone_from_identity_profile",
        lambda _ctx: None,
    )
    monkeypatch.setattr(
        "openminion.tools.time.plugin._timezone_from_context_metadata",
        lambda _ctx: None,
    )
    monkeypatch.setattr(
        "openminion.tools.time.plugin._timezone_from_location_fallback",
        lambda _ctx: "America/New_York",
    )

    timezone_name, defaulted = _resolve_timezone(
        explicit_timezone=None, explicit_location=None, ctx=_ctx(tmp_path)
    )
    assert timezone_name == "America/New_York"
    assert defaulted is False


def test_timezone_from_explicit_location_uses_geocode_timezone(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(
        "openminion.tools.weather.providers.openmeteo.plugin.resolve_openmeteo_config",
        lambda _ctx: SimpleNamespace(timeout_seconds=5.0, default_language="en"),
    )
    monkeypatch.setattr(
        "openminion.tools.weather.providers.openmeteo.plugin.geocode_openmeteo_location",
        lambda query, *, config, language, timeout_s: (
            {
                "resolved_name": query,
                "country": "Japan",
                "latitude": 35.6895,
                "longitude": 139.69171,
            },
            "https://geocode.example",
            {
                "results": [
                    {
                        "name": query,
                        "country": "Japan",
                        "latitude": 35.6895,
                        "longitude": 139.69171,
                        "timezone": "Asia/Tokyo",
                    }
                ]
            },
        ),
    )
    monkeypatch.setattr(
        "openminion.tools.weather.providers.openmeteo.plugin.secondary_geocode_openmeteo_location",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("secondary geocode should not run")
        ),
    )
    monkeypatch.setattr(
        "openminion.tools.weather.providers.openmeteo.plugin.forecast_openmeteo_current",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("forecast lookup should not run")
        ),
    )

    timezone_name = _timezone_from_explicit_location("Tokyo", _ctx(tmp_path))

    assert timezone_name == "Asia/Tokyo"


def test_timezone_from_explicit_location_secondary_geocode_uses_forecast_timezone(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(
        "openminion.tools.weather.providers.openmeteo.plugin.resolve_openmeteo_config",
        lambda _ctx: SimpleNamespace(
            timeout_seconds=5.0,
            default_language="en",
            model_copy=lambda *, update: SimpleNamespace(
                timeout_seconds=5.0,
                default_language="en",
                timezone=update.get("timezone"),
            ),
        ),
    )
    monkeypatch.setattr(
        "openminion.tools.weather.providers.openmeteo.plugin.geocode_openmeteo_location",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            ToolRuntimeError(
                "NOT_FOUND", "Location not found: Tokyo", {"query": "Tokyo"}
            )
        ),
    )
    monkeypatch.setattr(
        "openminion.tools.weather.providers.openmeteo.plugin.secondary_geocode_openmeteo_location",
        lambda query, *, config, language, timeout_s: (
            {
                "resolved_name": query,
                "country": "Japan",
                "latitude": 35.6895,
                "longitude": 139.69171,
            },
            "https://secondary.example",
            [{"display_name": query}],
        ),
    )
    monkeypatch.setattr(
        "openminion.tools.weather.providers.openmeteo.plugin.forecast_openmeteo_current",
        lambda *, latitude, longitude, config, timeout_s: (
            {"time": "2026-04-09T12:00", "temperature_2m": 17.0},
            "https://forecast.example",
            {
                "timezone": "Asia/Tokyo",
                "current": {
                    "time": "2026-04-09T12:00",
                    "temperature_2m": 17.0,
                },
            },
        ),
    )

    timezone_name = _timezone_from_explicit_location("Tokyo", _ctx(tmp_path))

    assert timezone_name == "Asia/Tokyo"


def test_timezone_from_explicit_location_not_found_fails_closed(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(
        "openminion.tools.weather.providers.openmeteo.plugin.resolve_openmeteo_config",
        lambda _ctx: SimpleNamespace(timeout_seconds=5.0, default_language="en"),
    )
    monkeypatch.setattr(
        "openminion.tools.weather.providers.openmeteo.plugin.geocode_openmeteo_location",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            ToolRuntimeError(
                "NOT_FOUND",
                "Location not found: xyzzy",
                {"query": "xyzzy"},
            )
        ),
    )
    monkeypatch.setattr(
        "openminion.tools.weather.providers.openmeteo.plugin.secondary_geocode_openmeteo_location",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            ToolRuntimeError(
                "NOT_FOUND",
                "Location not found: xyzzy",
                {"query": "xyzzy"},
            )
        ),
    )

    with pytest.raises(ToolRuntimeError) as exc:
        _timezone_from_explicit_location("xyzzy", _ctx(tmp_path))

    assert exc.value.code == "NOT_FOUND"


def test_now_dependency_error_for_unconfigured_identity_storage(
    monkeypatch, tmp_path: Path
) -> None:
    ctx = _ctx(tmp_path)
    ctx.repositories.identity_path = None
    monkeypatch.setattr(
        "openminion.tools.time.plugin.resolve_identity_repository", lambda _ctx: None
    )

    with pytest.raises(ToolRuntimeError) as exc:
        _h_now({}, ctx)
    assert exc.value.code == "DEPENDENCY_MISSING"
    assert exc.value.details.get("reason_code") == "storage_unconfigured"


def test_now_dependency_error_for_unavailable_identity_storage(
    monkeypatch, tmp_path: Path
) -> None:
    ctx = _ctx(tmp_path)
    ctx.repositories.identity_path = tmp_path / "identity.db"
    monkeypatch.setattr(
        "openminion.tools.time.plugin.resolve_identity_repository", lambda _ctx: None
    )

    with pytest.raises(ToolRuntimeError) as exc:
        _h_now({}, ctx)
    assert exc.value.code == "DEPENDENCY_MISSING"
    assert exc.value.details.get("reason_code") == "storage_unavailable"


def test_now_dependency_error_for_unexpected_identity_failure(
    monkeypatch, tmp_path: Path
) -> None:
    ctx = _ctx(tmp_path)

    class _BrokenRepo:
        def get_profile(self, _agent_id: str):
            raise RuntimeError("boom")

    monkeypatch.setattr(
        "openminion.tools.time.plugin.resolve_identity_repository",
        lambda _ctx: _BrokenRepo(),
    )

    with pytest.raises(ToolRuntimeError) as exc:
        _h_now({}, ctx)
    assert exc.value.code == "EXEC_ERROR"
    assert exc.value.details.get("reason_code") == "storage_exec_error"
