from __future__ import annotations

import ast
import json

from .cli_entrypoint_paths import (
    module_cli_entrypoint_path,
    module_cli_fixture_path,
    openminion_modules_root,
)


def test_non_exempt_modules_satisfy_cli_inventory_contract() -> None:
    modules_root = openminion_modules_root()
    exemptions = json.loads(
        module_cli_fixture_path("cli_exemptions.json").read_text(encoding="utf-8")
    )
    exempt_modules = {entry["module"] for entry in exemptions["exemptions"]}

    errors: list[str] = []
    for module_dir in sorted(
        p for p in modules_root.iterdir() if p.is_dir() and (p / "__init__.py").exists()
    ):
        module_name = module_dir.name
        if module_name in exempt_modules:
            continue

        cli_path = module_cli_entrypoint_path(module_dir)
        main_path = module_dir / "__main__.py"
        if cli_path is None:
            errors.append(f"{module_name}: missing cli entrypoint")
            continue
        if not main_path.exists():
            errors.append(f"{module_name}: missing __main__.py")
            continue

        tree = ast.parse(cli_path.read_text(encoding="utf-8"))
        main_defs = [
            n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "main"
        ]
        if not main_defs:
            errors.append(f"{module_name}: cli.main is missing")
            continue
        fn = main_defs[-1]
        args = fn.args.args
        if len(args) != 1 or args[0].arg != "argv":
            errors.append(f"{module_name}: main signature must be main(argv=...)")
        elif args[0].annotation is None:
            errors.append(f"{module_name}: argv parameter missing annotation")

        defaults = fn.args.defaults
        if (
            len(defaults) != 1
            or not isinstance(defaults[0], ast.Constant)
            or defaults[0].value is not None
        ):
            errors.append(f"{module_name}: argv default must be None")
        return_annotation = ast.unparse(fn.returns) if fn.returns is not None else ""
        if return_annotation != "int":
            errors.append(
                f"{module_name}: main return annotation must be int (found {return_annotation or 'None'})"
            )

    assert not errors, "\n".join(errors)
