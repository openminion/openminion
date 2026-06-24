from pathlib import Path
from types import SimpleNamespace

from openminion.cli.config import (
    infer_workspace_home_root,
    resolve_cli_tool_provider_specs_and_dispatch_map,
    resolve_cli_identity_db_path,
    resolve_cli_policy_db_path,
    resolve_cli_roots,
)


def test_resolve_cli_roots_respects_explicit_home_and_relative_data_root(
    tmp_path,
) -> None:
    home_root = tmp_path / "home"
    roots = resolve_cli_roots(home_root=home_root, data_root="state-data")

    assert roots.home_root == home_root.resolve()
    assert roots.data_root == (home_root / "state-data").resolve()


def test_resolve_cli_identity_db_path_prefers_configured_db_path() -> None:
    config = SimpleNamespace(
        identity=SimpleNamespace(
            db_path="/tmp/identity-new.db",
            root="/tmp/identity-legacy.db",
        )
    )

    assert (
        resolve_cli_identity_db_path(config) == Path("/tmp/identity-new.db").resolve()
    )


def test_resolve_cli_identity_db_path_falls_back_to_roots(tmp_path) -> None:
    roots = resolve_cli_roots(home_root=tmp_path / "home", data_root="state-data")

    assert (
        resolve_cli_identity_db_path(None, roots=roots)
        == (roots.data_root / "identity" / "identity.db").resolve()
    )


def test_resolve_cli_policy_db_path_falls_back_to_roots(tmp_path) -> None:
    roots = resolve_cli_roots(home_root=tmp_path / "home", data_root="state-data")

    assert (
        resolve_cli_policy_db_path(roots=roots)
        == (roots.data_root / "policy" / "policy.db").resolve()
    )


def test_infer_workspace_home_root_handles_repo_root_and_openminion_subdir(
    tmp_path,
) -> None:
    repo_root = tmp_path / "workspace"
    openminion_root = repo_root / "openminion"
    test_configs = repo_root / "test-configs"
    openminion_root.mkdir(parents=True)
    test_configs.mkdir(parents=True)

    assert infer_workspace_home_root(repo_root) == repo_root.resolve()
    assert infer_workspace_home_root(openminion_root) == repo_root.resolve()


def test_resolve_cli_tool_provider_specs_and_dispatch_map_handles_dispatch_failures() -> (
    None
):
    spec = SimpleNamespace(name="weather", description="Weather", parameters={})

    class _RuntimeTools:
        def model_provider_specs(self):
            return [spec]

        def model_runtime_dispatch_map(self):
            raise RuntimeError("boom")

    specs, dispatch_map = resolve_cli_tool_provider_specs_and_dispatch_map(
        _RuntimeTools()
    )

    assert specs == [spec]
    assert dispatch_map == {}


def test_resolve_cli_tool_provider_specs_and_dispatch_map_merges_prompt_visible_runtime_tools() -> (
    None
):
    canonical = SimpleNamespace(name="weather", description="Weather", parameters={})
    prompt_visible = SimpleNamespace(
        name="mcp.fixture.echo_text",
        prompt_visible_runtime_name=True,
        runtime_binding_id="runtime.mcp.fixture.echo_text",
    )

    class _RuntimeTools:
        _tools = {"mcp.fixture.echo_text": prompt_visible}

        def model_provider_specs(self):
            return [canonical]

        def model_runtime_dispatch_map(self):
            return {"weather": {"runtime_binding_id": "runtime.weather.current"}}

        def provider_spec_for_name(self, name: str):
            if name == "mcp.fixture.echo_text":
                return SimpleNamespace(
                    name=name,
                    description="Echo",
                    parameters={"type": "object"},
                )
            return None

    specs, dispatch_map = resolve_cli_tool_provider_specs_and_dispatch_map(
        _RuntimeTools()
    )

    assert [spec.name for spec in specs] == ["mcp.fixture.echo_text", "weather"]
    assert dispatch_map["mcp.fixture.echo_text"] == {
        "runtime_binding_id": "runtime.mcp.fixture.echo_text",
        "runtime_tool_name": "mcp.fixture.echo_text",
    }
