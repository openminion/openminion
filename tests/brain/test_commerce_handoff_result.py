from __future__ import annotations

import time
from types import SimpleNamespace

import pytest

from openminion.modules.brain.adapters.tool.results import run_tool_spec
from openminion.tools.commerce.plugin import _result
from openminion.tools.commerce.provider import build_commerce_handoff


def _run_handoff_result(monkeypatch, *, tool_name: str = "commerce.place_order"):
    monkeypatch.setattr(
        "openminion.modules.brain.adapters.tool.results.emit_tool_execution_event",
        lambda **_kwargs: None,
    )
    handoff = build_commerce_handoff(
        reason_code="authentication_required",
        configured_base_url="https://shop.example/account/verify",
        candidate_url="https://shop.example/account/verify",
    )
    spec = SimpleNamespace(
        name=tool_name,
        handler=lambda _args, _context: _result(
            "place_order", "handoff_required", handoff.model_dump(mode="json")
        ),
    )
    return run_tool_spec(
        spec=spec,
        validated_args={},
        context=SimpleNamespace(tool_call_id="call-1", artifacts=[]),
        start_time=time.monotonic(),
        background_write_authorized=False,
        tool_name=tool_name,
    )


@pytest.mark.parametrize("tool_name", ["commerce.place_order", "browser.open"])
def test_explicit_plugin_handoff_maps_to_needs_user(monkeypatch, tool_name) -> None:
    result = _run_handoff_result(monkeypatch, tool_name=tool_name)

    assert result["status"] == "needs_user"
    assert (
        "Open the configured merchant surface to authenticate, then inspect and prepare again."
    ) in result["summary"]
    assert result["outputs"]["data"]["commerce_code"] == "HANDOFF_REQUIRED"
    assert result["outputs"]["requires_user_takeover"] is True
    assert result["content"] == result["outputs"]["content"]
    assert "error" not in result
