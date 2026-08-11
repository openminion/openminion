from __future__ import annotations

from pathlib import Path
import re


DOC = Path(__file__).parents[2] / "docs" / "telemetry-export-quickstarts.md"


def test_quickstarts_cover_all_supported_backends_and_proof_levels() -> None:
    text = DOC.read_text(encoding="utf-8")
    for heading in (
        "## Generic OTLP or OpenTelemetry Collector",
        "## Jaeger",
        "## Grafana Tempo",
        "## Langfuse",
        "## Arize Phoenix",
        "## Pydantic Logfire",
    ):
        assert heading in text
    assert text.count("Checked 2026-08-11:") == 6
    assert "local probe row" in text
    assert "Collector receipt" in text
    assert "vendor proof" in text
    assert "content export disabled" in text


def test_quickstarts_contain_only_placeholders_and_portable_paths() -> None:
    text = DOC.read_text(encoding="utf-8")
    assert not re.search(r"(?i)(bearer|basic) [A-Za-z0-9_-]{12,}", text)
    assert not re.search(r"(?i)(api[_ -]?key|secret)[=:][^<\s]", text)
    assert not re.search(r"/(?:Users|home|private|var)/", text)
    assert "sk-lf-" not in text
    assert "pk-lf-" not in text
    for placeholder in (
        "<OTLP_HOST>",
        "<TENANT_ID>",
        "<PUBLIC_ID>",
        "<PRIVATE_SECRET>",
        "<PHOENIX_CREDENTIAL>",
        "<LOGFIRE_CREDENTIAL>",
    ):
        assert placeholder in text
