from __future__ import annotations

from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]


def _read(path: str) -> str:
    return (_ROOT / path).read_text(encoding="utf-8")


def test_llm_client_core_path_avoids_vendor_specific_payload_parsing() -> None:
    src = _read("src/openminion/modules/llm/runtime/client.py")
    assert "provider_raw.get(" not in src
    assert '["choices"]' not in src
    assert '.get("done_reason"' not in src


def test_llm_orchestrator_core_path_uses_canonical_fields() -> None:
    src = _read("src/openminion/modules/llm/orchestration/service.py")
    assert "provider.complete(" not in src
    assert "coerce_provider_output" not in src
    assert "client.call(" in src or "client.call_sync(" in src
    assert "response.output_text" in src
    assert "response.error" in src
    assert "response.usage" in src
