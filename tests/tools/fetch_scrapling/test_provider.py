from __future__ import annotations

import importlib
from types import SimpleNamespace

from openminion.tools.fetch.providers.scrapling.provider import provider


def test_provider_identity_and_capabilities() -> None:
    assert provider.name == "scrapling"
    assert "none" in provider.capabilities["render"]
    assert "dom" in provider.capabilities["render"]


def test_provider_fetch_static_mode_shape(monkeypatch) -> None:
    scrapling_provider_module = importlib.import_module(
        "openminion.tools.fetch.providers.scrapling.provider"
    )
    monkeypatch.setattr(
        scrapling_provider_module.core_http_provider,
        "fetch",
        lambda request, ctx=None: {
            "ok": True,
            "final_url": str(request.get("url", "")),
            "status_code": 200,
            "content_type": "text/html",
            "content_bytes": 10,
            "raw_body": b"<html></html>",
            "extracted_text": "ok",
            "warnings": [],
        },
    )
    result = provider.fetch(
        {
            "url": "https://example.com",
            "provider_options": {"scrapling": {"mode": "static"}},
        }
    )
    assert result["ok"] is True
    assert result["backend"] == "scrapling:static"


def test_dynamic_mode_requires_approval_by_default() -> None:
    result = provider.fetch(
        {
            "url": "https://example.com",
            "provider_options": {"scrapling": {"mode": "dynamic"}},
        }
    )
    assert result["ok"] is False
    assert result["error"]["code"] == "NEEDS_APPROVAL"


def test_stealth_mode_requires_approval_by_default() -> None:
    result = provider.fetch(
        {
            "url": "https://example.com",
            "provider_options": {"scrapling": {"mode": "stealth"}},
        }
    )
    assert result["ok"] is False
    assert result["error"]["code"] == "NEEDS_APPROVAL"


def test_geoip_requires_approval_by_default() -> None:
    result = provider.fetch(
        {
            "url": "https://example.com",
            "provider_options": {"scrapling": {"mode": "static", "geoip": True}},
        }
    )
    assert result["ok"] is False
    assert result["error"]["code"] == "NEEDS_APPROVAL"


def test_dynamic_mode_downgrades_with_warning_when_allowed(monkeypatch) -> None:
    scrapling_provider_module = importlib.import_module(
        "openminion.tools.fetch.providers.scrapling.provider"
    )
    monkeypatch.setattr(
        scrapling_provider_module.core_http_provider,
        "fetch",
        lambda request, ctx=None: {
            "ok": True,
            "final_url": str(request.get("url", "")),
            "status_code": 200,
            "content_type": "text/html",
            "content_bytes": 10,
            "raw_body": b"<html></html>",
            "extracted_text": "ok",
            "warnings": [],
        },
    )
    ctx = SimpleNamespace(
        policy=SimpleNamespace(
            raw={"tools": {"fetch_scrapling": {"allow_dynamic": True}}}
        )
    )
    result = provider.fetch(
        {
            "url": "https://example.com",
            "provider_options": {"scrapling": {"mode": "dynamic"}},
        },
        ctx,
    )
    assert result["ok"] is True
    assert result["backend"] == "scrapling:static"
    assert "DOWNGRADED_TO_STATIC" in result.get("warnings", [])
