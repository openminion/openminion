from __future__ import annotations

import json
from pathlib import Path

from tests.e2e.runners.run_cli_chat_probe import (
    _build_summary,
    _config_has_unset_runtime_env,
    _default_probe_data_root,
    _expand_probe_messages,
    _inferred_dispatch_sites,
    _latest_prompt_requires_confirmation,
    main,
    _parse_probe_status,
    _shutdown_timeout_can_count_as_success,
    _turn_response_boundary_detected,
)

import pytest

pytestmark = pytest.mark.e2e


def test_parse_probe_status_extracts_phase_and_exit_code() -> None:
    output = "hello\n[probe-status] phase=shutdown_timeout exit_code=124\n"
    assert _parse_probe_status(output) == {
        "phase": "shutdown_timeout",
        "exit_code": 124,
    }


def test_shutdown_timeout_can_count_as_success_only_after_exit_prompt() -> None:
    assert (
        _shutdown_timeout_can_count_as_success(
            "chat ready\n[s|a] you> hello\n[s|a] a: done\n[s|a] you> /exit\n"
        )
        is True
    )
    assert (
        _shutdown_timeout_can_count_as_success(
            "chat ready\n[s|a] you> hello\n[s|a] a: done\n"
        )
        is False
    )


def test_default_probe_data_root_is_session_isolated(tmp_path: Path) -> None:
    assert (
        _default_probe_data_root(
            home_root=tmp_path,
            session_id="abc123",
        )
        == (
            tmp_path
            / ".openminion"
            / "runtime"
            / "cli-chat-e2e"
            / "data-roots"
            / "abc123"
        ).resolve()
    )


def test_expand_probe_messages_preserves_multiline_prompt_before_slash_commands() -> (
    None
):
    assert _expand_probe_messages(["hello\n/debug\n/exit\n", "", "next line"]) == [
        "hello",
        "/debug",
        "/exit",
        "next line",
    ]


def test_expand_probe_messages_flattens_multiline_prompt_into_one_turn() -> None:
    assert _expand_probe_messages(
        [
            "Do this checklist:\n"
            "1. Read pyproject.toml\n"
            "[project.scripts]\n"
            'task-summary = "task_summary.report:cli"\n'
            "/debug\n"
            "/exit\n"
        ]
    ) == [
        (
            "Do this checklist: 1. Read pyproject.toml [project.scripts] "
            'task-summary = "task_summary.report:cli"'
        ),
        "/debug",
        "/exit",
    ]


def test_turn_response_boundary_ignores_typeahead_prompt_until_done() -> None:
    assert not _turn_response_boundary_detected(
        "❯ hello\nhello\n\n❯ Commands are unavailable while a turn is running."
    )
    assert _turn_response_boundary_detected("⏺ done\nDone in 3s\n\n❯ ")


def test_turn_response_boundary_accepts_inline_approval_prompt() -> None:
    screen = 'Approval required: file.write("README.md")\n[y]es / [N]o / [a]lways: '

    assert _turn_response_boundary_detected(screen)
    assert _latest_prompt_requires_confirmation("", screen)


def test_inferred_dispatch_sites_maps_coding_and_research_routes() -> None:
    coding = _inferred_dispatch_sites(
        bootstrap_events=[{"resolved_act_profile": "coding"}],
        execution_status_events=[{"route": "act_loop_adaptive"}],
    )
    research = _inferred_dispatch_sites(
        bootstrap_events=[{"resolved_act_profile": "research"}],
        execution_status_events=[{"route": "act_profile_research"}],
    )

    assert coding == ["adaptive.py: coding profile branch via act_loop_adaptive"]
    assert research == ["execution/dispatch.py + bootstrap/resolve.py -> ResearchMode"]


def test_build_summary_collects_typed_strategy_fields() -> None:
    events = [
        {
            "type": "brain.entry.path_detected",
            "payload": {
                "path": "act",
                "bootstrap_act_profile": "coding",
                "bootstrap_execution_target_kind": "local",
            },
        },
        {
            "type": "brain.act.bootstrap",
            "payload": {
                "resolved_act_profile": "coding",
                "resolved_execution_target_kind": "local",
                "source": "config_default_act_profile",
            },
        },
        {
            "type": "brain.execution_status",
            "payload": {
                "route": "act_loop_adaptive",
                "status_key": "working",
            },
        },
        {
            "type": "brain.mode.telemetry",
            "payload": {
                "coding.plan_phases_executed": [
                    "explore",
                    "plan",
                    "implement",
                    "verify",
                ],
                "coding.tool_calls_parallel": 2,
            },
        },
        {
            "type": "brain.research.progress",
            "payload": {"resume_count": 1, "last_checkpoint_id": "cp-1"},
        },
    ]
    audit_rows = [
        {
            "event": "tool.requested",
            "tool_name": "file.read",
            "act_profile": "coding",
            "execution_target": "local",
        }
    ]

    summary = _build_summary(
        session_id="bsev-smoke",
        transcript_path=Path("/tmp/transcript.txt"),
        events_path=Path("/tmp/events.json"),
        output="[probe-status] phase=shutdown_timeout exit_code=124\n",
        events=events,
        audit_paths=["/tmp/audit.jsonl"],
        audit_rows=audit_rows,
        event_session_id="bsev-smoke::conv:abc",
    )

    assert summary["observed_act_profiles"] == ["coding"]
    assert summary["observed_routes"] == ["act_loop_adaptive"]
    assert summary["tool_names"] == ["file.read"]
    assert summary["tool_failure_count"] == 0
    assert summary["coding_payload_hits"] == [
        {
            "type": "brain.mode.telemetry",
            "payload": {
                "coding.plan_phases_executed": [
                    "explore",
                    "plan",
                    "implement",
                    "verify",
                ],
                "coding.tool_calls_parallel": 2,
            },
        }
    ]
    assert summary["resume_markers"] == [
        {
            "type": "brain.research.progress",
            "payload": {"resume_count": 1, "last_checkpoint_id": "cp-1"},
        }
    ]


def test_build_summary_collects_coding_payloads_and_resume_markers_from_execution_status() -> (
    None
):
    events = [
        {
            "type": "brain.entry.path_detected",
            "payload": {
                "path": "act",
                "bootstrap_act_profile": "coding",
                "bootstrap_execution_target_kind": "local",
            },
        },
        {
            "type": "brain.act.bootstrap",
            "payload": {
                "resolved_act_profile": "coding",
                "resolved_execution_target_kind": "local",
                "source": "config_default_act_profile",
            },
        },
        {
            "type": "brain.execution_status",
            "payload": {
                "route": "act_loop_adaptive",
                "status_key": "working",
                "coding.plan_phases_executed": ["implement", "verify"],
                "coding.current_phase": "verify",
                "resume_count": 1,
                "last_checkpoint_id": "coding-ckpt-1",
            },
        },
    ]

    summary = _build_summary(
        session_id="coding-probe",
        transcript_path=Path("/tmp/transcript.txt"),
        events_path=Path("/tmp/events.json"),
        output="[probe-status] phase=shutdown_timeout exit_code=124\n",
        events=events,
        audit_paths=[],
        audit_rows=[],
        event_session_id="coding-probe::conv:abc",
    )

    assert summary["coding_payload_hits"] == [
        {
            "type": "brain.execution_status",
            "payload": {
                "coding.plan_phases_executed": ["implement", "verify"],
                "coding.current_phase": "verify",
            },
        }
    ]
    assert summary["resume_markers"] == [
        {
            "type": "brain.execution_status",
            "payload": {
                "resume_count": 1,
                "last_checkpoint_id": "coding-ckpt-1",
            },
        }
    ]


def test_config_has_unset_runtime_env_detects_placeholder_keys(
    tmp_path: Path,
) -> None:
    config_path = tmp_path / "profile.json"
    config_path.write_text(
        json.dumps(
            {
                "runtime": {
                    "env": {
                        "MINIMAX_API_KEY": "__SET_ME__",
                        "ALREADY_SET": "__SET_ME__",
                        "SAFE_VALUE": "literal",
                    }
                }
            }
        ),
        encoding="utf-8",
    )

    missing = _config_has_unset_runtime_env(
        config_path,
        environ={"ALREADY_SET": "present"},
    )

    assert missing == ("MINIMAX_API_KEY",)


def test_main_writes_preflight_artifacts_for_missing_live_provider_env(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    config_path = tmp_path / "profile.json"
    output_path = tmp_path / "transcript.txt"
    events_path = tmp_path / "events.json"
    summary_path = tmp_path / "summary.json"
    config_path.write_text(
        json.dumps({"runtime": {"env": {"MINIMAX_API_KEY": "__SET_ME__"}}}),
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("MINIMAX_API_KEY", raising=False)
    monkeypatch.setenv("OPENMINION_HOME", str(tmp_path))
    monkeypatch.setattr(
        "sys.argv",
        [
            "run_cli_chat_probe.py",
            "--config",
            str(config_path),
            "--agent",
            "minimax-m2-7",
            "--session",
            "probe-preflight",
            "--message",
            "plan a coding task",
            "--output",
            str(output_path),
            "--events-output",
            str(events_path),
            "--summary-output",
            str(summary_path),
        ],
    )

    exit_code = main()
    captured = capsys.readouterr()
    summary = json.loads(summary_path.read_text(encoding="utf-8"))

    assert exit_code == 2
    assert "missing live provider env for config" in captured.out
    assert "[probe-status] phase=config_env_missing exit_code=2" in captured.out
    assert output_path.read_text(encoding="utf-8") == captured.out
    assert json.loads(events_path.read_text(encoding="utf-8")) == []
    assert summary["probe_status"] == {"phase": "config_env_missing", "exit_code": 2}
    assert summary["tool_event_count"] == 0
