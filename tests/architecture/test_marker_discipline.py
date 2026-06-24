from __future__ import annotations

import ast
import re
from pathlib import Path


TESTS_ROOT = Path(__file__).resolve().parents[1]
E2E_ROOT = TESTS_ROOT / "e2e"


def _has_module_level_pytestmark(tree: ast.AST, marker_name: str) -> bool:
    if not isinstance(tree, ast.Module):
        return False
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        if not any(
            isinstance(target, ast.Name) and target.id == "pytestmark"
            for target in node.targets
        ):
            continue
        # Single attribute form: pytest.mark.<name>
        if _is_pytest_mark(node.value, marker_name):
            return True
        # List form: [pytest.mark.<name>, ...]
        if isinstance(node.value, (ast.List, ast.Tuple)):
            for elt in node.value.elts:
                if _is_pytest_mark(elt, marker_name):
                    return True
    return False


def _is_pytest_mark(node: ast.AST, marker_name: str) -> bool:
    target = node
    if isinstance(target, ast.Call):
        target = target.func
    return (
        isinstance(target, ast.Attribute)
        and target.attr == marker_name
        and isinstance(target.value, ast.Attribute)
        and target.value.attr == "mark"
        and isinstance(target.value.value, ast.Name)
        and target.value.value.id == "pytest"
    )


def _every_test_function_has_marker(tree: ast.AST, marker_name: str) -> bool:
    test_functions = [
        node
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.name.startswith("test_")
    ]
    if not test_functions:
        return True
    for fn in test_functions:
        if not any(_is_pytest_mark(dec, marker_name) for dec in fn.decorator_list):
            return False
    return True


def _file_carries_marker(path: Path, marker_name: str) -> bool:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    return _has_module_level_pytestmark(
        tree, marker_name
    ) or _every_test_function_has_marker(tree, marker_name)


def test_all_e2e_files_have_marker() -> None:
    if not E2E_ROOT.is_dir():
        return  # nothing to enforce
    violations: list[str] = []
    for path in sorted(E2E_ROOT.rglob("test_*.py")):
        if not _file_carries_marker(path, "e2e"):
            violations.append(str(path))
    assert not violations, (
        "The following tests/e2e/ files are missing the e2e marker.\n"
        "Add `pytestmark = pytest.mark.e2e` at module top OR `@pytest.mark.e2e` "
        "on every test function.\n\n" + "\n".join(violations)
    )


def test_all_postgres_gated_files_have_marker() -> None:
    violations: list[str] = []
    for path in sorted(TESTS_ROOT.rglob("test_*.py")):
        if path == Path(__file__):
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            continue
        if "OPENMINION_TEST_POSTGRES_URL" not in text:
            continue
        tree = ast.parse(text)
        if not _has_module_level_pytestmark(tree, "postgres"):
            violations.append(str(path))
    assert not violations, (
        "The following postgres-gated test files are missing the postgres marker.\n"
        "Add `pytestmark = pytest.mark.postgres` at module top.\n\n"
        + "\n".join(violations)
    )


# Match `pytest.skip("...not available...")` or `pytest.skip("...not importable...")`.
# We accept f-strings (JoinedStr) by inspecting the raw source line because ast.literal_eval
# can't evaluate them; the regex below covers both literal strings and f-strings.
_FORBIDDEN_SKIP_PATTERN = re.compile(
    r"pytest\.skip\s*\(\s*f?[\"'][^\"']*\b(?:not available|not importable)\b",
    re.IGNORECASE,
)


def test_no_silent_not_available_skips() -> None:
    violations: list[str] = []
    for path in sorted(TESTS_ROOT.rglob("*.py")):
        if path.name == "conftest.py":
            continue
        if path == Path(__file__):
            continue
        text = path.read_text(encoding="utf-8")
        for lineno, line in enumerate(text.splitlines(), start=1):
            if _FORBIDDEN_SKIP_PATTERN.search(line):
                if "Symlink" in line or "browser binary" in line or "tqdm" in line:
                    continue
                violations.append(f"{path}:{lineno}: {line.strip()}")
    assert not violations, (
        "Found silent shim-import skips. Convert to `pytest.importorskip(...)` for "
        "genuine optional deps, or to a hard import if the module is in-tree (in which "
        "case its absence is shim breakage, not optional-dep behavior).\n\n"
        + "\n".join(violations)
    )
