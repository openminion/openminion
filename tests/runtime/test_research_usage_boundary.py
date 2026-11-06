"""Research tool/budget settlement preserves #596's response-event usage owner."""

import json

from openminion.modules.brain.loop.strategies.research import RESEARCH_MODE
from openminion.modules.brain.schemas.agent import ModeProfileConfig
from openminion.modules.telemetry.usage import StatsService
from tests.runtime.test_turn_capture_authorization import _runtime, _call
from tests.runtime.test_whole_turn_usage import _UsageProvider


class _ResearchUsageProvider(_UsageProvider):
    def respond(self, body):
        specs = [item["function"] for item in body.get("tools", [])]
        names = [spec["name"] for spec in specs]
        if "respond" in names:
            response = _call("research", {})
            kind = "research-entry"
        elif any(
            spec["name"] == "submit_output"
            and "remaining_work" in spec.get("parameters", {}).get("properties", {})
            for spec in specs
        ):
            response = _call(
                "submit_output",
                {
                    "answer": "FIRST_FACT=731; SECOND_FACT=419",
                    "status": "complete",
                    "remaining_work": "",
                },
            )
            kind = "research-synthesis"
        else:
            return super().respond(body)
        self.requests.append(kind)
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


def test_http_research_preserves_whole_turn_usage_and_includes_child_tools(
    tmp_path, monkeypatch
):
    fixture = tmp_path / "fixture.txt"
    fixture.write_text("FIRST_FACT=731\n")
    fixture.with_name("second.txt").write_text("SECOND_FACT=419\n")
    provider = _ResearchUsageProvider(fixture)
    runtime = _runtime(tmp_path, monkeypatch, provider, constraint=None)
    try:
        runner = runtime.resolve_agent_service("openminion")._get_runner()
        runner.profile.mode_config[RESEARCH_MODE] = ModeProfileConfig(
            max_research_iterations=2
        )
        result = runtime.run_turn(
            payload={
                "message": "Research both local fixtures and summarize their facts.",
                "session_id": "research-usage",
            }
        )
        stats = result["stats"]
        assert "research-entry" in provider.requests
        assert "research-synthesis" in provider.requests
        assert provider.reads == stats["tool_calls"] == 2
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
        metadata = result["metadata"]
        assert int(metadata["tool_calls_count"]) == 2
        assert json.loads(metadata["run_stats_json"]) == stats
        counts = json.loads(metadata["llm_call_counts_by_purpose"])
        assert counts["act"] > 0 and counts["summarize"] == 1
        session_id = f"research-usage::conv:{metadata['thread_id']}"
        store = runner.session_api.store
        usage = StatsService(store).get_run_token_usage(
            result["run_id"], session_id=session_id
        )
        assert usage.total_input_tokens == stats["input_tokens"]
        assert usage.total_output_tokens == stats["output_tokens"]
        assert usage.total_cache_read_tokens == stats["cache_read_tokens"]
        assert usage.coverage.llm_call_events == stats["llm_calls"]
        events = store.get_events(session_id, types=["tool.call.completed"])
        assert (
            len({event["payload"]["call_id"] for event in events})
            == stats["tool_calls"]
        )
        outbound = runtime.sessions.list_messages(
            session_id="research-usage", limit=100
        )[-1]
        assert json.loads(outbound.metadata["run_stats_json"]) == stats
    finally:
        runtime.close()
        provider.close()
