from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
from pathlib import Path

import pytest

from openminion.base.constants import (
    OPENMINION_DATA_ROOT_ENV,
    OPENMINION_GENERATED_ROOT_ENV,
)
from tests.e2e import test_live_cli_chat_identity_yaml_matrix as identity_matrix
from tests.helpers import live_cli_chat_alibaba


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _load_module_from_repo_path(module_name: str, *relative_parts: str):
    repo_root = _repo_root()
    module_path = repo_root.joinpath(*relative_parts)
    spec = importlib.util.spec_from_file_location(module_name, module_path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _load_cli_gate_module():
    return _load_module_from_repo_path(
        "run_cli_e2e_gate",
        "openminion",
        "tests",
        "e2e",
        "runners",
        "run_cli_e2e_gate.py",
    )


def _load_chat_permutations_module():
    return _load_module_from_repo_path(
        "run_chat_permutations_e2e",
        "openminion",
        "tests",
        "e2e",
        "runners",
        "run_chat_permutations_e2e.py",
    )


def _load_cli_chat_probe_module():
    return _load_module_from_repo_path(
        "run_cli_chat_probe",
        "openminion",
        "tests",
        "e2e",
        "runners",
        "run_cli_chat_probe.py",
    )


def _load_live_skill_dense_probe_module():
    return _load_module_from_repo_path(
        "run_live_skill_dense_catalog_probe",
        "openminion",
        "tests",
        "e2e",
        "runners",
        "run_live_skill_dense_catalog_probe.py",
    )


def _load_autonomy_smoke_module():
    return _load_module_from_repo_path(
        "run_autonomy_smoke",
        "openminion",
        "tests",
        "e2e",
        "runners",
        "run_autonomy_smoke.py",
    )


def _load_cortensor_e2e_suite_module():
    return _load_module_from_repo_path(
        "run_cortensor_e2e_suite",
        "openminion",
        "tests",
        "e2e",
        "runners",
        "run_cortensor_e2e_suite.py",
    )


def test_live_cli_chat_helper_artifacts_use_openminion_package_home(
    monkeypatch, tmp_path: Path
) -> None:
    framework_root = tmp_path / "framework"
    framework_root.mkdir()
    openminion_root = framework_root / "openminion"
    openminion_root.mkdir()

    monkeypatch.setattr(live_cli_chat_alibaba, "framework_root", lambda: framework_root)
    monkeypatch.setattr(
        live_cli_chat_alibaba, "runtime_home_root", lambda: openminion_root
    )

    artifact_dir = live_cli_chat_alibaba.artifact_dir()

    assert artifact_dir == openminion_root / ".openminion" / "runtime" / "cli-chat-e2e"
    assert artifact_dir.exists()


def test_live_cli_chat_helper_skips_on_provider_quota_rejection(
    tmp_path: Path,
) -> None:
    transcript_path = tmp_path / "quota.txt"
    transcript_path.write_text("HTTP 429: usage limit exceeded", encoding="utf-8")

    with pytest.raises(pytest.skip.Exception, match="quota/billing unavailable"):
        live_cli_chat_alibaba.skip_if_provider_quota_rejected(
            transcript=transcript_path.read_text(encoding="utf-8"),
            transcript_path=transcript_path,
            context="quota test",
        )


def test_live_cli_chat_helper_detects_completion_contract_failure() -> None:
    assert live_cli_chat_alibaba.has_completion_contract_failure(
        {
            "body_preview": (
                "General act work ended without the required typed "
                "finalization_status contract."
            ),
            "failure_message": (
                "The model ended the turn without the required completion contract. "
                "Please try again."
            ),
        }
    )


def test_live_cli_chat_helper_skips_on_completion_contract_failure(
    tmp_path: Path,
) -> None:
    transcript_path = tmp_path / "contract.txt"
    transcript_path.write_text("contract failed", encoding="utf-8")

    with pytest.raises(pytest.skip.Exception, match="required completion contract"):
        live_cli_chat_alibaba.skip_if_completion_contract_failed(
            last_turn={
                "body_preview": (
                    "General act work ended without the required typed "
                    "finalization_status contract."
                ),
                "failure_message": (
                    "The model ended the turn without the required completion "
                    "contract. Please try again."
                ),
            },
            transcript_path=transcript_path,
            context="contract test",
        )


def test_live_cli_chat_helper_detects_placeholder_runtime_env(
    monkeypatch, tmp_path: Path
) -> None:
    config_path = tmp_path / "config.json"
    config_path.write_text(
        json.dumps({"runtime": {"env": {"MINIMAX_API_KEY": "__SET_ME__"}}}),
        encoding="utf-8",
    )
    monkeypatch.delenv("MINIMAX_API_KEY", raising=False)
    assert live_cli_chat_alibaba._config_has_unset_runtime_env(config_path) == (
        "MINIMAX_API_KEY",
    )


def test_live_cli_chat_helper_ignores_placeholder_when_env_present(
    monkeypatch, tmp_path: Path
) -> None:
    config_path = tmp_path / "config.json"
    config_path.write_text(
        json.dumps({"runtime": {"env": {"MINIMAX_API_KEY": "__SET_ME__"}}}),
        encoding="utf-8",
    )
    monkeypatch.setenv("MINIMAX_API_KEY", "present")
    assert live_cli_chat_alibaba._config_has_unset_runtime_env(config_path) == ()


def test_identity_yaml_matrix_artifacts_use_openminion_package_home(
    monkeypatch, tmp_path: Path
) -> None:
    framework_root = tmp_path / "framework"
    framework_root.mkdir()
    openminion_root = framework_root / "openminion"
    openminion_root.mkdir()

    monkeypatch.setattr(identity_matrix, "_framework_root", lambda: framework_root)
    monkeypatch.setattr(identity_matrix, "_runtime_home_root", lambda: openminion_root)

    artifact_dir = identity_matrix._artifact_dir()

    assert artifact_dir == openminion_root / ".openminion" / "runtime" / "cli-chat-e2e"
    assert artifact_dir.exists()


def test_cli_gate_local_mode_runs_help_and_focus_contracts(monkeypatch) -> None:
    gate = _load_cli_gate_module()
    calls: list[list[str]] = []

    def fake_run(command, *, env, timeout_seconds=None):
        assert timeout_seconds is None
        assert env["PYTHONPATH"] == "src"
        calls.append([str(part) for part in command])
        return 0

    monkeypatch.setattr(gate, "_run", fake_run)

    assert gate._run_local({"PYTHONPATH": "src"}) == 0

    assert len(calls) == len(gate.HELP_COMMANDS) + 1
    assert [tuple(call[3:]) for call in calls[:-1]] == list(gate.HELP_COMMANDS)
    assert calls[-1][1:4] == ["-m", "pytest", "-q"]
    assert calls[-1][4:] == [*gate.LOCAL_TESTS, "-ra"]


def test_cli_gate_live_mode_delegates_to_focus_runner(monkeypatch) -> None:
    gate = _load_cli_gate_module()
    captured: dict[str, object] = {}

    def fake_run(command, *, env, timeout_seconds=None):
        captured["command"] = [str(part) for part in command]
        captured["env"] = dict(env)
        captured["timeout_seconds"] = timeout_seconds
        return 0

    monkeypatch.setattr(gate, "_run", fake_run)

    assert gate._run_live({"OPENMINION_CLI_E2E_GATE_TIMEOUT_SECONDS": "12"}) == 0

    assert captured["command"][-2:] == [
        "tests/e2e/runners/run_cli_focus_e2e.py",
        "live",
    ]
    assert captured["env"]["OPENMINION_LIVE_CLI_FOCUS_E2E"] == "1"
    assert captured["timeout_seconds"] == 12


def test_cli_chat_probe_defaults_home_and_data_root_to_openminion_package_root(
    monkeypatch, tmp_path: Path
) -> None:
    probe = _load_cli_chat_probe_module()
    framework_root = tmp_path / "framework"
    framework_root.mkdir()
    openminion_root = framework_root / "openminion"
    openminion_root.mkdir()

    monkeypatch.setattr(probe, "FRAMEWORK_ROOT", framework_root)
    monkeypatch.setattr(probe, "OPENMINION_ROOT", openminion_root)
    monkeypatch.delenv("OPENMINION_HOME", raising=False)

    assert probe._resolve_home_root() == openminion_root
    assert probe._resolve_data_root(openminion_root) == openminion_root / ".openminion"


def test_cli_chat_probe_passes_data_root_to_child_cli(
    monkeypatch, tmp_path: Path
) -> None:
    probe = _load_cli_chat_probe_module()
    config_path = tmp_path / "config.json"
    config_path.write_text('{"runtime": {"env": {}}}', encoding="utf-8")
    data_root = tmp_path / "probe-data"
    captured: dict[str, object] = {}

    def fake_run_probe_session(**kwargs):
        captured.update(kwargs)
        return 0, "HRMR_CHAT_OK\n"

    monkeypatch.setattr(probe, "_run_probe_session", fake_run_probe_session)
    monkeypatch.setattr(probe, "_find_conversation_session_id", lambda **_: None)
    monkeypatch.setattr(probe, "_collect_tool_audit_rows", lambda **_: ([], []))
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "run_cli_chat_probe.py",
            "--config",
            str(config_path),
            "--agent",
            "openminion",
            "--session",
            "probe-session",
            "--message",
            "reply with the marker",
            "--data-root",
            str(data_root),
            "--python",
            sys.executable,
            "--require-output-marker",
            "HRMR_CHAT_OK",
        ],
    )

    assert probe.main() == 0

    command = captured["cmd"]
    env = captured["env"]
    assert isinstance(command, list)
    assert isinstance(env, dict)
    assert command[command.index("--data-root") + 1] == str(data_root.resolve())
    assert env["OPENMINION_DATA_ROOT"] == str(data_root.resolve())


@pytest.mark.parametrize(
    "loader",
    [_load_autonomy_smoke_module, _load_cortensor_e2e_suite_module],
)
def test_e2e_runners_derive_openminion_home_from_nested_package(
    loader, monkeypatch, tmp_path: Path
) -> None:
    runner = loader()
    framework_root = tmp_path / "framework"
    openminion_root = framework_root / "openminion"
    openminion_root.mkdir(parents=True)

    monkeypatch.delenv("OPENMINION_HOME", raising=False)

    assert runner._derive_openminion_home(framework_root) == openminion_root
    assert runner._derive_openminion_home(openminion_root) == openminion_root


def test_cli_chat_probe_rewrites_relative_legacy_artifact_paths(
    monkeypatch, tmp_path: Path
) -> None:
    probe = _load_cli_chat_probe_module()
    framework_root = tmp_path / "framework"
    framework_root.mkdir()
    openminion_root = framework_root / "openminion"
    openminion_root.mkdir()
    cwd = openminion_root

    monkeypatch.setattr(probe, "FRAMEWORK_ROOT", framework_root)
    monkeypatch.setattr(probe, "OPENMINION_ROOT", openminion_root)

    normalized = probe._normalize_probe_path(
        raw_path=Path("artifacts/cli-chat-e2e/transcript.txt"),
        home_root=openminion_root,
        cwd=cwd,
    )

    assert normalized == (
        openminion_root
        / ".openminion"
        / "runtime"
        / "cli-chat-e2e"
        / "transcript.txt"
    )


def test_cli_chat_probe_rewrites_absolute_package_legacy_artifact_paths(
    monkeypatch, tmp_path: Path
) -> None:
    probe = _load_cli_chat_probe_module()
    framework_root = tmp_path / "framework"
    framework_root.mkdir()
    openminion_root = framework_root / "openminion"
    openminion_root.mkdir()

    monkeypatch.setattr(probe, "FRAMEWORK_ROOT", framework_root)
    monkeypatch.setattr(probe, "OPENMINION_ROOT", openminion_root)

    absolute_legacy_path = (
        openminion_root / "artifacts" / "cli-chat-e2e" / "ltsr" / "summary.json"
    )
    normalized = probe._normalize_probe_path(
        raw_path=absolute_legacy_path,
        home_root=openminion_root,
        cwd=openminion_root,
    )

    assert normalized == (
        openminion_root
        / ".openminion"
        / "runtime"
        / "cli-chat-e2e"
        / "ltsr"
        / "summary.json"
    )


def test_cli_chat_probe_leaves_nonlegacy_relative_output_under_cwd(
    monkeypatch, tmp_path: Path
) -> None:
    probe = _load_cli_chat_probe_module()
    framework_root = tmp_path / "framework"
    framework_root.mkdir()
    openminion_root = framework_root / "openminion"
    openminion_root.mkdir()

    monkeypatch.setattr(probe, "FRAMEWORK_ROOT", framework_root)
    monkeypatch.setattr(probe, "OPENMINION_ROOT", openminion_root)

    normalized = probe._normalize_probe_path(
        raw_path=Path("probe-output.txt"),
        home_root=openminion_root,
        cwd=openminion_root,
    )

    assert normalized == openminion_root / "probe-output.txt"


def test_cli_gate_default_mode_is_local() -> None:
    gate = _load_cli_gate_module()

    assert gate._parse_args([]).mode == "local"


@pytest.mark.parametrize("mode", ["local", "live", "all"])
def test_cli_gate_accepts_supported_modes(mode: str) -> None:
    gate = _load_cli_gate_module()

    assert gate._parse_args([mode]).mode == mode


def test_cli_gate_timeout_uses_positive_integer_or_default() -> None:
    gate = _load_cli_gate_module()

    assert gate._timeout_seconds({}) == gate.DEFAULT_LIVE_TIMEOUT_SECONDS
    assert gate._timeout_seconds({gate.TIMEOUT_ENV: "0"}) == gate.DEFAULT_LIVE_TIMEOUT_SECONDS
    assert gate._timeout_seconds({gate.TIMEOUT_ENV: "bad"}) == gate.DEFAULT_LIVE_TIMEOUT_SECONDS
    assert gate._timeout_seconds({gate.TIMEOUT_ENV: "42"}) == 42


def test_cli_gate_main_sets_runtime_env_and_runs_local(monkeypatch, tmp_path: Path) -> None:
    gate = _load_cli_gate_module()
    captured: dict[str, str] = {}
    python_path = tmp_path / "python3.11"
    python_path.write_text("", encoding="utf-8")

    monkeypatch.setattr(gate, "PYTHON", python_path)
    monkeypatch.setattr(
        gate,
        "_run_local",
        lambda env: captured.update(env) or 0,
    )

    assert gate.main(["local"]) == 0
    assert captured["PYTHONDONTWRITEBYTECODE"] == "1"
    assert captured["PYTHONPATH"] == str(gate.ROOT / "src")


def test_chat_permutations_runner_artifacts_use_generated_root(
    monkeypatch, tmp_path: Path
) -> None:
    runner = _load_chat_permutations_module()
    framework_root = tmp_path / "framework"
    framework_root.mkdir()
    openminion_home = framework_root / "openminion"
    openminion_home.mkdir()

    monkeypatch.setattr(runner, "REPO_ROOT", framework_root)
    monkeypatch.setattr(runner, "OPENMINION_DIR", openminion_home)
    monkeypatch.setenv("OPENMINION_HOME", str(openminion_home))
    monkeypatch.delenv(OPENMINION_DATA_ROOT_ENV, raising=False)
    monkeypatch.delenv(OPENMINION_GENERATED_ROOT_ENV, raising=False)

    artifacts_root = runner._default_artifacts_root()

    assert artifacts_root == openminion_home / ".openminion" / "runtime" / "e2e"
    assert runner._default_log_root() == artifacts_root / "chat-logs"
    assert runner._default_config_root() == artifacts_root / "chat-configs"


def test_live_skill_dense_probe_runner_artifacts_use_generated_root(
    monkeypatch, tmp_path: Path
) -> None:
    runner = _load_live_skill_dense_probe_module()
    framework_root = tmp_path / "framework"
    framework_root.mkdir()
    openminion_home = framework_root / "openminion"
    openminion_home.mkdir()

    monkeypatch.setattr(runner, "REPO_ROOT", framework_root)
    monkeypatch.setattr(runner, "OPENMINION_DIR", openminion_home)
    monkeypatch.setenv("OPENMINION_HOME", str(openminion_home))
    monkeypatch.delenv(OPENMINION_DATA_ROOT_ENV, raising=False)
    monkeypatch.delenv(OPENMINION_GENERATED_ROOT_ENV, raising=False)

    artifacts_root = runner._artifact_root()

    assert artifacts_root == (
        openminion_home
        / ".openminion"
        / "runtime"
        / "skill-complex-official-matrix"
    )


def test_ci_script_defaults_use_generated_runtime_tree(monkeypatch) -> None:
    repo_root = _repo_root()
    ci_root = repo_root / ".openminion" / "runtime" / "ci"
    cases = [
        (
            "run_migration_checks",
            ("openminion", "scripts", "ci", "run_migration_checks.py"),
            ["prog", "--modules-json", '["openminion-storage"]'],
            "junitxml",
            ci_root / "migrations" / "junit.xml",
        ),
        (
            "generate_bundle_manifest",
            ("openminion", "scripts", "ci", "generate_bundle_manifest.py"),
            ["prog"],
            "output",
            ci_root / "bundle" / "bundle-manifest.json",
        ),
        (
            "build_wheels",
            ("openminion", "scripts", "ci", "build_wheels.py"),
            ["prog", "--modules-json", '["openminion"]'],
            "out_dir",
            ci_root / "wheels",
        ),
        (
            "invoke_selector_checks",
            ("openminion", "scripts", "ci", "invoke_selector_checks.py"),
            [
                "prog",
                "--selectors-json",
                '["openminion/tests/test_e2e_artifact_paths.py"]',
            ],
            "junitxml",
            ci_root / "test-results" / "junit.xml",
        ),
        (
            "invoke_selector_checks_cov",
            ("openminion", "scripts", "ci", "invoke_selector_checks.py"),
            [
                "prog",
                "--selectors-json",
                '["openminion/tests/test_e2e_artifact_paths.py"]',
            ],
            "coverage_xml",
            ci_root / "test-results" / "coverage.xml",
        ),
    ]

    for module_name, relative_parts, argv, attr_name, expected in cases:
        monkeypatch.setattr(sys, "argv", argv)
        module = _load_module_from_repo_path(module_name, *relative_parts)
        args = module.parse_args()
        assert Path(getattr(args, attr_name)) == expected


def test_shell_e2e_runners_default_home_to_openminion_package_root() -> None:
    repo_root = _repo_root()
    shell_paths = [
        repo_root
        / "openminion"
        / "tests"
        / "e2e"
        / "runners"
        / "run_crdh_e2e_smoke_guard.sh",
        repo_root
        / "openminion"
        / "tests"
        / "e2e"
        / "runners"
        / "run_skill_fixture_scenarios.sh",
        repo_root
        / "openminion"
        / "tests"
        / "e2e"
        / "runners"
        / "run_chat_provider_smoke.sh",
    ]
    for path in shell_paths:
        text = path.read_text(encoding="utf-8")
        assert (
            'OPENMINION_HOME="${OPENMINION_HOME:-$OPENMINION_DIR}"' in text
            or 'OPENMINION_HOME="${OPENMINION_HOME:-$ROOT}"' in text
        )
        assert (
            'OPENMINION_DATA_ROOT="${OPENMINION_DATA_ROOT:-$OPENMINION_HOME/.openminion}"'
            in text
        )


def test_artifact_create_read_integration():
    from openminion.modules.brain.adapters.factory import create_artifact_adapter

    with tempfile.TemporaryDirectory() as tmpdir:
        adapter = create_artifact_adapter(mode="auto", config={"root": tmpdir})
        assert adapter is not None, "Artifact adapter should not be None"

        test_content = "Hello, this is test artifact content"
        result = adapter.execute(
            command={
                "tool_name": "create_artifact",
                "args": {
                    "content": test_content,
                    "mime": "text/plain",
                    "label": "test-artifact.txt",
                },
            },
            session_id="test-session",
            trace_id="test-trace",
        )
        assert result["status"] == "success", f"Create failed: {result}"
        artifact_id = result["outputs"]["id"]
        assert artifact_id is not None, "Artifact ID should be returned"

        # Read back the artifact
        result = adapter.execute(
            command={"tool_name": "read_artifact", "args": {"id": artifact_id}},
            session_id="test-session",
            trace_id="test-trace",
        )
        assert result["status"] == "success", f"Read failed: {result}"
        assert test_content in result["outputs"]["content"], (
            "Read content should match original"
        )

        print("RIG-06: Artifact create -> read flow passed")
