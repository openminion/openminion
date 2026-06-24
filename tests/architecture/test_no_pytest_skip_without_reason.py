from __future__ import annotations

import ast
from pathlib import Path


TESTS_ROOT = Path(__file__).resolve().parents[1]


def _is_pytest_mark_call(node: ast.AST, mark_name: str) -> bool:
    if not isinstance(node, ast.Call):
        return False
    func = node.func
    return (
        isinstance(func, ast.Attribute)
        and func.attr == mark_name
        and isinstance(func.value, ast.Attribute)
        and func.value.attr == "mark"
        and isinstance(func.value.value, ast.Name)
        and func.value.value.id == "pytest"
    )


def _is_pytest_mark_attr(node: ast.AST, mark_name: str) -> bool:
    return (
        isinstance(node, ast.Attribute)
        and node.attr == mark_name
        and isinstance(node.value, ast.Attribute)
        and node.value.attr == "mark"
        and isinstance(node.value.value, ast.Name)
        and node.value.value.id == "pytest"
    )


def _has_non_empty_reason_argument(node: ast.Call) -> bool:
    if node.args:
        try:
            first = ast.literal_eval(node.args[0])
        except Exception:
            return True
        return bool(str(first).strip())
    for keyword in node.keywords:
        if keyword.arg != "reason":
            continue
        try:
            value = ast.literal_eval(keyword.value)
        except Exception:
            return True
        return bool(str(value).strip())
    return False


def _has_non_empty_skipif_reason(node: ast.Call) -> bool:
    for keyword in node.keywords:
        if keyword.arg != "reason":
            continue
        try:
            value = ast.literal_eval(keyword.value)
        except Exception:
            return True
        return bool(str(value).strip())
    return False


def _is_pytest_skip_call(node: ast.AST) -> bool:
    return (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "skip"
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id == "pytest"
    )


def _collect_reasonless_skips() -> list[str]:
    violations: list[str] = []
    for path in sorted(TESTS_ROOT.rglob("*.py")):
        if path.name == Path(__file__).name:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
                for decorator in node.decorator_list:
                    if _is_pytest_mark_attr(decorator, "skip"):
                        violations.append(
                            f"{path}:{decorator.lineno}: bare @pytest.mark.skip without reason"
                        )
                    elif _is_pytest_mark_call(decorator, "skip"):
                        if not _has_non_empty_reason_argument(decorator):
                            violations.append(
                                f"{path}:{decorator.lineno}: @pytest.mark.skip missing non-empty reason"
                            )
                    elif _is_pytest_mark_call(decorator, "skipif"):
                        if not _has_non_empty_skipif_reason(decorator):
                            violations.append(
                                f"{path}:{decorator.lineno}: @pytest.mark.skipif missing non-empty reason="
                            )
            elif _is_pytest_skip_call(node):
                if not _has_non_empty_reason_argument(node):
                    violations.append(
                        f"{path}:{node.lineno}: pytest.skip missing non-empty reason"
                    )
    return violations


def test_all_skip_surfaces_carry_reasons() -> None:
    violations = _collect_reasonless_skips()
    assert not violations, (
        "Found reasonless skip forms in tests/.\n"
        "Every `@pytest.mark.skip`, `@pytest.mark.skipif`, and `pytest.skip(...)` "
        "must carry a non-empty human-readable reason.\n\n" + "\n".join(violations)
    )
