from __future__ import annotations

from openminion.modules.context.pack.semantics import resolve_context_total_token_budget


def test_resolve_budget_prefers_min_of_runtime_and_requested() -> None:
    cap = resolve_context_total_token_budget(
        purpose="decide",
        runtime_token_budget=1200,
        requested_token_budget=3000,
    )
    assert cap == 1200


def test_resolve_budget_uses_requested_when_runtime_absent() -> None:
    cap = resolve_context_total_token_budget(
        purpose="decide",
        runtime_token_budget=0,
        requested_token_budget=900,
    )
    assert cap == 900


def test_resolve_budget_uses_runtime_when_requested_absent() -> None:
    cap = resolve_context_total_token_budget(
        purpose="act",
        runtime_token_budget=1400,
        requested_token_budget=None,
    )
    assert cap == 1400


def test_resolve_budget_falls_back_to_purpose_default() -> None:
    cap = resolve_context_total_token_budget(
        purpose="chat",
        runtime_token_budget=None,
        requested_token_budget=None,
    )
    assert cap == 1600
