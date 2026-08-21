from __future__ import annotations

from unittest.mock import patch

from openminion.modules.llm.errors import LLMCtlError
from openminion.modules.llm.providers.adapters import OpenAIProvider
from openminion.modules.llm.schemas import LLMRequest


def test_minimax_retries_transient_tool_transcript_2013_once() -> None:
    provider = OpenAIProvider()
    request = LLMRequest.model_validate(
        {
            "model": "MiniMax-M2.7",
            "messages": [
                {
                    "role": "assistant",
                    "tool_calls": [
                        {
                            "id": "call-1",
                            "name": "file.write",
                            "arguments": {"path": "result.txt", "content": "ok"},
                        }
                    ],
                },
                {
                    "role": "tool",
                    "content": '{"ok":true}',
                    "tool_call_id": "call-1",
                    "tool_status": "success",
                },
            ],
            "tools": [
                {
                    "name": "file.write",
                    "description": "write a file",
                    "input_schema": {"type": "object"},
                }
            ],
            "tool_choice": "auto",
        }
    )
    transient = LLMCtlError(
        "PROVIDER_ERROR",
        "openai request failed with HTTP 400",
        details={
            "status_code": 400,
            "upstream_message": (
                "invalid params, tool call result does not follow tool call (2013)"
            ),
        },
    )
    success = {
        "model": "MiniMax-M2.7",
        "choices": [{"finish_reason": "stop", "message": {"content": "done"}}],
        "usage": {"prompt_tokens": 3, "completion_tokens": 1, "total_tokens": 4},
    }

    with patch(
        "openminion.modules.llm.providers.openai.adapter._http_json_post",
        side_effect=[transient, success],
    ) as post:
        response = provider.complete(
            request,
            {
                "api_key": "test-key",
                "base_url": "https://api.minimax.io/v1",
                "tool_call_strategy": "hybrid",
            },
        )

    assert response.ok
    assert post.call_count == 2
