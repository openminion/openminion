from __future__ import annotations

from http import HTTPStatus

from openminion.api.server import dispatch_request


class _Runtime:
    runtime_manager = object()

    def __init__(self, snapshot: dict | None = None, *, fail: bool = False) -> None:
        self.snapshot = snapshot or {}
        self.fail = fail
        self.closed = False

    def runtime_self_model(self, *, agent_id: str | None = None) -> dict:
        if self.fail:
            raise RuntimeError("cannot compose self model")
        payload = dict(self.snapshot)
        if agent_id:
            payload["agent_id"] = agent_id
        else:
            payload.setdefault("agent_id", "mini")
        return payload

    def close(self) -> None:
        self.closed = True


def _snapshot(health: str) -> dict:
    return {
        "schema_version": "self_model.v1",
        "health": health,
        "agent_id": "mini",
        "identity": {"status": health, "facts": {}, "degraded_reasons": []},
        "capabilities": {"status": "ok", "facts": {}, "degraded_reasons": []},
        "policy": {"status": "ok", "facts": {}, "degraded_reasons": []},
        "memory_state": {"status": "ok", "facts": {}, "degraded_reasons": []},
        "context_state": {"status": "ok", "facts": {}, "degraded_reasons": []},
        "knowledge_state": {"status": "ok", "facts": {}, "degraded_reasons": []},
        "improvement_state": {"status": "ok", "facts": {}, "degraded_reasons": []},
        "degraded_reasons": [],
    }


def test_runtime_self_model_route_returns_ok_snapshot() -> None:
    status, payload = dispatch_request(
        "GET",
        "/v1/runtime/self-model",
        None,
        runtime=_Runtime(_snapshot("ok")),  # type: ignore[arg-type]
    )

    assert status == HTTPStatus.OK
    assert payload["ok"] is True
    assert payload["health"] == "ok"
    assert payload["self_model"]["agent_id"] == "mini"


def test_runtime_self_model_route_preserves_degraded_and_unavailable_health() -> None:
    for health in ("degraded", "unavailable"):
        status, payload = dispatch_request(
            "GET",
            "/v1/runtime/self-model",
            None,
            runtime=_Runtime(_snapshot(health)),  # type: ignore[arg-type]
        )

        assert status == HTTPStatus.OK
        assert payload["health"] == health
        assert payload["self_model"]["health"] == health


def test_runtime_self_model_route_uses_query_agent_id() -> None:
    status, payload = dispatch_request(
        "GET",
        "/v1/runtime/self-model",
        None,
        query="agent_id=writer",
        runtime=_Runtime(_snapshot("ok")),  # type: ignore[arg-type]
    )

    assert status == HTTPStatus.OK
    assert payload["self_model"]["agent_id"] == "writer"


def test_runtime_self_model_route_returns_503_when_no_typed_envelope() -> None:
    status, payload = dispatch_request(
        "GET",
        "/v1/runtime/self-model",
        None,
        runtime=_Runtime(fail=True),  # type: ignore[arg-type]
    )

    assert status == HTTPStatus.SERVICE_UNAVAILABLE
    assert payload["ok"] is False
    assert payload["error"]["code"] == "runtime_unavailable"
