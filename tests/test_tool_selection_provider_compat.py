import json
import pathlib
from unittest.mock import patch

from openminion.modules.llm.providers.adapters import (
    CortensorProvider,
    OpenRouterProvider,
)
from openminion.modules.llm.providers.normalization import normalize_provider_response
from openminion.services.tool.selection import (
    SelectionResult,
    ToolSelectionService,
    ToolStub,
    selection_result_to_provider_specs,
    stub_to_provider_spec,
)
from openminion.modules.tool.registry import ToolRegistry
from openminion.modules.llm.schemas import LLMRequest

_TEST_CONFIGS_DIR = (
    pathlib.Path(__file__).resolve().parent.parent.parent / "test-configs"
)


def _search_stub() -> ToolStub:
    return ToolStub(
        name="web.search",
        description_short="Search current web/news information.",
        required_args=["query"],
        example_minimal={"query": "<query>"},
    )


class _FakeHTTPResponse:
    def __init__(self, payload):
        self._payload = payload

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def read(self) -> bytes:
        return json.dumps(self._payload).encode("utf-8")


def test_stub_to_provider_spec_has_required_fields() -> None:
    spec = stub_to_provider_spec(_search_stub())
    assert spec.name == "web.search"
    assert spec.description
    assert "query" in (spec.parameters or {}).get("required", [])


def test_selection_result_to_provider_specs_uses_stubs_for_typed_mode() -> None:
    # TSSR: pre-retirement used mode="ranked"; the typed fallback path now
    # labels the result with mode="typed" and reason_codes=["full_catalog"].
    result = SelectionResult(
        mode="typed",
        shortlist=["web.search"],
        stubs=[_search_stub()],
        full_schema_tools=[],
        category=None,
        binding_source=None,
        fallback_used=False,
        token_estimate=64,
        reason_codes=["full_catalog"],
    )
    # A real service is not required for stub path, but use a typed mock shape.
    service = ToolSelectionService.__new__(ToolSelectionService)
    service._registry = ToolRegistry([])
    specs = selection_result_to_provider_specs(result, service)
    assert len(specs) == 1
    assert specs[0].name == "web.search"


def test_openrouter_adapter_parses_native_tool_calls() -> None:
    provider = OpenRouterProvider()
    request = LLMRequest.model_validate(
        {
            "model": "openai/gpt-4.1-mini",
            "messages": [{"role": "user", "content": "latest news on iran"}],
            "tools": [
                {
                    "name": "web.search",
                    "description": "Search current web/news information.",
                    "input_schema": {
                        "type": "object",
                        "properties": {"query": {"type": "string"}},
                        "required": ["query"],
                    },
                }
            ],
            "tool_choice": "required",
        }
    )
    raw_payload = {
        "model": "openai/gpt-4.1-mini",
        "choices": [
            {
                "message": {
                    "content": "",
                    "tool_calls": [
                        {
                            "id": "call_1",
                            "type": "function",
                            "function": {
                                "name": "web.search",
                                "arguments": '{"query":"latest news on iran"}',
                            },
                        }
                    ],
                },
                "finish_reason": "tool_calls",
            }
        ],
        "usage": {"prompt_tokens": 10, "completion_tokens": 3, "total_tokens": 13},
    }
    with patch(
        "openminion.modules.llm.providers.adapters.urllib_request.urlopen",
        return_value=_FakeHTTPResponse(raw_payload),
    ):
        resp = provider.complete(
            request,
            {
                "api_key": "test-key",
                "base_url": "https://openrouter.ai/api/v1",
                "tool_call_strategy": "hybrid",
            },
        )
    assert resp.ok
    assert resp.tool_calls
    assert resp.tool_calls[0].name == "web.search"
    assert resp.tool_calls[0].arguments.get("query") == "latest news on iran"


def test_cortensor_adapter_parses_native_tool_calls() -> None:
    provider = CortensorProvider()
    request = LLMRequest.model_validate(
        {
            "model": "gpt-4.1-mini",
            "messages": [{"role": "user", "content": "latest news on iran"}],
            "tools": [
                {
                    "name": "web.search",
                    "description": "Search current web/news information.",
                    "input_schema": {
                        "type": "object",
                        "properties": {"query": {"type": "string"}},
                        "required": ["query"],
                    },
                }
            ],
            "tool_choice": "required",
        }
    )
    raw_payload = {
        "model": "gpt-4.1-mini",
        "choices": [
            {
                "message": {
                    "content": "",
                    "tool_calls": [
                        {
                            "id": "call_2",
                            "type": "function",
                            "function": {
                                "name": "web.search",
                                "arguments": '{"query":"iran latest news"}',
                            },
                        }
                    ],
                },
                "finish_reason": "tool_calls",
            }
        ],
        "usage": {"prompt_tokens": 8, "completion_tokens": 2, "total_tokens": 10},
    }
    with patch(
        "openminion.modules.llm.providers.adapters.urllib_request.urlopen",
        return_value=_FakeHTTPResponse(raw_payload),
    ):
        resp = provider.complete(
            request,
            {
                "api_key": "test-key",
                "base_url": "http://127.0.0.1:8080/api/v2/completions",
                "api_mode": "openai_chat",
                "tool_call_strategy": "hybrid",
                "result_wait_attempts": 1,
                "result_wait_interval_seconds": 0,
                "empty_result_max_attempts": 1,
            },
        )
    assert resp.ok
    assert resp.tool_calls
    assert resp.tool_calls[0].name == "web.search"
    assert resp.tool_calls[0].arguments.get("query") == "iran latest news"


def test_openrouter_normalization_blocks_unexecutable_envelope_leak() -> None:
    # Raw envelope target is not allowed; normalization should sanitize text.
    raw_response = {
        "text": (
            "<|start|>assistant<|channel|>commentary to=browser.run "
            '<|constrain|>json<|message|>{"query":"latest news Iran"}<|call|>'
        ),
        "model": "openai/gpt-4.1-mini",
        "tool_calls": [],
        "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
        "finish_reason": "stop",
    }
    normalized = normalize_provider_response(
        raw_response,
        provider_name="openrouter",
        model_name="openai/gpt-4.1-mini",
        allowed_tool_names=["web.search"],
    )
    assert not normalized.tool_calls
    assert "UNEXECUTABLE_TOOL_ENVELOPE" in normalized.text


def test_openrouter_normalization_blocks_unexecutable_minimax_markup_leak() -> None:
    raw_response = {
        "text": (
            "<minimax:tool_call>"
            '<invoke name="tool.use">'
            '<parameter name="tool_name">unknown-tool</parameter>'
            '<parameter name="arguments">{"query":"iran"}</parameter>'
            "</invoke>"
            "</minimax:tool_call>"
        ),
        "model": "minimax/minimax-m2.5",
        "tool_calls": [],
        "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
        "finish_reason": "stop",
    }
    normalized = normalize_provider_response(
        raw_response,
        provider_name="openrouter",
        model_name="minimax/minimax-m2.5",
        allowed_tool_names=["web.search"],
    )
    assert not normalized.tool_calls
    assert "UNEXECUTABLE_TOOL_ENVELOPE" in normalized.text
    assert "<minimax:tool_call>" not in normalized.text


def test_openrouter_normalization_blocks_tool_code_browser_alias() -> None:
    raw_response = {
        "text": (
            "<tool_code>\n"
            "tool: browser_open\n"
            'args: { url: "https://example.com" }\n'
            "</tool_code>"
        ),
        "model": "minimax/minimax-m2.5",
        "tool_calls": [],
        "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
        "finish_reason": "tool_calls",
    }
    normalized = normalize_provider_response(
        raw_response,
        provider_name="openrouter",
        model_name="minimax/minimax-m2.5",
        allowed_tool_names=["browser"],
    )
    assert not normalized.tool_calls
    assert "UNEXECUTABLE_TOOL_ENVELOPE" in normalized.text


def test_openrouter_normalization_blocks_unexecutable_tool_code_leak() -> None:
    raw_response = {
        "text": (
            "<tool_code>\n"
            "tool: unknown_browser_tool\n"
            'args: { url: "https://example.com" }\n'
            "</tool_code>"
        ),
        "model": "minimax/minimax-m2.5",
        "tool_calls": [],
        "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
        "finish_reason": "stop",
    }
    normalized = normalize_provider_response(
        raw_response,
        provider_name="openrouter",
        model_name="minimax/minimax-m2.5",
        allowed_tool_names=["browser"],
    )
    assert not normalized.tool_calls
    assert "UNEXECUTABLE_TOOL_ENVELOPE" in normalized.text
    assert "<tool_code>" not in normalized.text


# Config/profile reproducibility regression tests


def _load_config(filename: str) -> dict:
    p = _TEST_CONFIGS_DIR / filename
    return json.loads(p.read_text())


# Provider compatibility layer - retry logic tests


def test_should_retry_with_auto_tool_choice_triggers_for_openrouter_glm_error() -> None:
    from openminion.modules.llm.providers.tool_choice import (
        should_retry_with_auto_tool_choice,
    )
    from types import SimpleNamespace

    error = SimpleNamespace(
        code="PROVIDER_ERROR",
        message="openrouter request failed with HTTP 404: tool_choice not supported by this model (required)",
    )
    assert should_retry_with_auto_tool_choice(error, tool_choice="required") is True


def test_should_retry_with_auto_tool_choice_triggers_for_glm_natural_language_error() -> (
    None
):
    from openminion.modules.llm.providers.tool_choice import (
        should_retry_with_auto_tool_choice,
    )
    from types import SimpleNamespace

    error = SimpleNamespace(
        code="PROVIDER_ERROR",
        message='openrouter request failed with HTTP 400: {"error":{"message":"Tool choice must be auto","code":400},"user_id":"u_xyz"}',
    )
    assert should_retry_with_auto_tool_choice(error, tool_choice="required") is True


def test_should_retry_with_auto_tool_choice_triggers_for_minimax_m2_7_error() -> None:
    from openminion.modules.llm.providers.tool_choice import (
        should_retry_with_auto_tool_choice,
    )
    from types import SimpleNamespace

    error = SimpleNamespace(
        code="PROVIDER_ERROR",
        message="openrouter request failed with HTTP 400: invalid tool_choice value for this provider",
    )
    assert should_retry_with_auto_tool_choice(error, tool_choice="required") is True


def test_should_retry_with_auto_tool_choice_triggers_for_minimax_chat_setting_error() -> (
    None
):
    from openminion.modules.llm.providers.tool_choice import (
        should_retry_with_auto_tool_choice,
    )
    from types import SimpleNamespace

    error = SimpleNamespace(
        code="PROVIDER_ERROR",
        message='openai request failed with HTTP 400: {"type":"error","error":{"message":"invalid params, invalid chat setting (2013)"}}',
    )

    assert (
        should_retry_with_auto_tool_choice(
            error,
            tool_choice={"type": "function", "function": {"name": "submit_output"}},
        )
        is True
    )


def test_should_retry_with_auto_tool_choice_does_not_trigger_for_unrelated_error() -> (
    None
):
    from openminion.modules.llm.providers.tool_choice import (
        should_retry_with_auto_tool_choice,
    )
    from types import SimpleNamespace

    error = SimpleNamespace(
        code="PROVIDER_ERROR",
        message="openrouter request failed with HTTP 500: internal server error",
    )
    assert should_retry_with_auto_tool_choice(error, tool_choice="required") is False


def test_should_retry_with_auto_tool_choice_skips_when_already_auto() -> None:
    from openminion.modules.llm.providers.tool_choice import (
        should_retry_with_auto_tool_choice,
    )
    from types import SimpleNamespace

    error = SimpleNamespace(
        code="PROVIDER_ERROR",
        message="openrouter request failed: tool_choice unsupported",
    )
    assert should_retry_with_auto_tool_choice(error, tool_choice="auto") is False


def test_openrouter_glm_override_resolves_without_thinking() -> None:
    from openminion.modules.brain.adapters.llm.overrides import (
        resolve_provider_retry_override,
    )

    result = resolve_provider_retry_override(
        provider_name="openrouter",
        model_name="z-ai/glm-5-turbo",
        purpose="decide",
        thinking=None,
        tool_choice="required",
        tool_names=["web.search"],
    )
    assert result.matched is True
    assert result.retry_tool_choice == "auto"
    assert "glm" in result.override_id or "openrouter" in result.override_id


def test_openrouter_minimax_override_resolves_without_thinking() -> None:
    from openminion.modules.brain.adapters.llm.overrides import (
        resolve_provider_retry_override,
    )

    result = resolve_provider_retry_override(
        provider_name="openrouter",
        model_name="minimax/minimax-m2.7",
        purpose="decide",
        thinking=None,
        tool_choice="required",
        tool_names=["web.search"],
    )
    assert result.matched is True
    assert result.retry_tool_choice == "auto"


def test_complete_with_provider_override_retry_retries_on_tool_choice_error() -> None:
    from openminion.modules.llm.providers.tool_choice import (
        complete_with_provider_override_retry,
    )
    from openminion.modules.llm.schemas import LLMResponse, ResponseError

    call_count = 0

    def _fake_complete(**kwargs: object) -> LLMResponse:
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return LLMResponse(
                ok=False,
                provider="openrouter",
                model="z-ai/glm-5-turbo",
                output_text="",
                tool_calls=[],
                usage={},
                latency_ms=10,
                finish_reason="",
                error=ResponseError(
                    code="PROVIDER_ERROR",
                    message="openrouter request failed with HTTP 404: tool_choice required not supported",
                ),
            )
        return LLMResponse(
            ok=True,
            provider="openrouter",
            model="z-ai/glm-5-turbo",
            output_text="ok",
            tool_calls=[],
            usage={},
            latency_ms=10,
            finish_reason="stop",
            error=None,
        )

    result = complete_with_provider_override_retry(
        complete_fn=_fake_complete,
        provider_name="openrouter",
        model_name="z-ai/glm-5-turbo",
        messages=[{"role": "user", "content": "test"}],
        tools=[{"name": "web.search"}],
        tool_choice="required",
        metadata={},
        thinking=None,
    )
    assert call_count == 2, f"Expected 2 calls (initial + retry), got {call_count}"
    assert result.response.ok is True
    assert (
        result.retry_override_id == "openrouter_glm_minimax_tool_choice_required_retry"
    )


def test_complete_with_provider_override_retry_uses_profile_policy_when_supplied() -> (
    None
):
    from openminion.modules.llm.providers.behavior import resolve_behavior_profile
    from openminion.modules.llm.providers.tool_choice import (
        complete_with_provider_override_retry,
    )
    from openminion.modules.llm.schemas import LLMResponse, ResponseError

    profile = resolve_behavior_profile(
        provider="openrouter",
        model="z-ai/glm-5-turbo",
        base_url="https://openrouter.ai/api/v1",
    )
    call_count = 0

    def _fake_complete(**kwargs: object) -> LLMResponse:
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return LLMResponse(
                ok=False,
                provider="openrouter",
                model="z-ai/glm-5-turbo",
                output_text="",
                tool_calls=[],
                usage={},
                latency_ms=10,
                finish_reason="",
                error=ResponseError(
                    code="PROVIDER_ERROR",
                    message="openrouter request failed with HTTP 404: tool_choice required not supported",
                ),
            )
        return LLMResponse(
            ok=True,
            provider="openrouter",
            model="z-ai/glm-5-turbo",
            output_text="ok",
            tool_calls=[],
            usage={},
            latency_ms=10,
            finish_reason="stop",
            error=None,
        )

    result = complete_with_provider_override_retry(
        complete_fn=_fake_complete,
        provider_name="openrouter",
        model_name="z-ai/glm-5-turbo",
        messages=[{"role": "user", "content": "test"}],
        tools=[{"name": "web.search"}],
        tool_choice="required",
        metadata={},
        thinking=None,
        policy=profile.retry_override_policy,
    )
    assert call_count == 2
    assert result.response.ok is True
    assert (
        result.retry_override_id == "openrouter_glm_minimax_tool_choice_required_retry"
    )


def test_glm5_turbo_config_tool_call_strategy_is_not_hybrid() -> None:
    cfg = _load_config("per-agent-openrouter-glm-5-turbo.json")
    strategy = cfg["providers"]["openrouter"]["tool_call_strategy"]
    assert strategy != "hybrid", (
        f"per-agent-openrouter-glm-5-turbo.json tool_call_strategy must not be "
        f"'hybrid' (got {strategy!r}); provider rejects tool_choice=required"
    )
    assert strategy == "auto"


def test_minimax_m2_7_config_tool_call_strategy_is_not_hybrid() -> None:
    cfg = _load_config("per-agent-openrouter-minimax-m2-7.json")
    strategy = cfg["providers"]["openrouter"]["tool_call_strategy"]
    assert strategy != "hybrid", (
        f"per-agent-openrouter-minimax-m2-7.json tool_call_strategy must not be "
        f"'hybrid' (got {strategy!r}); provider returns 404 on unsupported tool_choice"
    )
    assert strategy == "auto"


def _assert_default_agent_in_catalog(cfg: dict, filename: str) -> None:
    agents_catalog = set(cfg.get("agents", {}).keys())
    default_agent = str(cfg.get("default_agent", "") or "").strip()
    if default_agent:
        assert default_agent in agents_catalog, (
            f"{filename} default_agent={default_agent!r} not found in agents "
            f"catalog {agents_catalog}"
        )
    else:
        assert len(agents_catalog) >= 1, (
            f"{filename} has no default_agent and no agents catalog entries; "
            f"CLI profile selection will fail"
        )


def test_cerebras_oss_config_agent_name_matches_catalog() -> None:
    cfg = _load_config("per-agent-cerebras-oss.json")
    _assert_default_agent_in_catalog(cfg, "per-agent-cerebras-oss.json")


def test_cortensor35_config_agent_name_matches_catalog() -> None:
    cfg = _load_config("per-agent-cortensor35.json")
    _assert_default_agent_in_catalog(cfg, "per-agent-cortensor35.json")


def test_cortensor_oss20b_config_agent_name_matches_catalog() -> None:
    cfg = _load_config("per-agent-cortensor-oss20b.json")
    _assert_default_agent_in_catalog(cfg, "per-agent-cortensor-oss20b.json")
