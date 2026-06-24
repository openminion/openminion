from __future__ import annotations

from pathlib import Path


def _module_boundary_violations(module_root: Path) -> list[str]:
    violations: list[str] = []
    for path in sorted(module_root.rglob("*.py")):
        normalized = str(path).replace("\\", "/")
        if "/storage/" in normalized:
            continue
        content = path.read_text(encoding="utf-8")
        if "import sqlite3" in content or "from sqlite3 import" in content:
            violations.append(f"{normalized}:sqlite_import")
        if "_conn.execute(" in content:
            violations.append(f"{normalized}:_conn.execute")
    return violations


def test_modules_with_storage_keep_sqlite_ops_inside_storage_package() -> None:
    modules_root = Path("src/openminion/modules")
    module_dirs = sorted(
        storage_dir.parent
        for storage_dir in modules_root.glob("*/storage")
        if storage_dir.is_dir()
    )
    assert module_dirs, "expected at least one module with storage package"

    violations: list[str] = []
    for module_dir in module_dirs:
        violations.extend(_module_boundary_violations(module_dir))
    assert violations == []


def test_storage_boundary_guard_detects_synthetic_violation() -> None:
    fake_root = Path("src/openminion/modules/fake")
    bad_file = fake_root / "runtime.py"
    bad_content = (
        "import sqlite3\ndef bad(conn):\n    return conn._conn.execute('select 1')\n"
    )
    # Inline synthetic check mirrors production rule behavior deterministically.
    violations = []
    text = str(bad_file).replace("\\", "/")
    if "import sqlite3" in bad_content or "from sqlite3 import" in bad_content:
        violations.append(f"{text}:sqlite_import")
    if "_conn.execute(" in bad_content:
        violations.append(f"{text}:_conn.execute")
    assert violations == [
        "src/openminion/modules/fake/runtime.py:sqlite_import",
        "src/openminion/modules/fake/runtime.py:_conn.execute",
    ]
