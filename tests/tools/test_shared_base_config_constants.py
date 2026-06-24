from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from openminion.base.constants import (
    OPENMINION_DATA_ROOT_ENV as MODULE_DATA_ROOT_ENV,
)
from openminion.modules.tool import constants as module_tool_constants
from openminion.modules.tool.cli.runtime_invocation import pinchtab_daemon_config
from openminion.tools import env as tools_env
from openminion.tools.config import (
    get_tool_env_list,
    resolve_tool_context_env,
    resolve_tool_data_root,
    resolve_tool_workspace_root,
)
from openminion.tools.constants import (
    OPENMINION_POLICY_PATH_ENV,
    OPENMINION_WEB_SEARCH_PROVIDER_ENV,
    TOOL_REASON_RECORD_NOT_FOUND,
    TOOL_REASON_STORAGE_EXEC_ERROR,
    TOOL_REASON_STORAGE_UNAVAILABLE,
    TOOL_REASON_STORAGE_UNCONFIGURED,
)
from openminion.tools.ip.constants import DEFAULT_IP_PUBLIC_LOOKUP_ENDPOINTS
from openminion.tools.location.constants import (
    LOCATION_REASON_RECORD_NOT_FOUND,
    LOCATION_REASON_STORAGE_EXEC_ERROR,
    LOCATION_REASON_STORAGE_UNAVAILABLE,
    LOCATION_REASON_STORAGE_UNCONFIGURED,
)
from openminion.tools.browser.providers.pinchtab.constants import (
    DEFAULT_PINCHTAB_RUNTIME_SUBPATH,
)
from openminion.tools.task.constants import (
    TASK_REASON_RECORD_NOT_FOUND,
    TASK_REASON_STORAGE_EXEC_ERROR,
    TASK_REASON_STORAGE_UNAVAILABLE,
    TASK_REASON_STORAGE_UNCONFIGURED,
)
from openminion.tools.time.constants import (
    TIME_REASON_STORAGE_EXEC_ERROR,
    TIME_REASON_STORAGE_UNAVAILABLE,
    TIME_REASON_STORAGE_UNCONFIGURED,
)


def test_tools_env_reexports_canonical_shared_constants() -> None:
    assert (
        tools_env.OPENMINION_WEB_SEARCH_PROVIDER_ENV
        == OPENMINION_WEB_SEARCH_PROVIDER_ENV
    )
    assert (
        module_tool_constants.OPENMINION_POLICY_PATH_ENV == OPENMINION_POLICY_PATH_ENV
    )
    assert module_tool_constants.OPENMINION_DATA_ROOT_ENV == MODULE_DATA_ROOT_ENV


def test_resolve_tool_context_env_prefers_context_runtime_env() -> None:
    ctx = SimpleNamespace(
        runtime=SimpleNamespace(env={"OPENMINION_TEST_ENV": "runtime-value"})
    )
    resolved = resolve_tool_context_env(ctx)
    assert resolved.get("OPENMINION_TEST_ENV", "") == "runtime-value"


def test_get_tool_env_list_dedupes_values() -> None:
    resolved = get_tool_env_list(
        "OPENMINION_IP_PUBLIC_ENDPOINTS",
        env={"OPENMINION_IP_PUBLIC_ENDPOINTS": "https://a, https://b, https://a"},
    )
    assert resolved == ("https://a", "https://b")


def test_resolve_tool_workspace_root_prefers_context_extras(tmp_path: Path) -> None:
    extras_root = tmp_path / "extras-root"
    extras_root.mkdir(parents=True, exist_ok=True)
    env_root = tmp_path / "env-root"
    env_root.mkdir(parents=True, exist_ok=True)
    ctx = SimpleNamespace(
        extras={"workspace_root": str(extras_root)},
        env={"OPENMINION_WORKSPACE_ROOT": str(env_root)},
    )
    assert resolve_tool_workspace_root(context=ctx) == extras_root.resolve(strict=False)


def test_resolve_tool_data_root_uses_openminion_home(
    monkeypatch, tmp_path: Path
) -> None:
    home_root = tmp_path / "home"
    home_root.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("OPENMINION_HOME", str(home_root))
    monkeypatch.delenv(MODULE_DATA_ROOT_ENV, raising=False)

    assert resolve_tool_data_root() == (home_root / ".openminion").resolve(strict=False)


def test_pinchtab_daemon_config_uses_shared_data_root(tmp_path: Path) -> None:
    home_root = tmp_path / "home"
    data_root = tmp_path / "data"
    cfg = pinchtab_daemon_config(
        env={
            "OPENMINION_HOME": str(home_root),
            MODULE_DATA_ROOT_ENV: str(data_root),
        }
    )
    assert cfg.runtime_dir == (data_root / DEFAULT_PINCHTAB_RUNTIME_SUBPATH).resolve(
        strict=False
    )


def test_shared_reason_codes_are_reexported_consistently() -> None:
    assert LOCATION_REASON_STORAGE_UNCONFIGURED == TOOL_REASON_STORAGE_UNCONFIGURED
    assert LOCATION_REASON_STORAGE_UNAVAILABLE == TOOL_REASON_STORAGE_UNAVAILABLE
    assert LOCATION_REASON_STORAGE_EXEC_ERROR == TOOL_REASON_STORAGE_EXEC_ERROR
    assert LOCATION_REASON_RECORD_NOT_FOUND == TOOL_REASON_RECORD_NOT_FOUND
    assert TIME_REASON_STORAGE_UNCONFIGURED == TOOL_REASON_STORAGE_UNCONFIGURED
    assert TIME_REASON_STORAGE_UNAVAILABLE == TOOL_REASON_STORAGE_UNAVAILABLE
    assert TIME_REASON_STORAGE_EXEC_ERROR == TOOL_REASON_STORAGE_EXEC_ERROR
    assert TASK_REASON_STORAGE_UNCONFIGURED == TOOL_REASON_STORAGE_UNCONFIGURED
    assert TASK_REASON_STORAGE_UNAVAILABLE == TOOL_REASON_STORAGE_UNAVAILABLE
    assert TASK_REASON_STORAGE_EXEC_ERROR == TOOL_REASON_STORAGE_EXEC_ERROR
    assert TASK_REASON_RECORD_NOT_FOUND == TOOL_REASON_RECORD_NOT_FOUND


def test_shared_ip_defaults_remain_available_through_tools_env() -> None:
    assert (
        tools_env.get_ip_public_lookup_endpoints() == DEFAULT_IP_PUBLIC_LOOKUP_ENDPOINTS
    )
