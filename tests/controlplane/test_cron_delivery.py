from __future__ import annotations

import pytest

from openminion.modules.controlplane.runtime.cron_delivery import deliver_cron_result


def test_announce_delivery_sends_outbound_payload() -> None:
    outbound: list[dict] = []
    outcome = deliver_cron_result(
        "announce",
        "cli:ops",
        {"job_id": "job-1", "delivery": {"mode": "announce"}},
        {"run_id": "run-1"},
        {"summary": "done"},
        outbound=outbound.append,
    )
    assert outcome["ok"] is True
    assert outbound
    assert outbound[0]["target"] == "cli:ops"
    assert outbound[0]["summary"] == "done"


def test_webhook_delivery_posts_json_with_bearer_token() -> None:
    seen: dict = {}

    def _post(url: str, payload: dict, headers: dict[str, str]) -> int:
        seen["url"] = url
        seen["payload"] = payload
        seen["headers"] = headers
        return 202

    outcome = deliver_cron_result(
        "webhook",
        "https://hooks.example.com/cron",
        {"job_id": "job-2", "delivery": {"mode": "webhook"}},
        {"run_id": "run-2"},
        {"summary": "completed"},
        webhook_token="secret-token",
        http_post=_post,
    )
    assert outcome["ok"] is True
    assert outcome["status_code"] == 202
    assert seen["headers"]["Authorization"] == "Bearer secret-token"
    assert seen["payload"]["event"] == "cron.run.finished"


def test_best_effort_missing_target_does_not_raise() -> None:
    outcome = deliver_cron_result(
        "announce",
        "",
        {"job_id": "job-3", "delivery": {"mode": "announce", "best_effort": True}},
        {"run_id": "run-3"},
        {"summary": "noop"},
        outbound=lambda payload: payload,
    )
    assert outcome["ok"] is False
    assert outcome["best_effort"] is True


def test_missing_target_without_best_effort_raises() -> None:
    with pytest.raises(ValueError):
        deliver_cron_result(
            "announce",
            "",
            {"job_id": "job-4", "delivery": {"mode": "announce", "best_effort": False}},
            {"run_id": "run-4"},
            {"summary": "noop"},
            outbound=lambda payload: payload,
        )
