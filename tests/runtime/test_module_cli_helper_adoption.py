from __future__ import annotations

import json

from .cli_entrypoint_paths import (
    has_module_cli_entrypoint,
    module_cli_entrypoint_path,
    module_cli_fixture_path,
    openminion_modules_root,
)


def test_shared_bootstrap_helper_adoption_for_targeted_modules() -> None:
    modules_root = openminion_modules_root()

    exemptions = json.loads(
        module_cli_fixture_path("cli_exemptions.json").read_text(encoding="utf-8")
    )
    exempt_modules = {entry["module"] for entry in exemptions["exemptions"]}
    no_op_policy = json.loads(
        module_cli_fixture_path("cli_bootstrap_policy.json").read_text(encoding="utf-8")
    )
    no_op_modules = {entry["module"] for entry in no_op_policy["no_op_modules"]}

    non_exempt_modules = {
        p.name
        for p in modules_root.iterdir()
        if p.is_dir()
        and (p / "__init__.py").exists()
        and has_module_cli_entrypoint(p)
        and p.name not in exempt_modules
    }
    assert no_op_modules.issubset(non_exempt_modules)
    targeted_modules = non_exempt_modules - no_op_modules
    assert targeted_modules

    for module_name in sorted(targeted_modules):
        cli_path = module_cli_entrypoint_path(modules_root / module_name)
        assert cli_path is not None
        source = cli_path.read_text(encoding="utf-8")
        assert "apply_home_data_root_env(" in source, (
            f"{module_name}: missing apply_home_data_root_env helper adoption"
        )

    for module_name in sorted(no_op_modules):
        cli_path = module_cli_entrypoint_path(modules_root / module_name)
        assert cli_path is not None
        source = cli_path.read_text(encoding="utf-8")
        assert "apply_home_data_root_env(" not in source, (
            f"{module_name}: no-op module should not adopt bootstrap helper"
        )
