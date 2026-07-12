from __future__ import annotations

import ast
from pathlib import Path

import openminion.services.agent.execution as execution_pkg


def _repo_root() -> Path:
    for candidate in Path(__file__).resolve().parents:
        if (candidate / "src" / "openminion").is_dir():
            return candidate
    raise RuntimeError("unable to resolve repository root from test path")


ROOT = _repo_root()
EXECUTION_DIR = ROOT / "src" / "openminion" / "services" / "agent" / "execution"
LEGACY_FILE = ROOT / "src" / "openminion" / "services" / "agent" / "turn_flow.py"
FEATURE_MODULES = {"executor", "response", "tool_plan", "validators"}


def _imported_feature_modules(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            module = node.module or ""
            if module in FEATURE_MODULES:
                imported.add(module)
            if module.startswith("."):
                token = module.lstrip(".").split(".")[0]
                if token in FEATURE_MODULES:
                    imported.add(token)
            for alias in node.names:
                token = str(alias.name or "").split(".")[0]
                if token in FEATURE_MODULES:
                    imported.add(token)
    return imported


def test_execution_public_import_contract_is_stable() -> None:
    assert hasattr(execution_pkg, "AgentTurnFlowMixin")
    assert hasattr(execution_pkg.AgentTurnFlowMixin, "run_turn")


def test_execution_legacy_single_file_is_removed() -> None:
    assert not LEGACY_FILE.exists(), (
        "legacy single-file execution wrapper should not be reintroduced"
    )
    assert EXECUTION_DIR.is_dir(), "execution package directory must exist"


def test_execution_feature_modules_do_not_import_each_other() -> None:
    violations: list[str] = []
    for module_name in FEATURE_MODULES:
        path = EXECUTION_DIR / f"{module_name}.py"
        imports = _imported_feature_modules(path)
        imports.discard(module_name)
        if imports:
            violations.append(f"{module_name} imports {sorted(imports)}")
    assert not violations, " ; ".join(violations)
