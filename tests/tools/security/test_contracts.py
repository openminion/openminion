from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from openminion.modules.tool import build_default_tool_registry
from openminion.modules.tool.errors import ToolRuntimeError
from openminion.modules.tool.exposure import get_model_exposure_specs
from openminion.modules.tool.framework import derive_manifest
from openminion.tools.security import ALL_SECURITY_TOOLS, REGISTRAR, SECURITY_FAMILY
from openminion.tools.security.config import (
    SecurityConfig,
    resolve_local_target,
    resolve_security_config,
)
from openminion.tools.security.schemas import LocalScanArgs


def test_security_family_contract_and_hidden_profile() -> None:
    registry = build_default_tool_registry(strict=False)
    assert REGISTRAR.get_manifest(None) == derive_manifest(SECURITY_FAMILY)
    assert set(ALL_SECURITY_TOOLS).issubset(registry.list())

    inactive = get_model_exposure_specs(registry, metadata={"session_id": "s1"})
    assert {spec.name for spec in inactive}.isdisjoint(ALL_SECURITY_TOOLS)

    with pytest.raises(ToolRuntimeError, match="dependency"):
        registry.exposure_service.activate(
            "security_readonly", session_id="s1", approved=True
        )

    registry.exposure_service.activate(
        "security_readonly",
        session_id="s1",
        dependencies=("binary:semgrep", "binary:trivy"),
        approved=True,
    )
    visible = get_model_exposure_specs(registry, metadata={"session_id": "s1"})
    assert set(ALL_SECURITY_TOOLS).issubset({spec.name for spec in visible})


def test_scan_args_are_bounded_and_reject_scanner_controls() -> None:
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        LocalScanArgs.model_validate({"target": ".", "executable": "/tmp/scanner"})
    with pytest.raises(ValidationError):
        LocalScanArgs.model_validate({"target": ".", "max_findings": 0})
    with pytest.raises(ValidationError):
        LocalScanArgs.model_validate({"target": ".", "timeout_seconds": 601})


def test_security_config_defaults_to_current_workspace(tmp_path: Path) -> None:
    config = resolve_security_config(SimpleNamespace(workspace=tmp_path, env={}))
    assert config.allowed_roots == (tmp_path.resolve(),)
    assert config.semgrep_executable == "semgrep"
    assert config.trivy_executable == "trivy"


def test_target_must_exist_inside_allowed_root(tmp_path: Path) -> None:
    allowed = tmp_path / "allowed"
    allowed.mkdir()
    target = allowed / "target"
    target.mkdir()
    config = SecurityConfig(
        workspace_root=allowed,
        allowed_roots=(allowed,),
        semgrep_executable="semgrep",
        semgrep_config="rules.yml",
        trivy_executable="trivy",
    )
    assert resolve_local_target("target", config) == target.resolve()
    with pytest.raises(ToolRuntimeError, match="outside configured allowed roots"):
        resolve_local_target(str(tmp_path), config)
    with pytest.raises(ToolRuntimeError, match="does not exist"):
        resolve_local_target("missing", config)
