from __future__ import annotations

from typing import Any

from openminion.modules.llm import LLMCTL
from openminion.modules.llm.schemas import LLMRequest, LLMResponse, UsageInfo
from openminion.modules.llm.providers.tool_calling import (
    extract_fallback_tool_calls_from_text,
)


def test_rejects_minimax_tool_call_with_cli_alias() -> None:
    text = (
        "<minimax:tool_call>\n"
        '  <invoke name="cli">\n'
        '    <parameter name="command">ls</parameter>\n'
        "  </invoke>\n"
        "</minimax:tool_call>"
    )
    calls = extract_fallback_tool_calls_from_text(
        text,
        allowed_tool_names={"exec.run"},
    )
    assert len(calls) == 0


def test_extracts_minimax_tool_call_with_explicit_tool_name() -> None:
    text = (
        "<minimax:tool_call>\n"
        '  <invoke name="file.read">\n'
        '    <parameter name="path">README.md</parameter>\n'
        "  </invoke>\n"
        "</minimax:tool_call>"
    )
    calls = extract_fallback_tool_calls_from_text(
        text,
        allowed_tool_names={"file.read"},
    )
    assert len(calls) == 1
    assert calls[0].name == "file.read"
    assert calls[0].arguments == {"path": "README.md"}


def test_extracts_minimax_bracket_tool_call_with_search_alias_to_web_search() -> None:
    text = (
        "[TOOL_CALL]"
        '{tool => "search", args => { --query "public sentiment on Iran war" }}'
        "[/TOOL_CALL]"
    )
    calls = extract_fallback_tool_calls_from_text(
        text,
        allowed_tool_names={"web.search"},
    )
    assert len(calls) == 1
    assert calls[0].name == "web.search"
    assert calls[0].arguments == {"query": "public sentiment on Iran war"}


def test_extracts_minimax_bracket_tool_call_with_model_facing_web_search_name() -> None:
    text = (
        "[TOOL_CALL]"
        '{tool => "web_search", args => { --query "latest news Iran" --num 10 }}'
        "[/TOOL_CALL]"
    )
    calls = extract_fallback_tool_calls_from_text(
        text,
        allowed_tool_names={"web.search"},
    )
    assert len(calls) == 1
    assert calls[0].name == "web.search"
    assert calls[0].arguments == {"query": "latest news Iran", "num": "10"}


def test_extracts_minimax_bracket_tool_call_with_single_quoted_hyphenated_web_search_name() -> (
    None
):
    text = (
        "[TOOL_CALL]"
        "{tool => 'web-search', args => { --query 'latest news Iran' --num 10 }}"
        "[/TOOL_CALL]"
    )
    calls = extract_fallback_tool_calls_from_text(
        text,
        allowed_tool_names={"web.search"},
    )
    assert len(calls) == 1
    assert calls[0].name == "web.search"
    assert calls[0].arguments == {"query": "latest news Iran", "num": "10"}


def test_extracts_minimax_bracket_file_wrapper_call_with_read_operation() -> None:
    text = (
        "[TOOL_CALL]"
        '{tool => "file", args => { --operation "read" --path "README.md" }}'
        "[/TOOL_CALL]"
    )
    calls = extract_fallback_tool_calls_from_text(
        text,
        allowed_tool_names={"file.read"},
    )
    assert len(calls) == 1
    assert calls[0].name == "file.read"
    assert calls[0].arguments == {"operation": "read", "path": "README.md"}


def test_extracts_minimax_bracket_file_wrapper_call_with_search_operation() -> None:
    text = (
        "[TOOL_CALL]"
        '{tool => "file", args => { --operation "search" --path "." --query "alpha" }}'
        "[/TOOL_CALL]"
    )
    calls = extract_fallback_tool_calls_from_text(
        text,
        allowed_tool_names={"file.search"},
    )
    assert len(calls) == 1
    assert calls[0].name == "file.search"
    assert calls[0].arguments == {
        "operation": "search",
        "path": ".",
        "query": "alpha",
    }


def test_extracts_minimax_bracket_web_wrapper_call_with_search_operation() -> None:
    text = (
        "[TOOL_CALL]"
        '{tool => "web", args => { --operation "search" --query "latest OpenAI news" }}'
        "[/TOOL_CALL]"
    )
    calls = extract_fallback_tool_calls_from_text(
        text,
        allowed_tool_names={"web.search"},
    )
    assert len(calls) == 1
    assert calls[0].name == "web.search"
    assert calls[0].arguments == {
        "operation": "search",
        "query": "latest OpenAI news",
    }


def test_extracts_minimax_bracket_task_wrapper_call_with_cancel_operation() -> None:
    text = (
        "[TOOL_CALL]"
        '{tool => "task", args => { --operation "cancel" --task_id "job-123" }}'
        "[/TOOL_CALL]"
    )
    calls = extract_fallback_tool_calls_from_text(
        text,
        allowed_tool_names={"task.cancel"},
    )
    assert len(calls) == 1
    assert calls[0].name == "task.cancel"
    assert calls[0].arguments == {
        "operation": "cancel",
        "task_id": "job-123",
    }


def test_extracts_minimax_bracket_file_wrapper_call_with_list_alias() -> None:
    text = (
        "[TOOL_CALL]"
        '{tool => "file", args => { --operation "ls" --path "." }}'
        "[/TOOL_CALL]"
    )
    calls = extract_fallback_tool_calls_from_text(
        text,
        allowed_tool_names={"file.list_dir"},
    )
    assert len(calls) == 1
    assert calls[0].name == "file.list_dir"
    assert calls[0].arguments == {"operation": "ls", "path": "."}


def test_extracts_minimax_json_list_with_colon_op_and_args() -> None:
    text = (
        '[{":op":"web.fetch",":args":{"url":"https://example.com"}},'
        '{":op":"file.write",":args":{"path":"/tmp/out.txt","content":"ok"}}]'
    )
    calls = extract_fallback_tool_calls_from_text(
        text,
        allowed_tool_names={"web.fetch", "file.write"},
    )
    assert [call.name for call in calls] == ["web.fetch", "file.write"]
    assert calls[0].arguments == {"url": "https://example.com"}
    assert calls[1].arguments == {"path": "/tmp/out.txt", "content": "ok"}


class _BracketLeakProvider:
    name = "bracket_leak_provider"
    contract_version = "v1"

    def complete(self, request: LLMRequest, config: dict[str, Any]) -> LLMResponse:
        del config
        text = (
            "[TOOL_CALL]"
            '{tool => "web.search", args => { --query "latest news Iran" --num 10 }}'
            "[/TOOL_CALL]"
        )
        return LLMResponse(
            ok=True,
            provider=self.name,
            model=request.model or "bracket-model",
            output_text=text,
            assistant_messages=[],
            tool_calls=[],
            usage=UsageInfo(input_tokens=10, output_tokens=10, total_tokens=20),
            latency_ms=0,
            provider_raw={},
            error=None,
        )

    def list_models(self, config: dict[str, Any]) -> list[str]:
        del config
        return ["bracket-model"]

    def healthcheck(self, config: dict[str, Any]) -> dict[str, Any]:
        del config
        return {"ok": True}


def test_runtime_client_recovers_minimax_bracket_tool_calls_from_output_text() -> None:
    runtime = LLMCTL.from_config(
        {
            "version": 1,
            "llmctl": {
                "default_provider": "bracket_leak_provider",
                "default_model": "bracket-model",
            },
            "providers": {"bracket_leak_provider": {}},
            "agents": {
                "default": {
                    "default_provider": "bracket_leak_provider",
                    "default_model": "bracket-model",
                    "tool_policy": {"enable_tools": True},
                }
            },
        }
    )
    runtime.registry.add(_BracketLeakProvider())
    client = runtime.client(agent_name="default")

    response = client.complete(
        messages=[{"role": "user", "content": "latest news"}],
        tools=[
            {
                "name": "web.search",
                "description": "Search the web.",
                "input_schema": {"type": "object", "properties": {}},
            }
        ],
    )

    assert response.ok is True
    assert response.output_text == ""
    assert response.assistant_messages == []
    assert len(response.tool_calls) == 1
    assert response.tool_calls[0].name == "web.search"
    assert response.tool_calls[0].arguments == {
        "query": "latest news Iran",
        "num": "10",
    }


class _SingleQuotedBracketLeakProvider:
    name = "single_quoted_bracket_leak_provider"
    contract_version = "v1"

    def complete(self, request: LLMRequest, config: dict[str, Any]) -> LLMResponse:
        del config
        text = (
            "[TOOL_CALL]"
            "{tool => 'web-search', args => { --query 'latest news Iran' --num 10 }}"
            "[/TOOL_CALL]"
        )
        return LLMResponse(
            ok=True,
            provider=self.name,
            model=request.model or "bracket-model",
            output_text=text,
            assistant_messages=[],
            tool_calls=[],
            usage=UsageInfo(input_tokens=10, output_tokens=10, total_tokens=20),
            latency_ms=0,
            provider_raw={},
            error=None,
        )

    def list_models(self, config: dict[str, Any]) -> list[str]:
        del config
        return ["bracket-model"]

    def healthcheck(self, config: dict[str, Any]) -> dict[str, Any]:
        del config
        return {"ok": True}


def test_runtime_client_recovers_single_quoted_hyphenated_minimax_bracket_tool_calls() -> (
    None
):
    runtime = LLMCTL.from_config(
        {
            "version": 1,
            "llmctl": {
                "default_provider": "single_quoted_bracket_leak_provider",
                "default_model": "bracket-model",
            },
            "providers": {"single_quoted_bracket_leak_provider": {}},
            "agents": {
                "default": {
                    "default_provider": "single_quoted_bracket_leak_provider",
                    "default_model": "bracket-model",
                    "tool_policy": {"enable_tools": True},
                }
            },
        }
    )
    runtime.registry.add(_SingleQuotedBracketLeakProvider())
    client = runtime.client(agent_name="default")

    response = client.complete(
        messages=[{"role": "user", "content": "latest news"}],
        tools=[
            {
                "name": "web.search",
                "description": "Search the web.",
                "input_schema": {"type": "object", "properties": {}},
            }
        ],
    )

    assert response.ok is True
    assert response.output_text == ""
    assert response.assistant_messages == []
    assert len(response.tool_calls) == 1
    assert response.tool_calls[0].name == "web.search"
    assert response.tool_calls[0].arguments == {
        "query": "latest news Iran",
        "num": "10",
    }


def test_rejects_function_call_block_with_ddg_alias_to_tavily() -> None:
    text = (
        "<FunctionCall>\n"
        "{'tool' => 'ddg-search_search', 'args' => '\n"
        '<param name="query">latest news Iran</param>\n'
        '<param name="max_results">10</param>\n'
        "'}\n"
        "</FunctionCall>"
    )
    calls = extract_fallback_tool_calls_from_text(
        text,
        allowed_tool_names={"web.search"},
    )
    assert len(calls) == 0


def test_rejects_function_call_block_with_ddg_alias_to_web_search() -> None:
    text = (
        "<FunctionCall>\n"
        "{'tool' => 'ddg-search_search', 'args' => '\n"
        '<param name="query">latest news Iran</param>\n'
        "'}\n"
        "</FunctionCall>"
    )
    calls = extract_fallback_tool_calls_from_text(
        text,
        allowed_tool_names={"web.search"},
    )
    assert len(calls) == 0


def test_extracts_minimax_function_calls_search_alias_to_web_search() -> None:
    text = (
        "<function_calls>\n"
        '  <invoke name="search">\n'
        '    <parameter name="q">latest iran news 2026</parameter>\n'
        '    <parameter name="source">serpapi</parameter>\n'
        "  </invoke>\n"
        "</function_calls>"
    )
    calls = extract_fallback_tool_calls_from_text(
        text,
        allowed_tool_names={"web.search"},
    )
    assert len(calls) == 1
    assert calls[0].name == "web.search"
    assert calls[0].arguments == {
        "q": "latest iran news 2026",
        "source": "serpapi",
    }


class _FunctionCallsSearchLeakProvider:
    name = "function_calls_search_leak_provider"
    contract_version = "v1"

    def complete(self, request: LLMRequest, config: dict[str, Any]) -> LLMResponse:
        del config
        text = (
            "<function_calls>\n"
            '  <invoke name="search">\n'
            '    <parameter name="q">latest iran news 2026</parameter>\n'
            '    <parameter name="source">serpapi</parameter>\n'
            "  </invoke>\n"
            "</function_calls>"
        )
        return LLMResponse(
            ok=True,
            provider=self.name,
            model=request.model or "function-calls-model",
            output_text=text,
            assistant_messages=[],
            tool_calls=[],
            usage=UsageInfo(input_tokens=10, output_tokens=10, total_tokens=20),
            latency_ms=0,
            provider_raw={},
            error=None,
        )

    def list_models(self, config: dict[str, Any]) -> list[str]:
        del config
        return ["function-calls-model"]

    def healthcheck(self, config: dict[str, Any]) -> dict[str, Any]:
        del config
        return {"ok": True}


def test_runtime_client_recovers_minimax_function_calls_search_alias() -> None:
    runtime = LLMCTL.from_config(
        {
            "version": 1,
            "llmctl": {
                "default_provider": "function_calls_search_leak_provider",
                "default_model": "function-calls-model",
            },
            "providers": {"function_calls_search_leak_provider": {}},
            "agents": {
                "default": {
                    "default_provider": "function_calls_search_leak_provider",
                    "default_model": "function-calls-model",
                    "tool_policy": {"enable_tools": True},
                }
            },
        }
    )
    runtime.registry.add(_FunctionCallsSearchLeakProvider())
    client = runtime.client(agent_name="default")

    response = client.complete(
        messages=[{"role": "user", "content": "latest news"}],
        tools=[
            {
                "name": "web.search",
                "description": "Search the web.",
                "input_schema": {"type": "object", "properties": {}},
            }
        ],
    )

    assert response.ok is True
    assert response.output_text == ""
    assert response.assistant_messages == []
    assert len(response.tool_calls) == 1
    assert response.tool_calls[0].name == "web.search"
    assert response.tool_calls[0].arguments == {
        "q": "latest iran news 2026",
        "source": "serpapi",
    }


def test_rejects_minimax_tool_use_wrapper_with_ddg_alias_to_tavily() -> None:
    text = (
        "<minimax:tool_call>\n"
        '  <invoke name="tool.use">\n'
        '    <parameter name="tool_name">ddg-search</parameter>\n'
        '    <parameter name="arguments">{"query":"CNN Iran news latest","max_results":10}</parameter>\n'
        "  </invoke>\n"
        "</minimax:tool_call>"
    )
    calls = extract_fallback_tool_calls_from_text(
        text,
        allowed_tool_names={"web.search"},
    )
    assert len(calls) == 0


def test_rejects_minimax_tool_use_wrapper_with_ddg_alias_to_web_search() -> None:
    text = (
        "<minimax:tool_call>\n"
        '  <invoke name="tool.use">\n'
        '    <parameter name="tool_name">ddg-search</parameter>\n'
        '    <parameter name="arguments">{"query":"CNN Iran news latest"}</parameter>\n'
        "  </invoke>\n"
        "</minimax:tool_call>"
    )
    calls = extract_fallback_tool_calls_from_text(
        text,
        allowed_tool_names={"web.search"},
    )
    assert len(calls) == 0


def test_extracts_minimax_xml_weather_forecast_alias_to_weather() -> None:
    text = (
        "<tool_call>\n"
        '<tool name="weather_forecast">\n'
        '<param name="location">san francisco</param>\n'
        "</tool>\n"
        "</tool_call>"
    )
    calls = extract_fallback_tool_calls_from_text(
        text,
        allowed_tool_names={"weather"},
    )
    assert len(calls) == 0


def test_extracts_json_tool_call_exec_alias_to_exec_run() -> None:
    text = '<tool_call>\n{"name":"exec","parameters":{"command":"pwd"}}\n</tool_call>'
    calls = extract_fallback_tool_calls_from_text(
        text,
        model_name="minimax",
        allowed_tool_names={"exec.run"},
    )
    assert len(calls) == 1
    assert calls[0].name == "exec.run"
    assert calls[0].arguments == {"command": "pwd"}


def test_extracts_minimax_weather_worker_alias_to_weather() -> None:
    text = (
        "<minimax:tool_call>\n"
        '  <invoke name="weather-data_worker_get_current_weather">\n'
        '    <parameter name="location">san francisco</parameter>\n'
        "  </invoke>\n"
        "</minimax:tool_call>"
    )
    calls = extract_fallback_tool_calls_from_text(
        text,
        allowed_tool_names={"weather"},
    )
    assert len(calls) == 0


def test_extracts_plain_tool_directive_weather_forecast_to_weather() -> None:
    text = (
        "Let me check the weather for Los Angeles for you.\n\n"
        "Tool: weather.forecast\n"
        '- location: "Los Angeles, CA"\n'
        '- units: "fahrenheit"\n'
    )
    calls = extract_fallback_tool_calls_from_text(
        text,
        allowed_tool_names={"weather"},
    )
    assert len(calls) == 0
