from __future__ import annotations

import json
from argparse import Namespace
from pathlib import Path
from types import SimpleNamespace

import pytest

from openminion.base.config import OpenMinionConfig
from openminion.cli.commands.status.session_store import build_status_session_store
from openminion.cli.commands.status.token_report import token_rollup_json_payload
from openminion.cli.commands.status.tokens import run_tokens_status
from openminion.cli.parser.base import build_parser
from openminion.modules.session.storage.sqlite_store import SQLiteSessionStore
from openminion.modules.telemetry.usage import (
    TokenUsageCoverage,
    TokenUsageRecord,
    TokenUsageSummary,
)
from openminion.modules.telemetry.usage.coverage import TokenUsageDimensionCoverage
from openminion.modules.telemetry.usage.token_usage import SURFACE_LLM_TOTAL

_ROLLUP_FIXTURE_PATH = (
    Path(__file__).resolve().parents[1]
    / "telemetry"
    / "fixtures"
    / "token_usage"
    / "openminion_token_usage_rollup_v1.json"
)


def _args(
    *,
    session_id: str,
    run_id: str = "",
    recent: int | None = None,
    event_limit: int | None = None,
    as_json: bool = False,
    only_warnings: bool = False,
) -> Namespace:
    return Namespace(
        config="",
        session_id=session_id,
        run_id=run_id,
        recent=recent,
        event_limit=event_limit,
        json=as_json,
        only_warnings=only_warnings,
    )


def test_status_tokens_parser_registration() -> None:
    args = build_parser().parse_args(
        ["status", "tokens", "--session-id", "session-1", "--event-limit", "3"]
    )

    assert args.status_command == "tokens"
    assert args.session_id == "session-1"
    assert args.event_limit == 3


def test_status_tokens_parser_accepts_recent_rollup() -> None:
    args = build_parser().parse_args(
        ["status", "tokens", "--recent", "5", "--only-warnings"]
    )

    assert args.status_command == "tokens"
    assert args.recent == 5
    assert args.only_warnings is True


def test_status_tokens_parser_defaults_to_latest_session() -> None:
    args = build_parser().parse_args(["status", "tokens"])

    assert args.status_command == "tokens"
    assert args.session_id == ""


def test_status_tokens_json_is_raw_versioned_envelope(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    store = SQLiteSessionStore(tmp_path / "tokens.db")
    session_id = store.create_session(
        initial_agent_id="agent.main", profile_version="v1"
    )
    store.append_event(
        session_id,
        event_type="llm.call.completed",
        payload={
            "provider": "openai",
            "model": "gpt-test",
            "usage": {"input_tokens": 4, "output_tokens": 2, "total_tokens": 6},
        },
    )
    monkeypatch.setattr(
        "openminion.cli.commands.status.tokens.build_status_session_store",
        lambda _args, _config: store,
    )

    code = run_tokens_status(
        _args(session_id=session_id, as_json=True),
        config=OpenMinionConfig(),
    )

    payload = json.loads(capsys.readouterr().out)
    assert code == 0
    assert payload["schema_version"] == "openminion.token_usage.v1"
    assert "ok" not in payload
    assert payload["totals"]["provider_tokens"] == 6
    assert payload["coverage"]["input_tokens"]["reported"] == 1
    assert payload["coverage"]["cache_read_tokens"]["missing"] == 1


def test_status_tokens_text_reports_empty_and_incomplete_states(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    store = SQLiteSessionStore(tmp_path / "tokens-text.db")
    session_id = store.create_session(
        initial_agent_id="agent.main", profile_version="v1"
    )
    monkeypatch.setattr(
        "openminion.cli.commands.status.tokens.build_status_session_store",
        lambda _args, _config: store,
    )

    assert (
        run_tokens_status(
            _args(session_id=session_id),
            config=OpenMinionConfig(),
        )
        == 0
    )
    assert "no token usage events" in capsys.readouterr().out

    store = SQLiteSessionStore(tmp_path / "tokens-incomplete.db")
    session_id = store.create_session(
        initial_agent_id="agent.main", profile_version="v1"
    )
    for value in (1, 2):
        store.append_event(
            session_id,
            event_type="llm.call.completed",
            payload={"usage": {"input_tokens": value}},
        )
    monkeypatch.setattr(
        "openminion.cli.commands.status.tokens.build_status_session_store",
        lambda _args, _config: store,
    )

    assert (
        run_tokens_status(
            _args(session_id=session_id, event_limit=2),
            config=OpenMinionConfig(),
        )
        == 0
    )
    output = capsys.readouterr().out
    assert "complete=no" in output
    assert "coverage: llm_calls=1" in output
    assert "input=1/1" in output
    assert "incomplete: event_limit=2" in output
    assert "[event_window_limited]" in output


def test_status_tokens_uses_latest_session_when_session_id_is_omitted(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    store = SQLiteSessionStore(tmp_path / "tokens-latest.db")
    old_session = store.create_session(
        initial_agent_id="agent.main", profile_version="v1", session_id="session-old"
    )
    latest_session = store.create_session(
        initial_agent_id="agent.main", profile_version="v1", session_id="session-new"
    )
    store.append_event(
        old_session,
        event_type="llm.call.completed",
        payload={"usage": {"input_tokens": 1, "output_tokens": 1, "total_tokens": 2}},
    )
    store.append_event(
        latest_session,
        event_type="llm.call.completed",
        payload={"usage": {"input_tokens": 4, "output_tokens": 2, "total_tokens": 6}},
    )
    monkeypatch.setattr(
        "openminion.cli.commands.status.tokens.build_status_session_store",
        lambda _args, _config: store,
    )

    code = run_tokens_status(
        _args(session_id=""),
        config=OpenMinionConfig(),
    )

    output = capsys.readouterr().out
    assert code == 0
    assert "session=session-new" in output
    assert "provider=6" in output


def test_status_tokens_run_id_can_resolve_session_when_session_id_is_omitted(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    store = SQLiteSessionStore(tmp_path / "tokens-run-only.db")
    session_id = store.create_session(
        initial_agent_id="agent.main", profile_version="v1", session_id="session-run"
    )
    run_id = store.create_run_record(session_id, run_type="llm", run_id="run-1")
    store.finish_run_record(run_id, status="completed")
    store.append_event(
        session_id,
        event_type="llm.call.completed",
        payload={
            "run_id": run_id,
            "usage": {"input_tokens": 4, "output_tokens": 2, "total_tokens": 6},
        },
    )
    monkeypatch.setattr(
        "openminion.cli.commands.status.tokens.build_status_session_store",
        lambda _args, _config: store,
    )

    code = run_tokens_status(
        _args(session_id="", run_id=run_id),
        config=OpenMinionConfig(),
    )

    output = capsys.readouterr().out
    assert code == 0
    assert "session=session-run run=run-1" in output
    assert "provider=6" in output


def test_status_tokens_text_separates_provider_and_derived_totals(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    store = SQLiteSessionStore(tmp_path / "tokens-provenance.db")
    session_id = store.create_session(
        initial_agent_id="agent.main", profile_version="v1"
    )
    for usage in (
        {"input_tokens": 4, "output_tokens": 2, "total_tokens": 7},
        {
            "input_tokens": 3,
            "output_tokens": 1,
            "total_tokens": 4,
            "total_source": "derived",
        },
    ):
        store.append_event(
            session_id,
            event_type="llm.call.completed",
            payload={"usage": usage},
        )
    monkeypatch.setattr(
        "openminion.cli.commands.status.tokens.build_status_session_store",
        lambda _args, _config: store,
    )

    assert (
        run_tokens_status(
            _args(session_id=session_id),
            config=OpenMinionConfig(),
        )
        == 0
    )

    assert (
        "totals: provider=7 derived=4 input=7 output=3 cache_read=0 cache_write=0"
    ) in capsys.readouterr().out


def test_status_tokens_text_shows_insights_and_navigation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    store = SQLiteSessionStore(tmp_path / "tokens-insights.db")
    session_id = store.create_session(
        initial_agent_id="agent.main", profile_version="v1"
    )
    store.append_event(
        session_id,
        event_type="llm.call.completed",
        payload={
            "provider": "openai",
            "model": "gpt-test",
            "usage": {
                "input_tokens": 8,
                "output_tokens": 3,
                "total_tokens": 11,
                "cached_tokens": 5,
            },
        },
    )
    store.append_event(
        session_id,
        event_type="context.manifest.created",
        payload={
            "used_tokens": 20,
            "buckets": {
                "recent_window": {"used_tokens": 12},
                "retrieval": {"used_tokens": 8},
            },
        },
    )
    monkeypatch.setattr(
        "openminion.cli.commands.status.tokens.build_status_session_store",
        lambda _args, _config: store,
    )

    code = run_tokens_status(
        _args(session_id=session_id),
        config=OpenMinionConfig(),
    )

    output = capsys.readouterr().out
    assert code == 0
    assert "insights: top_model=openai/gpt-test 11" in output
    assert "by surface: context_pack=20, llm_total=11" in output
    assert "context buckets: recent_window=12, retrieval=8" in output
    assert "coverage health: llm_calls=1" in output
    assert "breakdown:" in output
    assert "next: add `--run-id <run-id>`" in output


def test_status_tokens_recent_rollup_shows_cross_session_insights(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    store = SQLiteSessionStore(tmp_path / "tokens-recent.db")
    first_session = store.create_session(
        initial_agent_id="agent.main", profile_version="v1", session_id="session-a"
    )
    second_session = store.create_session(
        initial_agent_id="agent.main", profile_version="v1", session_id="session-b"
    )
    store.append_event(
        first_session,
        event_type="llm.call.completed",
        payload={
            "provider": "openai",
            "model": "gpt-a",
            "usage": {"input_tokens": 4, "output_tokens": 2, "total_tokens": 6},
        },
    )
    store.append_event(
        second_session,
        event_type="context.manifest.created",
        payload={"used_tokens": 20, "buckets": {"retrieval": {"used_tokens": 20}}},
    )
    monkeypatch.setattr(
        "openminion.cli.commands.status.tokens.build_status_session_store",
        lambda _args, _config: store,
    )

    code = run_tokens_status(
        _args(session_id="", recent=2),
        config=OpenMinionConfig(),
    )

    output = capsys.readouterr().out
    assert code == 0
    assert "recent_sessions=2" in output
    assert "with_usage=2" in output
    assert "context_estimated=20" in output
    assert "top sessions:" in output
    assert "provider coverage: openai/gpt-a=records:1 provider:6" in output
    assert "coverage health:" in output
    assert "[context_dominates] context packing dominates recent usage" in output
    assert "drilldown: `openminion status tokens --session-id session-b`" in output


def test_status_tokens_recent_json_wraps_raw_session_envelopes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    store = SQLiteSessionStore(tmp_path / "tokens-recent-json.db")
    session_id = store.create_session(
        initial_agent_id="agent.main", profile_version="v1", session_id="session-json"
    )
    store.append_event(
        session_id,
        event_type="llm.call.completed",
        payload={
            "provider": "openai",
            "model": "gpt-json",
            "usage": {"input_tokens": 3, "output_tokens": 2, "total_tokens": 5},
        },
    )
    monkeypatch.setattr(
        "openminion.cli.commands.status.tokens.build_status_session_store",
        lambda _args, _config: store,
    )

    code = run_tokens_status(
        _args(session_id="", recent=1, as_json=True),
        config=OpenMinionConfig(),
    )

    payload = json.loads(capsys.readouterr().out)
    assert code == 0
    assert payload["schema_version"] == "openminion.token_usage_rollup.v1"
    assert payload["session_count"] == 1
    assert payload["input_session_count"] == 1
    assert payload["only_warnings"] is False
    assert payload["totals"]["provider_tokens"] == 5
    assert payload["coverage"]["llm_call_events"] == 1
    assert payload["provider_coverage"] == [
        {
            "provider": "openai",
            "model": "gpt-json",
            "llm_total_records": 1,
            "provider_total_records": 1,
            "derived_total_records": 0,
            "provider_tokens": 5,
            "derived_tokens": 0,
            "input_tokens": 3,
            "output_tokens": 2,
            "cache_read_tokens": 0,
            "cache_write_tokens": 0,
        }
    ]
    advisory_codes = {advisory["code"] for advisory in payload["advisories"]}
    assert "missing_call_correlation" in advisory_codes
    assert payload["summaries"][0]["schema_version"] == "openminion.token_usage.v1"
    assert payload["summaries"][0]["totals"]["provider_tokens"] == 5
    assert "ok" not in payload


def test_status_tokens_recent_json_matches_rollup_fixture() -> None:
    summary = TokenUsageSummary(
        session_id="session-rollup-fixture",
        records=(
            TokenUsageRecord(
                session_id="session-rollup-fixture",
                run_id="run-rollup-fixture",
                llm_call_id="call-provider",
                provider="openai",
                model="gpt-fixture",
                surface=SURFACE_LLM_TOTAL,
                source_event_type="llm.call.completed",
                source_event_id="event-provider",
                total_tokens=10,
                total_source="provider",
                input_tokens=6,
                output_tokens=4,
                cache_read_tokens=2,
            ),
            TokenUsageRecord(
                session_id="session-rollup-fixture",
                run_id="run-rollup-fixture",
                llm_call_id="call-derived",
                provider="openai",
                model="gpt-fixture",
                surface=SURFACE_LLM_TOTAL,
                source_event_type="llm.call.completed",
                source_event_id="event-derived",
                total_tokens=5,
                total_source="derived",
                input_tokens=3,
                output_tokens=2,
            ),
        ),
        source_event_count=2,
        events_scanned=2,
        coverage=TokenUsageCoverage(
            llm_call_events=2,
            provider_identified_llm_call_events=2,
            model_identified_llm_call_events=2,
            run_id_present_events=2,
            trace_id_present_events=0,
            llm_call_id_present_events=2,
            input_tokens=TokenUsageDimensionCoverage(reported=2),
            output_tokens=TokenUsageDimensionCoverage(reported=2),
            total_tokens=TokenUsageDimensionCoverage(reported=1, missing=1),
            cache_read_tokens=TokenUsageDimensionCoverage(reported=1, missing=1),
            cache_write_tokens=TokenUsageDimensionCoverage(missing=2),
        ),
    )

    payload = token_rollup_json_payload((summary,), input_session_count=1)
    expected = json.loads(_ROLLUP_FIXTURE_PATH.read_text(encoding="utf-8"))

    assert payload == expected


def test_status_tokens_recent_only_warnings_filters_clean_sessions(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    store = SQLiteSessionStore(tmp_path / "tokens-recent-warnings.db")
    clean_session = store.create_session(
        initial_agent_id="agent.main", profile_version="v1", session_id="session-clean"
    )
    warning_session = store.create_session(
        initial_agent_id="agent.main",
        profile_version="v1",
        session_id="session-warning",
    )
    store.append_event(
        clean_session,
        event_type="llm.call.completed",
        trace_id="trace-clean",
        payload={
            "run_id": "run-clean",
            "llm_call_id": "call-clean",
            "provider": "openai",
            "model": "gpt-clean",
            "usage": {"input_tokens": 4, "output_tokens": 2, "total_tokens": 6},
        },
    )
    store.append_event(
        warning_session,
        event_type="llm.call.completed",
        payload={"usage": {"input_tokens": 5, "output_tokens": 2}},
    )
    monkeypatch.setattr(
        "openminion.cli.commands.status.tokens.build_status_session_store",
        lambda _args, _config: store,
    )

    code = run_tokens_status(
        _args(session_id="", recent=2, only_warnings=True),
        config=OpenMinionConfig(),
    )

    output = capsys.readouterr().out
    assert code == 0
    assert "filtered=warnings input_sessions=2" in output
    assert "session-warning" in output
    assert "session-clean" not in output
    assert "[derived_total_tokens]" in output
    assert "warning sessions: session-warning=" in output


def test_status_tokens_recent_only_warnings_json_filters_clean_sessions(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    store = SQLiteSessionStore(tmp_path / "tokens-recent-warnings-json.db")
    clean_session = store.create_session(
        initial_agent_id="agent.main", profile_version="v1", session_id="session-clean"
    )
    warning_session = store.create_session(
        initial_agent_id="agent.main",
        profile_version="v1",
        session_id="session-warning",
    )
    store.append_event(
        clean_session,
        event_type="llm.call.completed",
        trace_id="trace-clean",
        payload={
            "run_id": "run-clean",
            "llm_call_id": "call-clean",
            "provider": "openai",
            "model": "gpt-clean",
            "usage": {"input_tokens": 4, "output_tokens": 2, "total_tokens": 6},
        },
    )
    store.append_event(
        warning_session,
        event_type="llm.call.completed",
        payload={"usage": {"input_tokens": 5, "output_tokens": 2}},
    )
    monkeypatch.setattr(
        "openminion.cli.commands.status.tokens.build_status_session_store",
        lambda _args, _config: store,
    )

    code = run_tokens_status(
        _args(session_id="", recent=2, as_json=True, only_warnings=True),
        config=OpenMinionConfig(),
    )

    payload = json.loads(capsys.readouterr().out)
    assert code == 0
    assert payload["session_count"] == 1
    assert payload["input_session_count"] == 2
    assert payload["only_warnings"] is True
    assert payload["summaries"][0]["session_id"] == "session-warning"
    assert payload["advisories"][0]["code"] == "derived_total_tokens"


def test_status_tokens_run_output_shows_outcome_and_friction(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    store = SQLiteSessionStore(tmp_path / "tokens-outcome.db")
    session_id = store.create_session(
        initial_agent_id="agent.main", profile_version="v1", session_id="session-run"
    )
    run_id = store.create_run_record(
        session_id,
        run_type="llm",
        run_id="run-1",
        meta={"request_id": "turn-1"},
    )
    store.finish_run_record(run_id, status="completed")
    store.append_event(
        session_id,
        event_type="llm.call.completed",
        trace_id="turn-1",
        payload={
            "run_id": run_id,
            "llm_call_id": "call-1",
            "provider": "openai",
            "model": "gpt-test",
            "purpose": "answer",
            "usage": {"input_tokens": 5, "output_tokens": 3, "total_tokens": 8},
        },
    )
    store.append_event(
        session_id,
        event_type="llm.call.retry",
        trace_id="turn-1",
        payload={"run_id": run_id},
    )
    store.append_event(
        session_id,
        event_type="tool.request",
        trace_id="turn-1",
        payload={"run_id": run_id, "tool_name": "search"},
    )
    store.append_event(
        session_id,
        event_type="turn.completed",
        trace_id="turn-1",
        payload={"run_id": run_id, "task_success": False},
    )
    monkeypatch.setattr(
        "openminion.cli.commands.status.tokens.build_status_session_store",
        lambda _args, _config: store,
    )

    code = run_tokens_status(
        _args(session_id=session_id, run_id=run_id),
        config=OpenMinionConfig(),
    )

    output = capsys.readouterr().out
    assert code == 0
    assert "outcome: provider_calls=1" in output
    assert "retries=1" in output
    assert "tools=1" in output
    assert "success=no" in output
    assert "provider retries increased token friction" in output
    assert "[provider_retry_friction]" in output
    assert "token spend ended with a negative outcome signal" in output


def test_status_tokens_rejects_cross_session_run(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = SQLiteSessionStore(tmp_path / "tokens-run.db")
    requested_session = store.create_session(
        initial_agent_id="agent.main", profile_version="v1"
    )
    run_session = store.create_session(
        initial_agent_id="agent.main", profile_version="v1"
    )
    run_id = store.create_run_record(run_session, run_type="llm", run_id="run-1")
    store.finish_run_record(run_id, status="completed")
    monkeypatch.setattr(
        "openminion.cli.commands.status.tokens.build_status_session_store",
        lambda _args, _config: store,
    )

    with pytest.raises(RuntimeError, match="does not belong"):
        run_tokens_status(
            _args(session_id=requested_session, run_id=run_id),
            config=OpenMinionConfig(),
        )


def test_status_tokens_rejects_non_positive_event_limit() -> None:
    with pytest.raises(RuntimeError, match="greater than zero"):
        run_tokens_status(
            _args(session_id="session-1", event_limit=0),
            config=OpenMinionConfig(),
        )


def test_status_tokens_rejects_recent_with_run_id() -> None:
    with pytest.raises(RuntimeError, match="cannot be combined"):
        run_tokens_status(
            _args(session_id="", run_id="run-1", recent=3),
            config=OpenMinionConfig(),
        )


def test_status_tokens_rejects_recent_with_session_id() -> None:
    with pytest.raises(RuntimeError, match="cannot be combined"):
        run_tokens_status(
            _args(session_id="session-1", recent=3),
            config=OpenMinionConfig(),
        )


def test_status_tokens_rejects_only_warnings_without_recent() -> None:
    with pytest.raises(RuntimeError, match="requires --recent"):
        run_tokens_status(
            _args(session_id="session-1", only_warnings=True),
            config=OpenMinionConfig(),
        )


def test_session_store_factory_uses_configured_record_backend(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager = SimpleNamespace(
        env=SimpleNamespace(snapshot=lambda: {}),
        home_root=tmp_path / "home",
        data_root=tmp_path / "data",
    )
    captured = {}
    sentinel = object()
    monkeypatch.setattr(
        "openminion.cli.commands.status.session_store.load_cli_manager_from_args",
        lambda _args: manager,
    )

    def _build(**kwargs):
        captured.update(kwargs)
        return sentinel

    monkeypatch.setattr(
        "openminion.cli.commands.status.session_store.build_module_session_store",
        _build,
    )
    config = OpenMinionConfig()
    config.storage.path = str(tmp_path / "openminion.db")
    config.storage.backend = "postgres"
    config.storage.postgres_url = "postgresql://example.invalid/openminion"

    result = build_status_session_store(Namespace(config=""), config)

    assert result is sentinel
    assert captured["config"].record_backend == "record.postgres"
    assert captured["config"].record_backend_options["url"].startswith("postgresql://")
