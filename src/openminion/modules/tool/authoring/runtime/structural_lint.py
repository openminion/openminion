import ast
import sys
from dataclasses import dataclass
from typing import Any


class StructuralLintError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True)
class StructuralLintResult:
    entry_function: str
    stdlib_imports: tuple[str, ...]
    external_imports: tuple[str, ...]
    test_count: int


def structural_lint(
    *,
    local_name: str,
    source_code: str,
    unit_tests_source: str,
    args_schema: dict[str, Any],
    dependencies: list[str],
    allowed_dependencies: set[str],
) -> StructuralLintResult:
    try:
        source_tree = ast.parse(source_code)
    except SyntaxError as exc:  # pragma: no cover - python owns exact syntax text
        raise StructuralLintError("INVALID_SOURCE", str(exc)) from exc

    try:
        tests_tree = ast.parse(unit_tests_source)
    except SyntaxError as exc:  # pragma: no cover - python owns exact syntax text
        raise StructuralLintError("INVALID_SOURCE", str(exc)) from exc

    functions = [node for node in source_tree.body if isinstance(node, ast.FunctionDef)]
    if not functions:
        raise StructuralLintError(
            "INVALID_SOURCE", "source_code must define a function"
        )

    entry = next((node for node in functions if node.name == local_name), None)
    if entry is None:
        entry = functions[0] if len(functions) == 1 else None
    if entry is None:
        raise StructuralLintError(
            "SIGNATURE_MISMATCH",
            "source_code must define exactly one function or one matching the tool name",
        )

    properties = (
        args_schema.get("properties", {}) if isinstance(args_schema, dict) else {}
    )
    required = args_schema.get("required", []) if isinstance(args_schema, dict) else []
    required_names = [
        str(item).strip()
        for item in (required if isinstance(required, list) else [])
        if str(item).strip()
    ]
    if required_names:
        params = [arg.arg for arg in entry.args.args]
        missing = [item for item in required_names if item not in params]
        if missing:
            raise StructuralLintError(
                "SIGNATURE_MISMATCH",
                f"entry function missing required args: {', '.join(missing)}",
            )
    elif properties and not isinstance(properties, dict):
        raise StructuralLintError(
            "INVALID_SCHEMA", "args_schema.properties must be an object"
        )

    imports = _collect_imports(source_tree)
    stdlib = tuple(sorted(name for name in imports if name in sys.stdlib_module_names))
    external = tuple(
        sorted(name for name in imports if name not in sys.stdlib_module_names)
    )
    declared = {str(item).strip() for item in dependencies if str(item).strip()}
    missing_declared = [name for name in external if name not in declared]
    if missing_declared:
        raise StructuralLintError(
            "DEP_NOT_ALLOWED",
            f"dependencies missing imported modules: {', '.join(missing_declared)}",
        )

    disallowed = [name for name in declared if name not in allowed_dependencies]
    if disallowed:
        raise StructuralLintError(
            "DEP_NOT_ALLOWED",
            f"dependencies not in allowed set: {', '.join(sorted(disallowed))}",
        )

    test_count = _count_tests(tests_tree)
    return StructuralLintResult(
        entry_function=entry.name,
        stdlib_imports=stdlib,
        external_imports=external,
        test_count=test_count,
    )


def _collect_imports(tree: ast.AST) -> set[str]:
    imports: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                token = str(alias.name or "").split(".", 1)[0].strip()
                if token:
                    imports.add(token)
        elif isinstance(node, ast.ImportFrom):
            token = str(node.module or "").split(".", 1)[0].strip()
            if token:
                imports.add(token)
    return imports


def _count_tests(tree: ast.AST) -> int:
    return sum(
        1
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef) and str(node.name).startswith("test_")
    )


__all__ = [
    "StructuralLintError",
    "StructuralLintResult",
    "structural_lint",
]
