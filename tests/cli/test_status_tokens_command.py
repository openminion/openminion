from __future__ import annotations

import json
from argparse import Namespace
from pathlib import Path
from types import SimpleNamespace

import pytest

from openminion.base.config import OpenMinionConfig
from openminion.cli.commands.status.session_store import build_status_session_store
from openminion.cli.commands.status.tokens import run_tokens_status
from openminion.cli.parser.base import build_parser
from openminion.modules.session.storage.sqlite_store import SQLiteSessionStore


def _args(
    *,
    session_id: str,
    run_id: str = "",
    event_limit: int | None = None,
    as_json: bool = False,
) -> Namespace:
    return Namespace(
        config="",
        session_id=session_id,
        run_id=run_id,
        event_limit=event_limit,
        json=as_json,
    )


def test_status_tokens_parser_registration() -> None:
    args = build_parser().parse_args(
        ["status", "tokens", "--session-id", "session-1", "--event-limit", "3"]
    )

    assert args.status_command == "tokens"
    assert args.session_id == "session-1"
    assert args.event_limit == 3


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
            _args(session_id=session_id, event_limit=1),
            config=OpenMinionConfig(),
        )
        == 0
    )
    output = capsys.readouterr().out
    assert "complete=no" in output
    assert "incomplete: event_limit=1" in output


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
        "openminion.cli.commands.status.session_store.load_cli_manager",
        lambda _path: manager,
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
