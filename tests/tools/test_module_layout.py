from __future__ import annotations

import importlib
import sys
from pathlib import Path

import pytest

# File-role contract reference for the tool-plugin layout consistency audit rule.


def _project_root() -> Path:
    return Path(__file__).resolve().parents[2]


def test_tool_core_is_canonicalized_under_modules_tool() -> None:
    root = _project_root()
    module_tool = root / "src" / "openminion" / "modules" / "tool"
    tools_pkg = root / "src" / "openminion" / "tools"

    required_flat_files = {
        "base.py",
        "dispatch.py",
        "interfaces.py",
        "plugin_api.py",
        "plugin_contract.py",
        "__init__.py",
        "__main__.py",
        "errors.py",
    }
    assert required_flat_files.issubset({p.name for p in module_tool.glob("*.py")})
    for package_name in (
        "adapters",
        "bootstrap",
        "cli",
        "contracts",
        "diagnostics",
        "family",
        "registry",
        "runtime",
    ):
        assert (module_tool / package_name / "__init__.py").exists()
    assert (module_tool / "runtime" / "dispatch.py").exists()
    assert (module_tool / "runtime" / "manager.py").exists()
    assert (module_tool / "runtime" / "registrar.py").exists()
    assert (module_tool / "runtime" / "tools_core.py").exists()
    assert (module_tool / "contracts" / "schemas.py").exists()
    assert (module_tool / "diagnostics" / "events.py").exists()
    removed_flat_helpers = {
        "cli_runtime.py",
        "cli_runtime_invocation.py",
        "cli_core_commands.py",
        "cli_exec_commands.py",
        "cli_pinchtab_commands.py",
        "family_runtime.py",
        "family_policy.py",
        "family_events.py",
    }
    assert not removed_flat_helpers.intersection(
        {p.name for p in module_tool.glob("*.py")}
    )

    top_level_py = {p.name for p in tools_pkg.glob("*.py")}
    assert top_level_py == {
        "__init__.py",
        "__main__.py",
        "config.py",
        "constants.py",
        "decorator.py",  # ISAP-11 developer-facing @openminion.tool decorator
        "env.py",
    }


def test_tool_package_root_no_longer_exports_v1_surface() -> None:
    tool_pkg = importlib.import_module("openminion.modules.tool")

    retired_exports = (
        "EventSinkV1",
        "PolicyDecisionV1",
        "PolicyEvaluatorV1",
        "ResourceSelectors",
        "RuntimeMediationV1",
        "ToolContextV1",
        "ToolExecutionResult",
        "ToolExecutionWrapperV1",
        "ToolImplV1",
        "ToolInputValidationError",
        "ToolPolicyIntent",
        "ToolRegistryV1",
        "ToolSpecV1",
        "validate_tool_args",
    )

    for name in retired_exports:
        assert not hasattr(tool_pkg, name), (
            f"openminion.modules.tool should not export retired V1 symbol {name}"
        )


def test_tool_plugin_packages_remain_under_tools_namespace() -> None:
    root = _project_root()
    tools_pkg = root / "src" / "openminion" / "tools"

    top_level_dirs = {
        "browser",
        "code",
        "exec",
        "fetch",
        "file",
        "gws",
        "ip",
        "location",
        "mcp",
        "reaction",
        "search",
        "skill",
        "task",
        "time",
        "tool_catalog",
        "utility",
        "weather",
    }
    discovered = {
        p.name
        for p in tools_pkg.iterdir()
        if p.is_dir() and not p.name.startswith("__")
    }
    assert top_level_dirs.issubset(discovered)
    for name in top_level_dirs:
        assert (tools_pkg / name / "__init__.py").exists()

    expected_provider_dirs = {
        "search": {"brave", "firecrawl", "serpapi", "serper", "tavily"},
        "browser": {"pinchtab", "playwright"},
        "weather": {"openmeteo", "weatherapi"},
        "fetch": {"scrapling"},
    }
    for category, expected in expected_provider_dirs.items():
        providers_dir = tools_pkg / category / "providers"
        assert (providers_dir / "__init__.py").exists()
        discovered_providers = {
            path.name for path in providers_dir.iterdir() if path.is_dir()
        }
        assert expected.issubset(discovered_providers)

    for retired in (
        "browser_pinchtab",
        "browser_playwright",
        "fetch_scrapling",
        "search_brave",
        "search_firecrawl",
        "search_serpapi",
        "search_serper",
        "search_tavily",
        "weather_openmeteo",
        "weather_weatherapi",
    ):
        assert not (tools_pkg / retired).exists()


def test_tool_plugin_packages_keep_explicit_registrar_surface() -> None:
    root = _project_root()
    tools_root = root / "src" / "openminion" / "tools"
    missing: list[str] = []

    for plugin_path in sorted(tools_root.rglob("plugin.py")):
        registrar_path = plugin_path.parent / "registrar.py"
        if not registrar_path.exists():
            missing.append(str(registrar_path.relative_to(tools_root)))

    assert not missing, "Tool plugin packages missing registrar.py:\n" + "\n".join(
        missing
    )


def test_legacy_tool_core_imports_fail_fast() -> None:
    for name in (
        "base",
        "cli",
        "errors",
        "interfaces",
        "plugins",
        "policy",
        "registry",
        "runtime",
        "schemas",
        "tools_core",
    ):
        sys.modules.pop(f"openminion.tools.{name}", None)
        with pytest.raises(ModuleNotFoundError):
            importlib.import_module(f"openminion.tools.{name}")

    legacy_pkg = importlib.import_module("openminion.modules.tool.cli")
    canonical_cli = importlib.import_module("openminion.modules.tool.cli")
    assert legacy_pkg is canonical_cli
    assert hasattr(canonical_cli, "main")


def test_all_bootstrap_tool_packages_define_interfaces_module() -> None:
    from openminion.modules.tool.bootstrap import _TOOL_BOOTSTRAP_ENTRIES

    root = _project_root()
    tools_root = root / "src" / "openminion" / "tools"
    missing: list[str] = []

    for entry in _TOOL_BOOTSTRAP_ENTRIES:
        if entry.kind != "tool":
            continue
        parts = entry.module_name.split(".")
        if len(parts) < 3 or parts[0] != "openminion" or parts[1] != "tools":
            continue
        module_path = Path(*parts[2:])
        interfaces_path = tools_root / module_path / "interfaces.py"
        if not interfaces_path.exists():
            missing.append(f"{entry.module_name} -> {interfaces_path}")
            continue
        importlib.import_module(f"{entry.module_name}.interfaces")

    assert not missing, "Missing interfaces.py for tool modules:\n" + "\n".join(missing)


def test_browser_interfaces_module_is_browser_only() -> None:
    browser_interfaces = importlib.import_module("openminion.tools.browser.interfaces")
    assert hasattr(browser_interfaces, "BROWSER_PLUGIN_INTERFACE_VERSION")

    foreign_contract_symbols = (
        "PLAYWRIGHT_PLUGIN_INTERFACE_VERSION",
        "GWS_PLUGIN_INTERFACE_VERSION",
        "REACTIONS_PLUGIN_INTERFACE_VERSION",
        "CRON_PLUGIN_INTERFACE_VERSION",
    )
    for symbol in foreign_contract_symbols:
        assert not hasattr(browser_interfaces, symbol), (
            f"browser.interfaces should not export foreign symbol {symbol}"
        )
