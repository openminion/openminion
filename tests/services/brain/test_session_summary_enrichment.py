from __future__ import annotations

from openminion.services.brain import service as brain_service


def test_session_summary_enricher_returns_structured_summary(monkeypatch) -> None:
    service = object.__new__(brain_service.BrainBridgeService)
    monkeypatch.setattr(
        brain_service,
        "_structure_session_summary",
        lambda **_kwargs: {"summary_text": "short enriched summary"},
    )

    enricher = service.build_session_summary_enricher()

    assert enricher("deterministic summary") == "short enriched summary"


def test_session_summary_enricher_keeps_summary_without_provider_result(
    monkeypatch,
) -> None:
    service = object.__new__(brain_service.BrainBridgeService)
    monkeypatch.setattr(
        brain_service,
        "_structure_session_summary",
        lambda **_kwargs: None,
    )

    enricher = service.build_session_summary_enricher()

    assert enricher("deterministic summary") == "deterministic summary"
