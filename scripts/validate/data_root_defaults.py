#!/usr/bin/env python3
"""Reject legacy or incomplete OpenMinion runtime-root defaults."""

from __future__ import annotations

import ast
from pathlib import Path
import re
import sys

REPO_IMPORT_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_IMPORT_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_IMPORT_ROOT))

from scripts.common.terminal_output import emit_plain_findings  # noqa: E402


REPO_ROOT = Path(__file__).resolve().parents[2]
MODULES_DIR = REPO_ROOT / "src" / "openminion" / "modules"
TESTS_DIR = REPO_ROOT / "tests"
PATTERNS = [
    re.compile(r"~/.openminion(?!-data)"),
    re.compile(r"/\.openminion/"),
]
ALLOWLIST_FILES = {
    "src/openminion/modules/memory/migrate.py",
    "src/openminion/modules/telemetry/service.py",
    # identity/config.py defines standalone-mode defaults for identity paths.
    # These intentionally reference ~/.openminion as a fallback when data_root
    # is not injected (module standalone mode).
    "src/openminion/modules/identity/config.py",
}
TEST_ROOT_ENV_NAMES = (
    "OPENMINION_HOME",
    "OPENMINION_DATA_ROOT",
    "OPENMINION_GENERATED_ROOT",
)
TEST_RUNTIME_ROOT_OWNERS = (
    "tests/brain/diagnostics/phase_contract_eval.py",
    "tests/e2e/cli/focus/harness/probe.py",
    "tests/e2e/cli/focus/harness/test_provider_matrix.py",
    "tests/e2e/cli/focus/test_onboarding.py",
    "tests/e2e/runners/run_autonomy_smoke.py",
    "tests/e2e/runners/run_chat_permutations_e2e.py",
    "tests/e2e/runners/run_cli_chat_probe.py",
    "tests/e2e/runners/run_cli_e2e_gate.py",
    "tests/e2e/runners/run_cortensor_e2e_suite.py",
    "tests/e2e/runners/run_crdh_e2e_smoke.py",
    "tests/e2e/runners/run_daily_assistant_smoke_suite.py",
    "tests/e2e/runners/run_inference_validation_smoke.py",
    "tests/e2e/runners/run_memory_identity_e2e_smoke.py",
    "tests/e2e/runners/run_tokencensus_pipe_e2e.py",
    "tests/e2e/test_live_cli_chat_identity_yaml_matrix.py",
    "tests/e2e/test_live_skill_dense_catalog_matrix.py",
    "tests/e2e/test_live_tool_new_tools_openrouter_matrix.py",
    "tests/e2e/test_live_tool_profile_matrix.py",
    "tests/helpers/live_cli_chat_alibaba.py",
    "tests/skills/runners/run_nl_named_skill_baseline.py",
    "tests/skills/runners/run_skill_selection_ab_probe.py",
    "tests/test_public_first_run_cli.py",
)


def _is_path_cwd_call(node: ast.AST) -> bool:
    return (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "cwd"
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id == "Path"
    )


def _has_cwd_database_path(text: str) -> bool:
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return False
    for node in ast.walk(tree):
        if isinstance(node, ast.keyword):
            if node.arg == "database_path" and _is_path_cwd_call(node.value):
                return True
        elif isinstance(node, ast.Assign) and _is_path_cwd_call(node.value):
            if any(
                isinstance(target, ast.Name) and target.id == "database_path"
                for target in node.targets
            ):
                return True
    return False


def _should_scan(path: Path) -> bool:
    if not path.is_file() or path.suffix != ".py":
        return False
    rel = str(path.relative_to(REPO_ROOT))
    return rel not in ALLOWLIST_FILES


def main() -> int:
    hits: list[str] = []
    for path in MODULES_DIR.rglob("*.py"):
        if not _should_scan(path):
            continue
        rel = str(path.relative_to(REPO_ROOT))
        text = path.read_text(encoding="utf-8", errors="ignore")
        hits.extend(
            f"{rel}: {pattern.pattern}" for pattern in PATTERNS if pattern.search(text)
        )

    conftest_text = (TESTS_DIR / "conftest.py").read_text(encoding="utf-8")
    for env_name in TEST_ROOT_ENV_NAMES[:2]:
        if f'monkeypatch.setenv("{env_name}"' not in conftest_text:
            hits.append(f"tests/conftest.py: missing isolated {env_name} fixture")
    if 'monkeypatch.delenv("OPENMINION_GENERATED_ROOT"' not in conftest_text:
        hits.append("tests/conftest.py: generated root must derive from isolated data")

    for rel in TEST_RUNTIME_ROOT_OWNERS:
        text = (REPO_ROOT / rel).read_text(encoding="utf-8")
        for env_name in TEST_ROOT_ENV_NAMES:
            if env_name not in text:
                hits.append(f"{rel}: missing explicit {env_name} runtime root")
        if 'setdefault("OPENMINION_DATA_ROOT"' in text:
            hits.append(f"{rel}: inherits ambient OPENMINION_DATA_ROOT")

    for path in TESTS_DIR.rglob("*.py"):
        text = path.read_text(encoding="utf-8", errors="ignore")
        if _has_cwd_database_path(text):
            rel = path.relative_to(REPO_ROOT)
            hits.append(f"{rel}: cwd-backed database_path")
    if hits:
        emit_plain_findings("OpenMinion runtime-root violations detected:", hits)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
