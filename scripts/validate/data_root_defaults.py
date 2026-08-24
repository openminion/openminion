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
OPENMINION_IMPORT_RE = re.compile(r"(?m)^(?:from|import)\s+openminion(?:\.|\s)")
MAIN_GUARD_RE = re.compile(r"""(?m)^if\s+__name__\s*==\s*["']__main__["']\s*:""")
PYTHON_RUNNER_ROOT_EXEMPTIONS = {
    "tests/ci/runners/run_exec_validation_matrix_lane.py": (
        "delegates only to pytest, whose conftest owns isolation"
    ),
    "tests/e2e/runners/run_cli_focus_e2e.py": (
        "delegates only to pytest, whose conftest owns isolation"
    ),
    "tests/e2e/runners/run_memory_identity_e2e_smoke.py": (
        "uses TemporaryDirectory for its runtime root"
    ),
    "tests/e2e/runners/run_tokencensus_pipe_e2e.py": (
        "requires an explicit root or creates one with tempfile.mkdtemp"
    ),
    "tests/e2e/runners/run_trailer_conformance_matrix.py": (
        "reads an explicit existing data root and requires an output path"
    ),
    "tests/skills/runners/run_skill_prerouting_resilience_report.py": (
        "reads explicit inputs and writes only an explicit optional output"
    ),
    "tests/skills/runners/run_skill_selection_ab_probe.py": (
        "requires an explicit caller-owned root"
    ),
}


def _runtime_roots_precede_openminion_imports(text: str, setup_name: str) -> bool:
    openminion_import = OPENMINION_IMPORT_RE.search(text)
    if openminion_import is None:
        return True
    setup_index = text.find(f"{setup_name}(")
    return 0 <= setup_index < openminion_import.start()


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
    if not _runtime_roots_precede_openminion_imports(
        conftest_text, "configure_runtime_roots"
    ):
        hits.append(
            "tests/conftest.py: shared collection runtime roots must precede "
            "OpenMinion imports"
        )
    for env_name in TEST_ROOT_ENV_NAMES[:2]:
        if f'monkeypatch.setenv("{env_name}"' not in conftest_text:
            hits.append(f"tests/conftest.py: missing isolated {env_name} fixture")
    if 'monkeypatch.delenv("OPENMINION_GENERATED_ROOT"' not in conftest_text:
        hits.append("tests/conftest.py: generated root must derive from isolated data")

    helper_contracts = (
        (TESTS_DIR / "helpers" / "runtime_roots.py", "tempfile.mkdtemp"),
        (TESTS_DIR / "helpers" / "runtime_roots.sh", "mktemp -d"),
    )
    for helper_path, temp_root_marker in helper_contracts:
        rel = str(helper_path.relative_to(REPO_ROOT))
        if not helper_path.is_file():
            hits.append(f"{rel}: missing shared runtime-root helper")
            continue
        helper_text = helper_path.read_text(encoding="utf-8")
        if temp_root_marker not in helper_text:
            hits.append(f"{rel}: missing system-temp root owner")
        for env_name in TEST_ROOT_ENV_NAMES:
            if env_name not in helper_text:
                hits.append(f"{rel}: missing {env_name}")

    python_runners = sorted(TESTS_DIR.glob("**/runners/run_*.py"))
    discovered_runner_paths = {
        str(path.relative_to(REPO_ROOT)) for path in python_runners
    }
    for stale_path in sorted(
        set(PYTHON_RUNNER_ROOT_EXEMPTIONS).difference(discovered_runner_paths)
    ):
        hits.append(f"{stale_path}: stale runtime-root exemption")
    for path in python_runners:
        rel = str(path.relative_to(REPO_ROOT))
        text = path.read_text(encoding="utf-8")
        isolated = "isolate_runtime_roots(" in text
        exempt = rel in PYTHON_RUNNER_ROOT_EXEMPTIONS
        if not isolated and not exempt:
            hits.append(f"{rel}: unmanaged executable test runtime roots")
        if isolated and exempt:
            hits.append(f"{rel}: redundant runtime-root exemption")
        if isolated and not _runtime_roots_precede_openminion_imports(
            text, "isolate_runtime_roots"
        ):
            hits.append(
                f"{rel}: runtime-root isolation must precede OpenMinion imports"
            )

    for path in sorted(TESTS_DIR.rglob("*.py")):
        if path in python_runners:
            continue
        rel = str(path.relative_to(REPO_ROOT))
        text = path.read_text(encoding="utf-8")
        if MAIN_GUARD_RE.search(text) is None:
            continue
        if OPENMINION_IMPORT_RE.search(text) is None:
            continue
        if "isolate_runtime_roots(" not in text:
            hits.append(f"{rel}: unmanaged executable test runtime roots")
        elif not _runtime_roots_precede_openminion_imports(
            text, "isolate_runtime_roots"
        ):
            hits.append(
                f"{rel}: runtime-root isolation must precede OpenMinion imports"
            )

    for path in sorted(TESTS_DIR.glob("**/runners/run_*.sh")):
        rel = str(path.relative_to(REPO_ROOT))
        text = path.read_text(encoding="utf-8")
        if "helpers/runtime_roots.sh" not in text:
            hits.append(f"{rel}: missing shared shell runtime-root helper")
        if "isolate_openminion_test_roots " not in text:
            hits.append(f"{rel}: unmanaged executable shell runtime roots")
        if "${OPENMINION_DIR}/test-configs/" in text:
            hits.append(f"{rel}: package-checkout config write default")
        if "--config test-configs/" in text:
            hits.append(f"{rel}: missing package-relative test config")
        if "${TMPDIR:-/tmp}/openminion-test-artifacts" in text:
            hits.append(f"{rel}: shared temporary artifact default")

    makefile_text = (REPO_ROOT / "Makefile").read_text(encoding="utf-8")
    if "OPENMINION_HOME ?= $(REPO_ROOT)" in makefile_text:
        hits.append("Makefile: test home defaults to the package checkout")
    if "OPENMINION_DATA_ROOT ?= $(OPENMINION_HOME)/.openminion" in makefile_text:
        hits.append("Makefile: test data defaults to the package checkout")

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
