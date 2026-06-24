from __future__ import annotations

from typing import Any

import pytest

from openminion.modules.controlplane.channels.telegram.bot_api import TelegramAPIError
from openminion.modules.controlplane.channels.telegram.config import (
    DeliveryConfig,
    ReplyConfig,
    RetryConfig,
)
from openminion.modules.controlplane.channels.telegram.delivery import (
    TelegramDeliveryService,
)
from openminion.modules.controlplane.channels.telegram.models import TelegramReplyTarget
from openminion.modules.controlplane.runtime.audit import AuditLogger


class _ScriptedAPI:
    def __init__(self, script: list[Any]) -> None:
        self._script = list(script)
        self.calls = 0
        self.payloads: list[dict[str, Any]] = []

    def send_message(self, payload: dict[str, Any]) -> dict[str, Any]:
        self.calls += 1
        self.payloads.append(payload)
        if not self._script:
            raise AssertionError("scripted api ran out of responses")
        step = self._script.pop(0)
        if isinstance(step, Exception):
            raise step
        return step


def _build_service(
    api: _ScriptedAPI,
    *,
    max_attempts: int = 2,
    sleep_sink: list[float] | None = None,
    audit_logger: AuditLogger | None = None,
) -> TelegramDeliveryService:
    def sleep_fn(seconds: float) -> None:
        if sleep_sink is not None:
            sleep_sink.append(seconds)

    return TelegramDeliveryService(
        api=api,  # type: ignore[arg-type]
        delivery_config=DeliveryConfig(
            parse_mode="plain",
            chunk_limit=500,
            retry=RetryConfig(max_attempts=max_attempts, backoff_ms=[1, 1]),
        ),
        reply_config=ReplyConfig(mode="reply_to_user"),
        sleep_fn=sleep_fn,
        audit_logger=audit_logger,
    )


def _failed_events(audit: AuditLogger) -> list[Any]:
    return [e for e in audit.events if e.event_type == "cp.delivery.failed"]


def _target() -> TelegramReplyTarget:
    return TelegramReplyTarget(chat_id=111, message_id=22, topic_id=None)


def test_send_text_raises_unauthorized_without_retry() -> None:
    err = TelegramAPIError(code=401, description="Unauthorized")
    api = _ScriptedAPI(script=[err])
    sleeps: list[float] = []
    audit = AuditLogger()
    svc = _build_service(api, max_attempts=3, sleep_sink=sleeps, audit_logger=audit)

    with pytest.raises(TelegramAPIError) as excinfo:
        svc.send_text(text="hi", target=_target())

    assert excinfo.value.code == 401
    assert excinfo.value.retryable is False
    assert api.calls == 1
    assert sleeps == []

    failures = _failed_events(audit)
    assert len(failures) == 1
    ev = failures[0]
    assert ev.outcome == "failed"
    assert ev.severity == "error"
    assert ev.details == {
        "code": 401,
        "description": "Unauthorized",
        "retryable": False,
        "attempts": 1,
        "chat_id": "111",
        "method": "send_message",
    }


def test_send_text_raises_forbidden_without_retry() -> None:
    err = TelegramAPIError(
        code=403, description="Forbidden: bot was blocked by the user"
    )
    api = _ScriptedAPI(script=[err])
    sleeps: list[float] = []
    audit = AuditLogger()
    svc = _build_service(api, max_attempts=3, sleep_sink=sleeps, audit_logger=audit)

    with pytest.raises(TelegramAPIError) as excinfo:
        svc.send_text(text="hi", target=_target())

    assert excinfo.value.code == 403
    assert excinfo.value.retryable is False
    assert api.calls == 1
    assert sleeps == []

    failures = _failed_events(audit)
    assert len(failures) == 1
    ev = failures[0]
    assert ev.details == {
        "code": 403,
        "description": "Forbidden: bot was blocked by the user",
        "retryable": False,
        "attempts": 1,
        "chat_id": "111",
        "method": "send_message",
    }


def test_send_text_retries_on_429_and_sleeps_at_least_retry_after() -> None:
    retry_err = TelegramAPIError(
        code=429, description="Too Many Requests", retry_after=1
    )
    success_result = {
        "message_id": 7,
        "chat": {"id": 111},
        "text": "hi",
    }
    api = _ScriptedAPI(script=[retry_err, success_result])
    sleeps: list[float] = []
    audit = AuditLogger()
    svc = _build_service(api, max_attempts=2, sleep_sink=sleeps, audit_logger=audit)

    result = svc.send_text(text="hi", target=_target())

    assert result.ok is True
    assert len(result.sent_messages) == 1
    assert api.calls == 2
    assert sleeps, "expected at least one sleep call for 429 backoff"
    assert sleeps[0] >= 1.0
    assert _failed_events(audit) == []


def test_send_text_raises_chat_not_found_without_retry() -> None:
    err = TelegramAPIError(code=400, description="Bad Request: chat not found")
    api = _ScriptedAPI(script=[err])
    sleeps: list[float] = []
    audit = AuditLogger()
    svc = _build_service(api, max_attempts=3, sleep_sink=sleeps, audit_logger=audit)

    with pytest.raises(TelegramAPIError) as excinfo:
        svc.send_text(text="hi", target=_target())

    assert excinfo.value.code == 400
    assert "chat not found" in excinfo.value.description
    assert excinfo.value.retryable is False
    assert api.calls == 1
    assert sleeps == []

    failures = _failed_events(audit)
    assert len(failures) == 1
    ev = failures[0]
    assert ev.details == {
        "code": 400,
        "description": "Bad Request: chat not found",
        "retryable": False,
        "attempts": 1,
        "chat_id": "111",
        "method": "send_message",
    }


def test_send_text_429_exhausts_attempts_and_raises() -> None:
    api = _ScriptedAPI(
        script=[
            TelegramAPIError(code=429, description="Too Many Requests", retry_after=1),
            TelegramAPIError(code=429, description="Too Many Requests", retry_after=1),
        ]
    )
    sleeps: list[float] = []
    audit = AuditLogger()
    svc = _build_service(api, max_attempts=2, sleep_sink=sleeps, audit_logger=audit)

    with pytest.raises(TelegramAPIError) as excinfo:
        svc.send_text(text="hi", target=_target())

    assert excinfo.value.code == 429
    assert api.calls == 2

    failures = _failed_events(audit)
    assert len(failures) == 1
    ev = failures[0]
    assert ev.details == {
        "code": 429,
        "description": "Too Many Requests",
        "retryable": True,
        "attempts": 2,
        "chat_id": "111",
        "method": "send_message",
    }


def test_no_audit_logger_means_no_emission_but_still_raises() -> None:
    err = TelegramAPIError(code=401, description="Unauthorized")
    api = _ScriptedAPI(script=[err])
    svc = _build_service(api, max_attempts=3, audit_logger=None)

    with pytest.raises(TelegramAPIError):
        svc.send_text(text="hi", target=_target())


def test_telegram_api_error_str_contains_useful_details() -> None:
    err = TelegramAPIError(
        code=429,
        description="Too Many Requests",
        retry_after=1,
    )

    rendered = str(err)

    assert "429" in rendered
    assert "Too Many Requests" in rendered
    assert "retryable=True" in rendered
