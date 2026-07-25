from __future__ import annotations

import importlib
from types import SimpleNamespace
from typing import Any

import pytest
from pydantic import ValidationError

from openminion.modules.tool.errors import ToolRuntimeError
from openminion.modules.tool.exposure import get_model_exposure_specs
from openminion.modules.tool import build_default_tool_registry
from openminion.modules.tool.framework import derive_manifest, derive_tool_specs
from openminion.modules.tool.registry import ToolRegistry

FAMILIES: tuple[dict[str, Any], ...] = (
    {
        "package": "cloud_ops",
        "family_attr": "CLOUD_OPS_FAMILY",
        "tools_attr": "ALL_CLOUD_OPS_TOOLS",
        "profile": "cloud_ops_readonly",
        "dependency": "fixture:cloud_ops",
        "fixture_operation": "ssm_inventory",
        "args": {
            "target_id": "target-1",
            "scope": "fixture",
            "account_id": "123456789012",
            "region": "us-east-1",
        },
    },
    {
        "package": "observability",
        "family_attr": "OBSERVABILITY_FAMILY",
        "tools_attr": "ALL_OBSERVABILITY_TOOLS",
        "profile": "observability_readonly",
        "dependency": "fixture:observability",
        "fixture_operation": "prometheus_rules",
        "args": {"target_id": "target-1", "scope": "fixture"},
    },
    {
        "package": "k8s",
        "family_attr": "K8S_FAMILY",
        "tools_attr": "ALL_K8S_TOOLS",
        "profile": "k8s_readonly",
        "dependency": "fixture:k8s",
        "fixture_operation": "workload_get",
        "args": {
            "target_id": "target-1",
            "scope": "fixture",
            "context": "dev",
            "namespace": "default",
            "kind": "deployment",
            "name": "web",
        },
    },
    {
        "package": "iac",
        "family_attr": "IAC_FAMILY",
        "tools_attr": "ALL_IAC_TOOLS",
        "profile": "iac_plan",
        "dependency": "fixture:iac",
        "fixture_operation": "validate",
        "args": {"target_id": "target-1", "scope": "fixture", "workspace": "infra"},
    },
    {
        "package": "gitops",
        "family_attr": "GITOPS_FAMILY",
        "tools_attr": "ALL_GITOPS_TOOLS",
        "profile": "gitops_readonly",
        "dependency": "fixture:gitops",
        "fixture_operation": "app_status",
        "args": {
            "target_id": "target-1",
            "scope": "fixture",
            "cluster": "dev",
            "namespace": "apps",
            "app": "web",
        },
    },
    {
        "package": "config_mgmt",
        "family_attr": "CONFIG_MGMT_FAMILY",
        "tools_attr": "ALL_CONFIG_MGMT_TOOLS",
        "profile": "config_check",
        "dependency": "fixture:config_mgmt",
        "fixture_operation": "ansible_check",
        "args": {
            "target_id": "target-1",
            "scope": "fixture",
            "inventory": "local",
            "playbook": "site.yml",
        },
    },
)


def _module(package: str):
    return importlib.import_module(f"openminion.tools.{package}")


def _registry(package: str) -> ToolRegistry:
    registry = ToolRegistry()
    _module(package).REGISTRAR.register(registry)
    return registry


@pytest.mark.parametrize("case", FAMILIES, ids=[case["package"] for case in FAMILIES])
def test_family_registrar_manifest_matches_registered_surface(
    case: dict[str, Any],
) -> None:
    module = _module(case["package"])
    family = getattr(module, case["family_attr"])
    expected_tools = getattr(module, case["tools_attr"])
    registry = _registry(case["package"])

    assert tuple(sorted(registry.list())) == tuple(sorted(expected_tools))
    assert tuple(tool.name for tool in derive_tool_specs(family)) == expected_tools
    assert derive_manifest(family) == module.REGISTRAR.get_manifest(None)
    assert all(tool.dangerous is False for tool in registry.list().values())


@pytest.mark.parametrize("case", FAMILIES, ids=[case["package"] for case in FAMILIES])
def test_family_tools_are_hidden_until_explicit_profile_activation(
    case: dict[str, Any],
) -> None:
    registry = build_default_tool_registry(strict=False)
    expected_tools = getattr(_module(case["package"]), case["tools_attr"])

    inactive = get_model_exposure_specs(
        registry,
        metadata={"session_id": "s1", "target_id": "target-1"},
    )
    inactive_names = {spec.name for spec in inactive}
    assert inactive_names.isdisjoint(expected_tools)

    with pytest.raises(ToolRuntimeError, match="dependency"):
        registry.exposure_service.activate(
            case["profile"],
            session_id="s1",
            target_id="target-1",
            target_kind="ops-target",
            approved=True,
        )

    registry.exposure_service.activate(
        case["profile"],
        session_id="s1",
        target_id="target-1",
        target_kind="ops-target",
        dependencies=(case["dependency"],),
        approved=True,
    )
    visible = get_model_exposure_specs(
        registry,
        metadata={"session_id": "s1", "target_id": "target-1"},
    )
    visible_names = {spec.name for spec in visible}
    assert set(expected_tools).issubset(visible_names)


@pytest.mark.parametrize("case", FAMILIES, ids=[case["package"] for case in FAMILIES])
def test_readonly_handler_returns_redacted_fixture_evidence(
    case: dict[str, Any],
) -> None:
    registry = _registry(case["package"])
    tool = registry.get(next(iter(registry.list())))
    ctx = SimpleNamespace(
        extras={
            f"{case['package']}_fixture": {
                case["fixture_operation"]: {
                    "items": [{"name": "sample", "secret_token": "do-not-leak"}]
                }
            }
        }
    )

    result = tool.handler(dict(case["args"]), ctx)

    assert result["ok"] is True
    assert result["verified"] is True
    assert result["data"]["evidence"]["source"] == "fixture"
    assert "do-not-leak" not in repr(result)
    assert "[REDACTED]" in repr(result)


@pytest.mark.parametrize("case", FAMILIES, ids=[case["package"] for case in FAMILIES])
def test_readonly_args_reject_mutation_fields(case: dict[str, Any]) -> None:
    registry = _registry(case["package"])
    tool = registry.get(next(iter(registry.list())))
    payload = dict(case["args"], apply=True)

    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        tool.args_model.model_validate(payload)
