from __future__ import annotations

from pathlib import Path


_REPO_ROOT = Path(__file__).resolve().parents[1]


def _read(relative_path: str) -> str:
    return (_REPO_ROOT / relative_path).read_text(encoding="utf-8")


def test_tier_a_model_tool_contract_imports_present() -> None:
    required_model_import_files = (
        "src/openminion/modules/tool/runtime/registry_categories.py",
        "src/openminion/services/tool/selection.py",
        "src/openminion/services/security/policy.py",
    )
    for relpath in required_model_import_files:
        content = _read(relpath)
        assert "modules.tool.contracts.model_ids import" in content, relpath


def test_tier_a_runtime_binding_contract_import_present() -> None:
    content = _read("src/openminion/modules/tool/dispatch.py")
    assert "modules.tool.contracts.runtime_ids import" in content
