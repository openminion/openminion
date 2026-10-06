from __future__ import annotations

import time
from types import SimpleNamespace

from openminion.modules.brain.adapters.tool.results import run_tool_spec
from openminion.modules.commerce.provider import build_commerce_handoff


def _run_handoff_result(monkeypatch, *, tool_name: str = "commerce.place_order"):
    monkeypatch.setattr(
        "openminion.modules.brain.adapters.tool.results.emit_tool_execution_event",
        lambda **_kwargs: None,
    )
    handoff = build_commerce_handoff(
        reason_code="authentication_required",
        message="Sign in with the merchant, then inspect and prepare again.",
        configured_base_url="https://shop.example/account",
        candidate_url="https://shop.example/account/verify",
    )
    spec = SimpleNamespace(
        name=tool_name,
        handler=lambda _args, _context: handoff.model_dump(mode="json"),
    )
    return run_tool_spec(
        spec=spec,
        validated_args={},
        context=SimpleNamespace(tool_call_id="call-1", artifacts=[]),
        start_time=time.monotonic(),
        background_write_authorized=False,
        tool_name=tool_name,
    )


def test_typed_commerce_handoff_maps_to_needs_user(monkeypatch) -> None:
    result = _run_handoff_result(monkeypatch)

    assert result["status"] == "needs_user"
    assert result["summary"] == (
        "Sign in with the merchant, then inspect and prepare again."
    )
    assert result["outputs"]["commerce_code"] == "HANDOFF_REQUIRED"
    assert "error" not in result


def test_handoff_marker_does_not_change_non_commerce_tools(monkeypatch) -> None:
    result = _run_handoff_result(monkeypatch, tool_name="browser.open")

    assert result["status"] == "success"
