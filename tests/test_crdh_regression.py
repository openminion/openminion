from __future__ import annotations

import json
from unittest.mock import patch

import pytest

from openminion.modules.llm.errors import LLMCtlError
from openminion.modules.llm.providers.adapters import CortensorProvider
from openminion.modules.llm.schemas import LLMRequest


class _FakeHTTPResponse:
    def __init__(self, payload):
        self._payload = payload

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def read(self) -> bytes:
        return json.dumps(self._payload).encode("utf-8")


def test_cortensor_offchain_urn_pending_is_bounded() -> None:
    provider = CortensorProvider()
    request = LLMRequest.model_validate(
        {
            "model": "gpt-4.1-mini",
            "messages": [{"role": "user", "content": "hello"}],
        }
    )

    pending_payload = {
        "model": "gpt-4.1-mini",
        "choices": [{"message": {"content": ""}, "finish_reason": "stop"}],
        "result": "urn:blob:pending-result",
    }

    call_count = {"n": 0}

    def _fake_urlopen(*args, **kwargs):
        del args, kwargs
        call_count["n"] += 1
        return _FakeHTTPResponse(pending_payload)

    with patch(
        "openminion.modules.llm.providers.adapters.urllib_request.urlopen",
        side_effect=_fake_urlopen,
    ):
        with pytest.raises(LLMCtlError) as exc:
            provider.complete(
                request,
                {
                    "api_key": "test-key",
                    "base_url": "http://127.0.0.1:8080/api/v2/completions",
                    "api_mode": "openai_chat",
                    # Force tight bounds: inner poll loop should still raise
                    # after the adapter's offchain min wait attempts.
                    "result_wait_attempts": 1,
                    "result_wait_interval_seconds": 0,
                    "empty_result_max_attempts": 1,
                },
            )

    assert exc.value.code == "EMPTY_URN_CONTENT"
    # Adapter should elevate to its minimum offchain wait attempts.
    assert call_count["n"] == 6


class TestTurnTimeoutResolution:
    def test_resolve_turn_timeout_default(self):
        from openminion.services.gateway import _resolve_turn_timeout_seconds

        result = _resolve_turn_timeout_seconds(
            inbound_metadata={},
            default_timeout=600,
        )
        assert result == 600

    def test_resolve_turn_timeout_from_metadata(self):
        from openminion.services.gateway import _resolve_turn_timeout_seconds

        result = _resolve_turn_timeout_seconds(
            inbound_metadata={"turn_timeout_seconds": "300"},
            default_timeout=600,
        )
        assert result == 300

    def test_resolve_turn_timeout_minimum_bound(self):
        from openminion.services.gateway import _resolve_turn_timeout_seconds

        # Below minimum should return default
        result = _resolve_turn_timeout_seconds(
            inbound_metadata={"turn_timeout_seconds": "10"},
            default_timeout=600,
        )
        assert result == 600

    def test_resolve_turn_timeout_maximum_bound(self):
        from openminion.services.gateway import _resolve_turn_timeout_seconds

        # Above maximum should be capped
        result = _resolve_turn_timeout_seconds(
            inbound_metadata={"turn_timeout_seconds": "3600"},
            default_timeout=600,
        )
        assert result == 1800  # Capped at 30 minutes
