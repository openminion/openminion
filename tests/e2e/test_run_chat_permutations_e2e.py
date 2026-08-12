from __future__ import annotations

import inspect
import json
from pathlib import Path

import tests.e2e.runners.run_chat_permutations_e2e as runner
from openminion.base.constants import (
    OPENMINION_DATA_ROOT_ENV,
    OPENMINION_GENERATED_ROOT_ENV,
)
from tests.e2e.runners.run_chat_permutations_e2e import (
    EXTERNAL_SERVICES_CONVERSATION,
    RELEASE_GATE_CONVERSATION,
    _build_conversation,
    _chat_subprocess_env,
    _conversation_messages,
    _conversation_workdir,
    _default_work_root,
    _external_service_prerequisite_reason,
    _latest_prompt_requires_confirmation,
    _ready_prompt_detected,
    _resolve_conversations,
    _scenario_data_root_parent,
    _transcript_has_known_failure,
    _turn_response_boundary_detected_since,
    _write_config,
)

import pytest

pytestmark = pytest.mark.e2e


def test_latest_prompt_requires_confirmation_when_new_prompt_contains_policy_gate() -> (
    None
):
    previous = "[session|agent] you> alpha\n"
    current = (
        previous
        + "[session|agent] agent: Policy confirmation required.\n"
        + "Reply exactly yes to confirm or exactly no to cancel.\n"
        + "[session|agent] you> "
    )
    assert _latest_prompt_requires_confirmation(previous, current) is True


def test_latest_prompt_requires_confirmation_ignores_old_confirmation_text() -> None:
    previous = (
        "[session|agent] agent: Policy confirmation required.\n[session|agent] you> "
    )
    current = previous + "[session|agent] agent: handled yes\n[session|agent] you> "
    assert _latest_prompt_requires_confirmation(previous, current) is False


def test_transcript_has_known_failure_detects_fail_closed_contracts() -> None:
    assert (
        _transcript_has_known_failure(
            "General act work ended without the required typed "
            "finalization_status contract."
        )
        is True
    )
    assert _transcript_has_known_failure("Adaptive loop stopped unexpectedly.") is True
    assert _transcript_has_known_failure("[chat] turn failed.") is True
    assert _transcript_has_known_failure("Denied by policy: path escaped") is True
    assert _transcript_has_known_failure("path escapes workspace root: /tmp/x") is True
    assert _transcript_has_known_failure("normal assistant response") is False


def test_conversation_messages_preserves_scripted_turns_without_auto_yes() -> None:
    messages = _conversation_messages(
        "Write to file /tmp/x the following content: hi\n"
        'tool run_command {"command":"pwd"}\n'
        "Read file /tmp/x\n"
    )
    assert messages == [
        "Write to file /tmp/x the following content: hi",
        'tool run_command {"command":"pwd"}',
        "Read file /tmp/x",
    ]


def test_conversation_messages_skips_runner_owned_commands() -> None:
    assert _conversation_messages("hello\n/debug\n/exit\n") == ["hello"]


def test_conversation_workdir_stays_under_artifact_work_root(tmp_path) -> None:
    workdir = _conversation_workdir(
        work_root=tmp_path / "workdirs",
        provider="minimax",
        model="MiniMax-M2.7",
        scenario="tool calling",
    )
    assert (
        workdir == tmp_path / "workdirs" / "minimax" / "MiniMax-M2.7" / "tool_calling"
    )


def test_build_conversation_uses_relative_workdir_reference(tmp_path: Path) -> None:
    template = tmp_path / "conversation.txt"
    template.write_text("Read {{WORKDIR}}/sample.txt\n", encoding="utf-8")

    conversation = _build_conversation(
        template,
        tmp_path / "long" / "absolute" / "workdir",
        skip_network=False,
    )

    assert conversation == "Read ./sample.txt\n"


def test_scenario_data_roots_live_beside_runner_artifacts(tmp_path) -> None:
    assert _scenario_data_root_parent(tmp_path / "logs") == tmp_path / "data-roots"


def test_chat_subprocess_env_overrides_inherited_runtime_roots(
    monkeypatch, tmp_path: Path
) -> None:
    data_root = tmp_path / "scenario-data"
    monkeypatch.setenv(OPENMINION_DATA_ROOT_ENV, str(tmp_path / "outer-data"))
    monkeypatch.setenv(OPENMINION_GENERATED_ROOT_ENV, str(tmp_path / "outer-runtime"))

    env = _chat_subprocess_env(data_root=data_root)

    assert env[OPENMINION_DATA_ROOT_ENV] == str(data_root)
    assert env[OPENMINION_GENERATED_ROOT_ENV] == str(data_root / "runtime")


def test_main_accepts_explicit_artifact_roots_with_stale_generated_env(
    monkeypatch, tmp_path: Path
) -> None:
    captured = {}
    conversation = tmp_path / "conversation.txt"
    conversation.write_text("hello\n", encoding="utf-8")
    monkeypatch.setenv(OPENMINION_DATA_ROOT_ENV, str(tmp_path / "outer-data"))
    monkeypatch.setenv(OPENMINION_GENERATED_ROOT_ENV, str(tmp_path / "stale-generated"))
    monkeypatch.setenv("OPENMINION_E2E_PROVIDERS", "echo")

    def _fake_run_chat(**kwargs):
        captured.update(kwargs)
        return True, "ok"

    monkeypatch.setattr(runner, "_run_chat", _fake_run_chat)

    assert (
        runner.main(
            [
                "--conversation",
                str(conversation),
                "--log-root",
                str(tmp_path / "logs"),
                "--config-root",
                str(tmp_path / "configs"),
                "--work-root",
                str(tmp_path / "workdirs"),
            ]
        )
        == 0
    )
    workdir = captured["workdir"]
    assert isinstance(workdir, Path)
    assert workdir == tmp_path / "workdirs" / "echo" / "echo" / "conversation"


def test_default_conversations_use_release_gate_fixture_only() -> None:
    assert _resolve_conversations(
        conversations=None,
        conversation_dir=None,
        include_edgecases=False,
        include_chaos=False,
        include_external_services=False,
        include_diagnostic=False,
    ) == [RELEASE_GATE_CONVERSATION]


def test_external_services_fixture_is_explicit_opt_in() -> None:
    assert _resolve_conversations(
        conversations=None,
        conversation_dir=None,
        include_edgecases=False,
        include_chaos=False,
        include_external_services=True,
        include_diagnostic=False,
    ) == [RELEASE_GATE_CONVERSATION, EXTERNAL_SERVICES_CONVERSATION]


def test_external_services_prerequisites_report_missing_key_and_browser(
    monkeypatch,
) -> None:
    monkeypatch.delenv("TINYFISH_API_KEY", raising=False)
    monkeypatch.delenv("PINCHTAB_URL", raising=False)
    monkeypatch.delenv("PINCHTAB_AUTOSTART", raising=False)

    reason = _external_service_prerequisite_reason(EXTERNAL_SERVICES_CONVERSATION)

    assert "missing_provider_key:TINYFISH_API_KEY" in reason
    assert "external_service_unavailable:PINCHTAB_URL/PINCHTAB_AUTOSTART" in reason


def test_main_records_external_prerequisite_skip(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("OPENMINION_E2E_PROVIDERS", "echo")
    monkeypatch.delenv("TINYFISH_API_KEY", raising=False)
    monkeypatch.delenv("PINCHTAB_URL", raising=False)
    monkeypatch.delenv("PINCHTAB_AUTOSTART", raising=False)

    def _fake_run_chat(**_kwargs):
        return True, "ok"

    monkeypatch.setattr(runner, "_run_chat", _fake_run_chat)

    log_root = tmp_path / "logs"
    assert (
        runner.main(
            [
                "--include-external-services",
                "--log-root",
                str(log_root),
                "--config-root",
                str(tmp_path / "configs"),
                "--work-root",
                str(tmp_path / "workdirs"),
            ]
        )
        == 0
    )

    summary = json.loads((log_root / "summary.json").read_text(encoding="utf-8"))
    assert summary["passed"] == 1
    assert summary["skipped"] == 1
    assert summary["failed"] == 0
    skip_reason = summary["results"][1]["reason"]
    assert "missing_provider_key:TINYFISH_API_KEY" in skip_reason
    assert "external_service_unavailable:PINCHTAB_URL/PINCHTAB_AUTOSTART" in skip_reason


def test_default_work_root_uses_ignored_generated_runtime_tree() -> None:
    assert ".openminion" in _default_work_root().parts
    assert _default_work_root().name == "chat-workdirs"


def test_ready_prompt_detects_current_tui_prompt() -> None:
    assert _ready_prompt_detected("Status:\n  usage: none\n❯ ") is True
    assert _ready_prompt_detected("[session|agent] you> ") is True


def test_turn_response_boundary_ignores_queued_prompt() -> None:
    previous = "❯ "
    current = previous + "hello\nhello\n\nQueued messages (1 pending).\n\n❯ "
    assert (
        _turn_response_boundary_detected_since(
            previous, current, require_turn_done=True
        )
        is False
    )


def test_turn_response_boundary_detects_slash_prompt_after_output() -> None:
    previous = "❯ "
    current = previous + "/status\n/status\n\nStatus:\n  agent: e2e-agent\n❯ "
    assert (
        _turn_response_boundary_detected_since(
            previous, current, require_turn_done=False
        )
        is True
    )


def test_turn_response_boundary_accepts_slash_prompt_without_body() -> None:
    previous = "❯ "
    current = previous + "/debug\n\n❯ "
    assert (
        _turn_response_boundary_detected_since(
            previous, current, require_turn_done=False
        )
        is True
    )


def test_turn_response_boundary_waits_for_done_on_chat_turn() -> None:
    previous = "❯ "
    current = previous + "hello\nhello\n\n❯ "
    assert (
        _turn_response_boundary_detected_since(
            previous, current, require_turn_done=True
        )
        is False
    )
    assert (
        _turn_response_boundary_detected_since(
            previous,
            current + "⏺ hello (thinking=minimal)\n\nDone in 0s\n\n",
            require_turn_done=True,
        )
        is True
    )


def test_runner_module_has_cli_entrypoint() -> None:
    source = inspect.getsource(runner)
    assert 'if __name__ == "__main__"' in source
    assert "raise SystemExit(main())" in source


def test_generated_config_uses_invoked_agent_id(tmp_path) -> None:
    config_path = tmp_path / "echo.json"
    _write_config("echo", "echo", config_path, agent_id="e2e-agent-echo")

    payload = json.loads(config_path.read_text(encoding="utf-8"))
    assert payload["default_agent"] == "e2e-agent-echo"
    assert payload["agents"]["e2e-agent-echo"]["name"] == "e2e-agent-echo"
    assert payload["agents"]["e2e-agent-echo"]["provider"] == "echo"


def test_generated_minimax_config_uses_openai_compatible_provider(tmp_path) -> None:
    config_path = tmp_path / "minimax.json"
    _write_config("minimax", "MiniMax-M2.7", config_path, agent_id="e2e-agent-minimax")

    payload = json.loads(config_path.read_text(encoding="utf-8"))
    assert payload["agents"]["e2e-agent-minimax"]["provider"] == "openai"
    assert payload["providers"]["openai"]["api_key_env"] == "MINIMAX_API_KEY"
    assert payload["providers"]["openai"]["base_url"] == "https://api.minimax.io/v1"
    assert payload["providers"]["openai"]["model"] == "MiniMax-M2.7"
