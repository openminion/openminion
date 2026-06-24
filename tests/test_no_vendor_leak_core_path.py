from __future__ import annotations

from pathlib import Path


_OPENMINION_ROOT = Path(__file__).resolve().parents[1] / "src" / "openminion"


def _read(path: str) -> str:
    return Path(path).read_text(encoding="utf-8")


def test_llm_bridge_does_not_parse_vendor_raw_payload() -> None:
    src = _read(str(_OPENMINION_ROOT / "modules" / "llm" / "providers" / "bridge.py"))
    assert "provider_raw.get(" not in src
    assert '.get("done_reason"' not in src
    assert 'finish_reason = str(getattr(response, "finish_reason", "") or "")' in src


def test_agent_service_normalizes_provider_response_before_core_loop_logic() -> None:
    src = _read(str(_OPENMINION_ROOT / "services" / "agent" / "service.py"))
    assert "normalize_provider_response(" in src
    assert (
        "raw_provider_response = await self._invoke_provider_request(provider_request)"
        in src
    )
    raw_assign = (
        "raw_provider_response = await self._invoke_provider_request(provider_request)"
    )
    raw_assign_idx = src.index(raw_assign)
    after = src[raw_assign_idx + len(raw_assign) :]
    assert "normalize_provider_response(" in after, (
        "normalize_provider_response must be called after raw provider fetch"
    )
