from __future__ import annotations

import json

from .cli_entrypoint_paths import (
    module_cli_fixture_path,
    module_cli_shim_path,
    openminion_modules_root,
)


def test_non_exempt_module_clis_define_main_guard() -> None:
    modules_root = openminion_modules_root()
    exemptions = json.loads(
        module_cli_fixture_path("cli_exemptions.json").read_text(encoding="utf-8")
    )
    exempt_modules = {entry["module"] for entry in exemptions["exemptions"]}

    missing: list[str] = []
    for module_dir in sorted(
        p for p in modules_root.iterdir() if p.is_dir() and (p / "__init__.py").exists()
    ):
        if module_dir.name in exempt_modules:
            continue
        shim_path = module_cli_shim_path(module_dir)
        if shim_path is None:
            continue
        text = shim_path.read_text(encoding="utf-8")
        if (
            'if __name__ == "__main__"' not in text
            and "if __name__ == '__main__'" not in text
        ):
            missing.append(module_dir.name)

    assert not missing, f"module cli files missing __main__ guard: {', '.join(missing)}"
