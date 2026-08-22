from __future__ import annotations

import io
import json
import subprocess
from urllib.error import HTTPError, URLError
from unittest.mock import patch

import pytest

from openminion.modules.llm.errors import LLMCtlError
from openminion.modules.llm.providers.transport.curl import curl_json_post
from openminion.modules.llm.providers.transport.error_facts import openai_error_facts
from openminion.modules.llm.providers.transport.http import http_json_post
from openminion.modules.llm.providers.transport.sse import iter_sse_post_lines


def _body() -> str:
    return json.dumps(
        {
            "error": {
                "code": "unsupported_tools",
                "message": "tools are not supported",
                "type": "invalid_request_error",
                "param": "tools",
            },
            "request_id": "req-body",
            "authorization": "Bearer must-not-leak",
        }
    )


def _http_error() -> HTTPError:
    return HTTPError(
        "https://api.cortensor.app/v1/chat/completions",
        400,
        "Bad Request",
        {"X-Request-ID": "req-header"},
        io.BytesIO(_body().encode()),
    )


def test_openai_compatible_error_facts_match_across_transports() -> None:
    errors: list[LLMCtlError] = []
    with (
        patch(
            "openminion.modules.llm.providers.transport.http.urllib_request.urlopen",
            side_effect=_http_error(),
        ),
        pytest.raises(LLMCtlError) as raised,
    ):
        http_json_post(
            url="https://api.cortensor.app/v1/chat/completions",
            payload={"model": "oss-20b"},
            headers={},
            timeout_seconds=480,
            provider_name="openai",
        )
    errors.append(raised.value)

    with (
        patch(
            "openminion.modules.llm.providers.transport.sse.urllib_request.urlopen",
            side_effect=_http_error(),
        ),
        pytest.raises(LLMCtlError) as raised,
    ):
        list(
            iter_sse_post_lines(
                url="https://api.cortensor.app/v1/chat/completions",
                payload={"model": "oss-20b", "stream": True},
                headers={},
                timeout_seconds=480,
                provider_name="openai",
            )
        )
    errors.append(raised.value)

    result = subprocess.CompletedProcess(
        args=["curl"], returncode=0, stdout=f"{_body()}\n400", stderr=""
    )
    with (
        patch(
            "openminion.modules.llm.providers.transport.curl.shutil.which",
            return_value="/usr/bin/curl",
        ),
        patch(
            "openminion.modules.llm.providers.transport.curl.subprocess.run",
            return_value=result,
        ),
        pytest.raises(LLMCtlError) as raised,
    ):
        curl_json_post(
            url="https://api.cortensor.app/v1/chat/completions",
            payload={"model": "oss-20b"},
            body_json=None,
            headers={},
            timeout_seconds=480,
            provider_name="openai",
            reason="fallback",
            with_default_user_agent_fn=lambda value: value,
        )
    errors.append(raised.value)

    expected = {
        "status_code": 400,
        "upstream_code": "unsupported_tools",
        "upstream_message": "tools are not supported",
        "upstream_type": "invalid_request_error",
        "upstream_param": "tools",
    }
    for error in errors:
        assert error.code == "PROVIDER_ERROR"
        assert expected.items() <= error.details.items()
        assert "must-not-leak" not in str(error)
        assert "must-not-leak" not in str(error.details)
    assert errors[0].details["request_id"] == "req-header"
    assert errors[1].details["request_id"] == "req-header"
    assert errors[2].details["request_id"] == "req-body"


def test_openai_error_facts_redact_nested_credentials() -> None:
    secret = "abcdefghijklmnop"
    facts = openai_error_facts(
        json.dumps({"error": {"message": f"failed for Bearer {secret}"}}),
        status_code=400,
    )

    assert secret not in str(facts)
    assert facts["upstream_message"] == "failed for Bearer [REDACTED]"


def test_http_debug_error_records_response_request_id() -> None:
    debug_events: list[dict] = []
    response_metadata: dict[str, str] = {}
    with (
        patch(
            "openminion.modules.llm.providers.transport.http.urllib_request.urlopen",
            side_effect=_http_error(),
        ),
        patch(
            "openminion.modules.llm.providers.transport.http.write_llm_debug_event",
            side_effect=lambda event, **_kwargs: debug_events.append(event),
        ),
        pytest.raises(LLMCtlError),
    ):
        http_json_post(
            url="https://api.cortensor.app/v1/chat/completions",
            payload={"model": "oss-20b"},
            headers={},
            timeout_seconds=480,
            provider_name="openai",
            response_metadata=response_metadata,
        )

    error_event = next(event for event in debug_events if event["event"] == "error")
    assert error_event["request_id"] == "req-header"
    assert response_metadata == {"request_id": "req-header"}


def test_http_debug_response_records_response_request_id() -> None:
    debug_events: list[dict] = []
    with (
        patch(
            "openminion.modules.llm.providers.transport.http._read_http_response",
            return_value=(
                200,
                json.dumps({"choices": [{"message": {"content": "ok"}}]}),
                "req-success",
            ),
        ),
        patch(
            "openminion.modules.llm.providers.transport.http.write_llm_debug_event",
            side_effect=lambda event, **_kwargs: debug_events.append(event),
        ),
    ):
        http_json_post(
            url="https://api.cortensor.app/v1/chat/completions",
            payload={"model": "oss-20b"},
            headers={},
            timeout_seconds=480,
            provider_name="openai",
        )

    response_event = next(
        event for event in debug_events if event["event"] == "response"
    )
    assert response_event["request_id"] == "req-success"


def test_http_post_can_disable_dns_curl_resubmission() -> None:
    with (
        patch(
            "openminion.modules.llm.providers.transport.http._read_http_response",
            side_effect=URLError("temporary failure in name resolution"),
        ),
        patch(
            "openminion.modules.llm.providers.transport.http.curl_json_post"
        ) as curl_post,
        pytest.raises(LLMCtlError),
    ):
        http_json_post(
            url="https://api.cortensor.app/v1/chat/completions",
            payload={"model": "oss-20b"},
            headers={},
            timeout_seconds=480,
            provider_name="openai",
            allow_curl_fallback=False,
        )

    curl_post.assert_not_called()


@pytest.mark.parametrize(
    "raw_body",
    [
        "Bearer abcdefghijklmnop",
        json.dumps(["Bearer abcdefghijklmnop"]),
    ],
)
def test_urllib_malformed_responses_do_not_leak(raw_body: str) -> None:
    debug_events: list[dict] = []
    response_traces: list[dict] = []
    with (
        patch(
            "openminion.modules.llm.providers.transport.http._read_http_response",
            return_value=(200, raw_body, ""),
        ),
        patch(
            "openminion.modules.llm.providers.transport.http.write_llm_debug_event",
            side_effect=lambda event, **_kwargs: debug_events.append(event),
        ),
        patch(
            "openminion.modules.llm.providers.transport.http.trace_http_json_response",
            side_effect=lambda **kwargs: response_traces.append(
                {
                    key: kwargs.get(key)
                    for key in ("body_text", "parsed_json", "parse_error")
                }
            ),
        ),
        pytest.raises(LLMCtlError),
    ):
        http_json_post(
            url="https://api.cortensor.app/v1/chat/completions",
            payload={"model": "oss-20b"},
            headers={},
            timeout_seconds=480,
            provider_name="openai",
        )

    evidence = json.dumps([debug_events, response_traces])
    assert "abcdefghijklmnop" not in evidence
    assert "response_bytes" in evidence


@pytest.mark.parametrize(
    "raw_body",
    [
        "Bearer abcdefghijklmnop",
        json.dumps(["Bearer abcdefghijklmnop"]),
    ],
)
def test_curl_malformed_responses_do_not_leak(raw_body: str) -> None:
    debug_events: list[dict] = []
    response_traces: list[dict] = []
    result = subprocess.CompletedProcess(
        args=["curl"], returncode=0, stdout=f"{raw_body}\n200", stderr=""
    )
    with (
        patch(
            "openminion.modules.llm.providers.transport.curl.shutil.which",
            return_value="/usr/bin/curl",
        ),
        patch(
            "openminion.modules.llm.providers.transport.curl.subprocess.run",
            return_value=result,
        ),
        patch(
            "openminion.modules.llm.providers.transport.curl.write_llm_debug_event",
            side_effect=lambda event, **_kwargs: debug_events.append(event),
        ),
        patch(
            "openminion.modules.llm.providers.transport.curl.trace_http_json_response",
            side_effect=lambda **kwargs: response_traces.append(
                {
                    key: kwargs.get(key)
                    for key in ("body_text", "parsed_json", "parse_error")
                }
            ),
        ),
        pytest.raises(LLMCtlError),
    ):
        curl_json_post(
            url="https://api.cortensor.app/v1/chat/completions",
            payload={"model": "oss-20b"},
            body_json=None,
            headers={},
            timeout_seconds=480,
            provider_name="openai",
            reason="fallback",
            with_default_user_agent_fn=lambda value: value,
        )

    evidence = json.dumps([debug_events, response_traces])
    assert "abcdefghijklmnop" not in evidence
    assert "response_bytes" in evidence
