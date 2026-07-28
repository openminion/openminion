from __future__ import annotations

import json
import os
from argparse import Namespace
from types import SimpleNamespace

import pytest

from openminion.base.config import OpenMinionConfig
from openminion.cli.commands.status.context_trace import run_context_trace_status
from openminion.cli.parser.base import build_parser


class _SessionStore:
    def __init__(self, events: list[dict] | None = None) -> None:
        self._events = list(events or [])
        self.closed = False

    def get_session(self, session_id: str):
        return SimpleNamespace(id=session_id) if session_id != "missing" else None

    def list_events(self, session_id: str, *, event_type: str, limit: int, **kwargs):
        del session_id, kwargs
        return [
            event for event in self._events[:limit] if event["event_type"] == event_type
        ]

    def close(self) -> None:
        self.closed = True


def _args(
    *,
    session_id: str,
    as_json: bool = False,
    review: bool = False,
    canary: str = "",
    calibration: str = "",
    artifacts_dir: str = "",
) -> Namespace:
    return Namespace(
        config="",
        session_id=session_id,
        turn_id="",
        limit=50,
        json=as_json,
        review=review,
        canary=canary,
        calibration=calibration,
        artifacts_dir=artifacts_dir,
    )


def _trace_event() -> dict:
    return {
        "id": "evt-1",
        "event_type": "context.manifest.created",
        "created_at": "2026-07-17T00:00:00Z",
        "payload": {
            "decision_trace": {
                "trace_version": "context-decision.v1",
                "session_id": "sess-1",
                "turn_id": "turn-1",
                "pack_version": "pack-1",
                "persistence_status": "persisted",
                "decisions": [
                    {
                        "segment_id": "retrieval:1",
                        "bucket": "retrieval",
                        "action": "included",
                        "reason_code": "selected",
                    }
                ],
            }
        },
    }


def test_status_context_trace_parser_registration() -> None:
    args = build_parser().parse_args(
        ["status", "context-trace", "--session", "session-1", "--turn", "turn-1"]
    )

    assert args.status_command == "context-trace"
    assert args.session_id == "session-1"
    assert args.turn_id == "turn-1"


def test_status_context_trace_review_parser_registration() -> None:
    args = build_parser().parse_args(
        [
            "status",
            "context-trace",
            "--session",
            "session-1",
            "--review",
            "--canary",
            "canary.json",
            "--calibration",
            "calibration.json",
            "--artifacts-dir",
            "artifacts",
        ]
    )

    assert args.review is True
    assert args.canary == "canary.json"
    assert args.calibration == "calibration.json"
    assert args.artifacts_dir == "artifacts"


def test_status_context_trace_json_output(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    store = _SessionStore([_trace_event()])
    monkeypatch.setattr(
        "openminion.cli.commands.status.context_trace.build_status_session_store",
        lambda _args, _config: store,
    )

    code = run_context_trace_status(
        _args(session_id="sess-1", as_json=True),
        config=OpenMinionConfig(),
    )

    payload = json.loads(capsys.readouterr().out)
    assert code == 0
    assert payload["ok"] is True
    assert payload["count"] == 1
    assert (
        payload["traces"][0]["decision_trace"]["decisions"][0]["action"] == "included"
    )
    assert store.closed is True


def test_status_context_trace_review_output_redacts_sensitive_values(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    event = _trace_event()
    event["payload"]["decision_trace"]["decisions"].append(
        {
            "segment_id": "secret-token-segment",
            "bucket": "retrieval",
            "action": "dropped",
            "reason_code": "password_budget",
        }
    )
    monkeypatch.setattr(
        "openminion.cli.commands.status.context_trace.build_status_session_store",
        lambda _args, _config: _SessionStore([event]),
    )

    code = run_context_trace_status(
        _args(session_id="sess-1", review=True),
        config=OpenMinionConfig(),
    )

    output = capsys.readouterr().out
    assert code == 0
    assert "context review:" in output
    assert "retrieval:1" in output
    assert "secret-token-segment" not in output
    assert "[redacted]" in output


def test_status_context_trace_review_json_includes_optional_degradation(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    canary = tmp_path / "canary.json"
    canary.write_text('{"report_version": "wrong.v1", "summary": {}}\n')
    monkeypatch.setattr(
        "openminion.cli.commands.status.context_trace.build_status_session_store",
        lambda _args, _config: _SessionStore([_trace_event()]),
    )

    code = run_context_trace_status(
        _args(session_id="sess-1", as_json=True, review=True, canary=str(canary)),
        config=OpenMinionConfig(),
    )

    payload = json.loads(capsys.readouterr().out)
    assert code == 0
    assert payload["review"]["schema_version"] == "memory-context-review.v1"
    assert payload["review"]["degraded_reasons"]


def test_status_context_trace_review_artifacts_dir_discovers_latest_reports(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    old_canary = tmp_path / "old-canary.json"
    new_canary = tmp_path / "new-canary.json"
    calibration = tmp_path / "context-budget-calibration.json"
    old_canary.write_text(
        json.dumps(
            {
                "report_version": "memory-context-operational-canary.v1",
                "summary": {"score": "old"},
            }
        )
    )
    new_canary.write_text(
        json.dumps(
            {
                "report_version": "memory-context-operational-canary.v1",
                "summary": {"score": "new"},
            }
        )
    )
    calibration.write_text(
        json.dumps(
            {
                "report_version": "context-budget-calibration.v1",
                "summary": {"recommendation": "keep"},
            }
        )
    )
    os.utime(old_canary, ns=(1, 1))
    os.utime(new_canary, ns=(2, 2))
    monkeypatch.setattr(
        "openminion.cli.commands.status.context_trace.build_status_session_store",
        lambda _args, _config: _SessionStore([_trace_event()]),
    )

    code = run_context_trace_status(
        _args(session_id="sess-1", review=True, artifacts_dir=str(tmp_path)),
        config=OpenMinionConfig(),
    )

    output = capsys.readouterr().out
    assert code == 0
    assert "- canary: score=new" in output
    assert "- calibration: recommendation=keep" in output


def test_status_context_trace_handles_malformed_event_payload(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    event = {"event_type": "context.manifest.created", "payload": "bad"}
    monkeypatch.setattr(
        "openminion.cli.commands.status.context_trace.build_status_session_store",
        lambda _args, _config: _SessionStore([event]),
    )

    code = run_context_trace_status(
        _args(session_id="sess-1", as_json=True, review=True),
        config=OpenMinionConfig(),
    )

    assert code == 1
    assert "CONTEXT_TRACE_NOT_FOUND" in capsys.readouterr().out


def test_status_context_trace_missing_trace_returns_nonzero(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(
        "openminion.cli.commands.status.context_trace.build_status_session_store",
        lambda _args, _config: _SessionStore([]),
    )

    code = run_context_trace_status(
        _args(session_id="sess-1"),
        config=OpenMinionConfig(),
    )

    assert code == 1
    assert "CONTEXT_TRACE_NOT_FOUND" in capsys.readouterr().out
