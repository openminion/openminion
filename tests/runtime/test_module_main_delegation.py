from __future__ import annotations

import ast
import json

from .cli_entrypoint_paths import (
    has_module_cli_entrypoint,
    module_cli_fixture_path,
    openminion_modules_root,
)


def _is_main_guard(test: ast.expr) -> bool:
    if not isinstance(test, ast.Compare):
        return False
    if not isinstance(test.left, ast.Name) or test.left.id != "__name__":
        return False
    if len(test.ops) != 1 or not isinstance(test.ops[0], ast.Eq):
        return False
    if len(test.comparators) != 1:
        return False
    comp = test.comparators[0]
    return isinstance(comp, ast.Constant) and comp.value == "__main__"


def _calls_main(node: ast.AST) -> bool:
    for child in ast.walk(node):
        if (
            isinstance(child, ast.Call)
            and isinstance(child.func, ast.Name)
            and child.func.id == "main"
        ):
            return True
    return False


def test_non_exempt_module_main_files_delegate_to_cli_main() -> None:
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
        if module_name in exempt_modules or not has_module_cli_entrypoint(module_dir):
            continue
        main_path = module_dir / "__main__.py"
        if not main_path.exists():
            errors.append(f"{module_name}: missing __main__.py")
            continue

        source = main_path.read_text(encoding="utf-8")
        tree = ast.parse(source)

        imports_main = False
        for node in tree.body:
            if isinstance(node, ast.ImportFrom):
                if any(alias.name == "main" for alias in node.names):
                    imports_main = True
                    break
        if not imports_main:
            errors.append(f"{module_name}: __main__.py does not import main")
            continue

        guarded_delegate = False
        for node in tree.body:
            if isinstance(node, ast.If) and _is_main_guard(node.test):
                if any(_calls_main(stmt) for stmt in node.body):
                    guarded_delegate = True
                    break
        if not guarded_delegate:
            errors.append(
                f"{module_name}: __main__.py missing guarded main() delegation"
            )

        for node in tree.body:
            if isinstance(node, ast.If) and _is_main_guard(node.test):
                continue
            if _calls_main(node):
                errors.append(
                    f"{module_name}: __main__.py calls main() outside __name__ guard"
                )
                break

    assert not errors, "\n".join(errors)
