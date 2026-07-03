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
        "run_cli_chat_e2e_gate",
        "openminion",
        "tests",
        "e2e",
        "runners",
        "run_cli_chat_e2e_gate.py",
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


def test_live_cli_chat_helper_artifacts_ignore_openminion_home(
    monkeypatch, tmp_path: Path
) -> None:
    framework_root = tmp_path / "framework"
    framework_root.mkdir()
    openminion_home = tmp_path / "openminion-home"
    openminion_home.mkdir()

    monkeypatch.setattr(live_cli_chat_alibaba, "framework_root", lambda: framework_root)
    monkeypatch.setenv("OPENMINION_HOME", str(openminion_home))

    artifact_dir = live_cli_chat_alibaba.artifact_dir()

    assert artifact_dir == framework_root / ".openminion" / "runtime" / "cli-chat-e2e"
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


def test_identity_yaml_matrix_artifacts_ignore_openminion_home(
    monkeypatch, tmp_path: Path
) -> None:
    framework_root = tmp_path / "framework"
    framework_root.mkdir()
    openminion_home = tmp_path / "openminion-home"
    openminion_home.mkdir()

    monkeypatch.setattr(identity_matrix, "_framework_root", lambda: framework_root)
    monkeypatch.setenv("OPENMINION_HOME", str(openminion_home))

    artifact_dir = identity_matrix._artifact_dir()

    assert artifact_dir == framework_root / ".openminion" / "runtime" / "cli-chat-e2e"
    assert artifact_dir.exists()


def test_cli_chat_gate_artifacts_root_uses_default_data_root(
    monkeypatch, tmp_path: Path
) -> None:
    gate = _load_cli_gate_module()
    openminion_home = tmp_path / "openminion-home"
    openminion_home.mkdir()

    monkeypatch.setenv("OPENMINION_HOME", str(openminion_home))

    assert gate.resolve_artifacts_root(openminion_home) == (
        openminion_home / ".openminion" / "runtime" / "cli-chat-e2e"
    )

    output_path = Path("artifacts/cli-chat-e2e/transcript.txt")
    resolved = (
        gate.resolve_artifacts_root(openminion_home)
        / gate._normalize_artifact_relative_path(output_path)
    ).resolve()

    assert resolved == (
        openminion_home / ".openminion" / "runtime" / "cli-chat-e2e" / "transcript.txt"
    )


def test_cli_chat_gate_rewrites_absolute_repo_artifacts_path(
    monkeypatch, tmp_path: Path
) -> None:
    gate = _load_cli_gate_module()
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    openminion_home = tmp_path / "openminion-home"
    openminion_home.mkdir()

    monkeypatch.setenv("OPENMINION_HOME", str(openminion_home))

    absolute_legacy_path = (
        repo_root / "artifacts" / "cli-chat-e2e" / "memory-export-import" / "gate.txt"
    )
    resolved = gate._normalize_output_path(
        raw_path=absolute_legacy_path,
        artifacts_root=gate.resolve_artifacts_root(openminion_home),
        repo_root=repo_root,
    )

    assert resolved == (
        openminion_home
        / ".openminion"
        / "runtime"
        / "cli-chat-e2e"
        / "memory-export-import"
        / "gate.txt"
    )


def test_cli_chat_probe_defaults_home_and_data_root_to_framework_root(
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

    assert probe._resolve_home_root() == framework_root
    assert probe._resolve_data_root(framework_root) == framework_root / ".openminion"


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
        home_root=framework_root,
        cwd=cwd,
    )

    assert normalized == (
        framework_root / ".openminion" / "runtime" / "cli-chat-e2e" / "transcript.txt"
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
        home_root=framework_root,
        cwd=openminion_root,
    )

    assert normalized == (
        framework_root
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
        home_root=framework_root,
        cwd=openminion_root,
    )

    assert normalized == openminion_root / "probe-output.txt"


def test_cli_chat_gate_resolves_default_agent_from_config_payload() -> None:
    gate = _load_cli_gate_module()

    resolved = gate._resolve_agent_id(
        requested_agent="",
        config_payload={
            "agents": {
                "alpha": {"provider": "openai"},
                "beta": {"provider": "openai"},
            },
            "default_agent": "beta",
        },
    )

    assert resolved == "beta"


def test_cli_chat_gate_resolves_single_agent_without_default() -> None:
    gate = _load_cli_gate_module()

    resolved = gate._resolve_agent_id(
        requested_agent="",
        config_payload={"agents": {"solo": {"provider": "openai"}}},
    )

    assert resolved == "solo"


def test_cli_chat_gate_explicit_agent_overrides_config_default() -> None:
    gate = _load_cli_gate_module()

    resolved = gate._resolve_agent_id(
        requested_agent="alpha",
        config_payload={
            "agents": {
                "alpha": {"provider": "openai"},
                "beta": {"provider": "openai"},
            },
            "default_agent": "beta",
        },
    )

    assert resolved == "alpha"


def test_cli_chat_gate_rejects_multi_agent_config_without_default() -> None:
    gate = _load_cli_gate_module()

    with pytest.raises(ValueError, match="could not resolve an agent id"):
        gate._resolve_agent_id(
            requested_agent="",
            config_payload={
                "agents": {
                    "alpha": {"provider": "openai"},
                    "beta": {"provider": "openai"},
                }
            },
        )


def test_chat_permutations_runner_artifacts_use_generated_root(
    monkeypatch, tmp_path: Path
) -> None:
    runner = _load_chat_permutations_module()
    framework_root = tmp_path / "framework"
    framework_root.mkdir()
    openminion_home = tmp_path / "openminion-home"
    openminion_home.mkdir()

    monkeypatch.setattr(runner, "REPO_ROOT", framework_root)
    monkeypatch.setenv("OPENMINION_HOME", str(openminion_home))
    monkeypatch.delenv(OPENMINION_DATA_ROOT_ENV, raising=False)
    monkeypatch.delenv(OPENMINION_GENERATED_ROOT_ENV, raising=False)

    artifacts_root = runner._default_artifacts_root()

    assert artifacts_root == framework_root / ".openminion" / "runtime" / "e2e"
    assert runner._default_log_root() == artifacts_root / "chat-logs"
    assert runner._default_config_root() == artifacts_root / "chat-configs"


def test_live_skill_dense_probe_runner_artifacts_use_generated_root(
    monkeypatch, tmp_path: Path
) -> None:
    runner = _load_live_skill_dense_probe_module()
    framework_root = tmp_path / "framework"
    framework_root.mkdir()
    openminion_home = tmp_path / "openminion-home"
    openminion_home.mkdir()

    monkeypatch.setattr(runner, "REPO_ROOT", framework_root)
    monkeypatch.setenv("OPENMINION_HOME", str(openminion_home))
    monkeypatch.delenv(OPENMINION_DATA_ROOT_ENV, raising=False)
    monkeypatch.delenv(OPENMINION_GENERATED_ROOT_ENV, raising=False)

    artifacts_root = runner._artifact_root()

    assert artifacts_root == (
        framework_root / ".openminion" / "runtime" / "skill-complex-official-matrix"
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


def test_shell_e2e_runners_default_home_to_framework_root() -> None:
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
        assert 'OPENMINION_HOME="${OPENMINION_HOME:-$FRAMEWORK_ROOT}"' in text
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
