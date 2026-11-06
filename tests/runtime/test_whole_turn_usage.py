"""Ordinary runtime usage, with a controlled OpenAI-compatible HTTP provider."""

import json
from pathlib import Path
from typing import Any
from unittest.mock import Mock

import pytest

from openminion.modules.telemetry.usage import StatsService
from tests.runtime.test_turn_capture_authorization import (
    _Provider,
    _runtime,
    _call,
    _completion,
)


class _UsageProvider(_Provider):
    def __init__(self, file_path: Path | None = None) -> None:
        self.file_path = file_path
        self.reads = 0
        self.usage: list[dict[str, Any]] = []
        super().__init__()

    def respond(self, body: dict[str, Any]) -> dict[str, Any]:
        names = [tool["function"]["name"] for tool in body.get("tools", [])]
        if "respond" in names:
            self.requests.append("entry")
            response = (
                _call("tool_request", {"name": "file.read", "freshness": {}})
                if self.file_path
                else _call("respond", {"answer": "Hello.", "freshness": {}})
            )
        elif self.file_path and "file_read" in names and self.reads < 2:
            self.reads += 1
            self.requests.append("read")
            response = _call(
                "file_read",
                {
                    "path": str(
                        self.file_path
                        if self.reads == 1
                        else self.file_path.with_name("second.txt")
                    )
                },
            )
        elif self.file_path and "file_read" in names:
            self.requests.append("answer")
            response = _completion(
                {
                    "role": "assistant",
                    "content": 'Read both fixtures.\n<finalization_status>{"status":"final_answer","reasoning":"Both reads completed."}</finalization_status>',
                },
                "stop",
            )
        else:
            response = super().respond(body)
        number = len(self.usage) + 1
        usage = {
            "prompt_tokens": number * 10,
            "completion_tokens": number,
            "total_tokens": number * 11,
            "prompt_tokens_details": {"cached_tokens": number * 2},
        }
        self.usage.append(usage)
        response["usage"] = usage
        for call in response["choices"][0]["message"].get("tool_calls", []):
            call["id"] = f"call-{number}"
        return response


@pytest.mark.parametrize("with_tools", [False, True])
def test_public_turn_usage_includes_every_provider_response(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, with_tools: bool
) -> None:
    fixture = tmp_path / "fixture.txt"
    fixture.write_text("first line\nsecond line\n")
    fixture.with_name("second.txt").write_text("different contents\n")
    provider = _UsageProvider(fixture if with_tools else None)
    runtime = _runtime(tmp_path, monkeypatch, provider, constraint=None)
    try:
        result = runtime.run_turn(
            payload={
                "message": "Read the fixture twice and report its contents."
                if with_tools
                else "Say hello.",
                "session_id": "usage",
            }
        )
        stats = result["stats"]
        assert stats["llm_calls"] == len(provider.usage)
        assert stats["input_tokens"] == sum(
            row["prompt_tokens"] for row in provider.usage
        )
        assert stats["output_tokens"] == sum(
            row["completion_tokens"] for row in provider.usage
        )
        assert stats["cache_read_tokens"] == sum(
            row["prompt_tokens_details"]["cached_tokens"] for row in provider.usage
        )
        assert len(provider.usage) == (6 if with_tools else 2)
        assert stats["tool_calls"] == (2 if with_tools else 0)
        metadata = result["metadata"]
        assert json.loads(metadata["run_stats_json"]) == stats
        assert int(metadata["llm_calls_count"]) == stats["llm_calls"]
        store = (
            runtime.resolve_agent_service("openminion")._get_runner().session_api.store
        )
        usage = StatsService(store).get_run_token_usage(
            result["run_id"],
            session_id=f"usage::conv:{result['metadata']['thread_id']}",
        )
        assert usage is not None
        assert usage.total_input_tokens == stats["input_tokens"]
        assert usage.total_output_tokens == stats["output_tokens"]
        assert usage.total_cache_read_tokens == stats["cache_read_tokens"]
        assert usage.coverage.llm_call_events == stats["llm_calls"]
        outbound = runtime.sessions.list_messages(session_id="usage", limit=100)[-1]
        assert json.loads(outbound.metadata["run_stats_json"]) == stats
    finally:
        runtime.close()
        provider.close()


@pytest.mark.parametrize("missing", [False, True])
def test_consecutive_turns_do_not_reuse_prior_usage(tmp_path, monkeypatch, missing):
    provider = _UsageProvider()
    runtime = _runtime(tmp_path, monkeypatch, provider, constraint=None)
    original_respond = provider.respond

    def respond(body):
        response = original_respond(body)
        if missing:
            response.pop("usage")
        return response

    provider.respond = respond
    try:
        first = runtime.run_turn(
            payload={"message": "First greeting.", "session_id": "two-turns"}
        )
        mark = len(provider.usage)
        second = runtime.run_turn(
            payload={"message": "Second greeting.", "session_id": "two-turns"}
        )
        for result, rows in (
            (first, provider.usage[:mark]),
            (second, provider.usage[mark:]),
        ):
            stats = result["stats"]
            assert stats["llm_calls"] == len(rows) == 2
            assert stats["input_tokens"] == (
                0 if missing else sum(row["prompt_tokens"] for row in rows)
            )
            assert stats["cache_read_tokens"] == (
                0
                if missing
                else sum(row["prompt_tokens_details"]["cached_tokens"] for row in rows)
            )
            coverage = json.loads(result["metadata"]["llm_usage_coverage"])
            assert coverage["cache_read_tokens"]["missing"] == (2 if missing else 0)
            assert coverage["input_tokens"]["reported"] == (0 if missing else 2)
        assert first["run_id"] != second["run_id"]
    finally:
        runtime.close()
        provider.close()


def test_concurrent_public_turns_keep_separate_call_events(tmp_path, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor

    provider = _UsageProvider()
    runtime = _runtime(tmp_path, monkeypatch, provider, constraint=None)
    runner = runtime.resolve_agent_service("openminion")._get_runner()
    # Usage isolation starts after lazy identity/context SQLite initialization.
    runner._build_context(
        state=runner._load_or_init_state("usage-context-init"),
        purpose="reflect",
        budget={"max_tokens": 800},
        hints={},
        logger=Mock(),
    )
    assert provider.usage == []
    try:

        def turn(session_id):
            return runtime.run_turn(
                payload={"message": "Say hello.", "session_id": session_id}
            )

        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(turn, ("first-session", "second-session")))
        assert len(provider.usage) == 4
        store = (
            runtime.resolve_agent_service("openminion")._get_runner().session_api.store
        )
        all_ids = set()
        for result in results:
            session_id = (
                f"{result['session_id']}::conv:{result['metadata']['thread_id']}"
            )
            events = [
                event
                for event in store.get_events(session_id)
                if event["event_type"] == "llm.call.completed"
            ]
            assert len(events) == result["stats"]["llm_calls"] == 2
            assert {event["payload"]["run_id"] for event in events} == {
                result["run_id"]
            }
            ids = {event["payload"]["llm_call_id"] for event in events}
            assert not ids & all_ids
            all_ids.update(ids)
            usage = StatsService(store).get_run_token_usage(
                result["run_id"], session_id=session_id
            )
            assert usage.total_input_tokens == result["stats"]["input_tokens"]
    finally:
        runtime.close()
        provider.close()


def test_entry_correction_keeps_each_response_usage(tmp_path, monkeypatch):
    provider = _UsageProvider()
    original_respond = provider.respond
    corrected = False

    def respond(body):
        nonlocal corrected
        response = original_respond(body)
        if not corrected and any(
            tool["function"]["name"] == "respond" for tool in body.get("tools", [])
        ):
            corrected = True
            empty = _completion({"role": "assistant", "content": ""}, "stop")
            empty["usage"] = response["usage"]
            return empty
        return response

    provider.respond = respond
    runtime = _runtime(tmp_path, monkeypatch, provider, constraint=None)
    try:
        result = runtime.run_turn(
            payload={"message": "Say hello.", "session_id": "correction"}
        )
        assert provider.requests == ["extraction", "entry", "entry"]
        assert result["stats"]["llm_calls"] == 3
        assert result["stats"]["input_tokens"] == 60
        assert result["stats"]["output_tokens"] == 6
        assert result["stats"]["cache_read_tokens"] == 12
        phases = json.loads(result["metadata"]["llm_call_counts_by_purpose"])
        assert phases["reflect"] == 1
        assert phases["entry"] == 2
    finally:
        runtime.close()
        provider.close()


def test_continuation_steps_share_run_usage_beyond_event_tail(tmp_path, monkeypatch):
    fixture = tmp_path / "first.txt"
    fixture.write_text("first\n")
    fixture.with_name("second.txt").write_text("second\n")
    provider = _UsageProvider(fixture)
    original_respond = provider.respond

    def respond(body):
        response = original_respond(body)
        content = response["choices"][0]["message"].get("content") or ""
        if "<finalization_status>" in content:
            response["choices"][0]["message"]["content"] = "Noted."
        return response

    provider.respond = respond
    runtime = _runtime(tmp_path, monkeypatch, provider, constraint=None)
    try:
        result = runtime.run_turn(
            payload={"message": "Read both fixtures.", "session_id": "continuation"}
        )
        assert len(provider.usage) > 6
        assert result["stats"]["llm_calls"] == len(provider.usage)
        assert result["stats"]["input_tokens"] == sum(
            row["prompt_tokens"] for row in provider.usage
        )
        assert result["stats"]["output_tokens"] == sum(
            row["completion_tokens"] for row in provider.usage
        )
        store = (
            runtime.resolve_agent_service("openminion")._get_runner().session_api.store
        )
        session_id = f"continuation::conv:{result['metadata']['thread_id']}"
        events = store.list_events(session_id, limit=None)
        assert len(events) > 100
        calls = [event for event in events if event["type"] == "llm.call.completed"]
        assert len({event["trace_id"] for event in calls}) > 1
        assert {event["payload"]["run_id"] for event in calls} == {result["run_id"]}
        usage = StatsService(store).get_run_token_usage(
            result["run_id"], session_id=session_id
        )
        assert usage.total_input_tokens == result["stats"]["input_tokens"]
    finally:
        runtime.close()
        provider.close()


def test_partial_usage_does_not_reuse_action_or_inference_totals(tmp_path, monkeypatch):
    provider = _UsageProvider()
    original_respond = provider.respond

    def respond(body):
        response = original_respond(body)
        if any(tool["function"]["name"] == "respond" for tool in body.get("tools", [])):
            response.pop("usage")
        return response

    provider.respond = respond
    runtime = _runtime(tmp_path, monkeypatch, provider, constraint=None)
    try:
        result = runtime.run_turn(
            payload={"message": "Say hello.", "session_id": "partial"}
        )
        assert result["stats"]["llm_calls"] == 2
        assert result["stats"]["input_tokens"] == 10
        assert result["stats"]["output_tokens"] == 1
        assert result["stats"]["cache_read_tokens"] == 2
        coverage = json.loads(result["metadata"]["llm_usage_coverage"])
        assert coverage["input_tokens"] == {"reported": 1, "missing": 1, "invalid": 0}
        assert coverage["cache_read_tokens"] == {
            "reported": 1,
            "missing": 1,
            "invalid": 0,
        }
    finally:
        runtime.close()
        provider.close()


def test_failed_entry_request_is_counted_without_fabricated_usage(
    tmp_path, monkeypatch
):
    from openminion.modules.llm.errors import LLMCtlError

    provider = _UsageProvider()
    runtime = _runtime(tmp_path, monkeypatch, provider, constraint=None)
    client = runtime.resolve_agent_service("openminion")._get_runner().llm_api.client
    original_call = client.call
    failed = False

    def call(request):
        nonlocal failed
        if request.metadata.get("purpose") == "entry" and not failed:
            failed = True
            raise LLMCtlError("TIMEOUT", "controlled request failure")
        return original_call(request)

    monkeypatch.setattr(client, "call", call)
    try:
        result = runtime.run_turn(
            payload={"message": "Say hello.", "session_id": "failed-request"}
        )
        assert failed
        assert len(provider.requests) == 2
        assert result["stats"]["llm_calls"] == 3
        assert result["stats"]["input_tokens"] == 30
        assert result["stats"]["output_tokens"] == 3
        coverage = json.loads(result["metadata"]["llm_usage_coverage"])
        assert coverage["failed_llm_call_events"] == 1
        assert coverage["input_tokens"] == {"reported": 2, "missing": 1, "invalid": 0}
    finally:
        runtime.close()
        provider.close()
