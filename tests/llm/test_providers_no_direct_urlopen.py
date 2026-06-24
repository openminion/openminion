from __future__ import annotations

from pathlib import Path

import pytest

_PROVIDERS_ROOT = (
    Path(__file__).resolve().parents[2]
    / "src"
    / "openminion"
    / "modules"
    / "llm"
    / "providers"
)

_ADAPTER_FILES = [
    _PROVIDERS_ROOT / "openai" / "adapter.py",
    _PROVIDERS_ROOT / "openrouter" / "adapter.py",
    _PROVIDERS_ROOT / "anthropic" / "adapter.py",
    _PROVIDERS_ROOT / "ollama" / "adapter.py",
    _PROVIDERS_ROOT / "groq" / "adapter.py",
    _PROVIDERS_ROOT / "cerebras" / "adapter.py",
    _PROVIDERS_ROOT / "cortensor" / "adapter.py",
]

_FORBIDDEN_TOKENS = ("urlopen(", "urllib_request")


@pytest.mark.parametrize("adapter_path", _ADAPTER_FILES, ids=lambda p: p.parent.name)
def test_adapter_does_not_use_direct_urlopen(adapter_path: Path) -> None:
    assert adapter_path.exists(), f"missing adapter file: {adapter_path}"
    source = adapter_path.read_text(encoding="utf-8")
    for token in _FORBIDDEN_TOKENS:
        assert token not in source, (
            f"{adapter_path.relative_to(_PROVIDERS_ROOT)} must not reference "
            f"{token!r} directly; route HTTP through transport.http_json_post / "
            f"http_json_get instead."
        )


def test_provider_adapters_aggregator_remains_excluded() -> None:
    aggregator = _PROVIDERS_ROOT / "adapters.py"
    source = aggregator.read_text(encoding="utf-8")
    assert "urllib_request" in source, (
        "adapters.py must continue to re-export urllib_request "
        "until the test-seam migration plan retires it."
    )


def test_transport_http_remains_canonical_owner() -> None:
    transport_http = _PROVIDERS_ROOT / "transport" / "http.py"
    source = transport_http.read_text(encoding="utf-8")
    assert "urllib_request.urlopen" in source, (
        "transport/http.py is the canonical urlopen owner; it must keep the "
        "real call site."
    )
