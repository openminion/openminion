from __future__ import annotations

from pathlib import Path


def _collect_sqlite_boundary_violations(
    file_map: dict[str, str], *, allow_under: tuple[str, ...]
) -> list[str]:
    violations: list[str] = []
    for path, content in sorted(file_map.items()):
        normalized = path.replace("\\", "/")
        if any(normalized.startswith(prefix) for prefix in allow_under):
            continue
        if "sqlite3" in content:
            violations.append(f"{normalized}:sqlite3")
        if "_conn.execute(" in content:
            violations.append(f"{normalized}:_conn.execute")
    return violations


def _read_module_sources(root: Path) -> dict[str, str]:
    mapping: dict[str, str] = {}
    for path in root.rglob("*.py"):
        mapping[str(path)] = path.read_text(encoding="utf-8")
    return mapping


def test_retrieve_runtime_layer_avoids_direct_connection_exec() -> None:
    retrieve_path = Path("src/openminion/modules/retrieve/runtime/retrieve.py")
    content = retrieve_path.read_text(encoding="utf-8")
    assert "self._conn.execute(" not in content


def test_session_module_boundaries_keep_sqlite_inside_storage_package() -> None:
    root = Path("src/openminion/modules/session")
    sources = _read_module_sources(root)
    violations = _collect_sqlite_boundary_violations(
        sources,
        allow_under=("src/openminion/modules/session/storage/",),
    )
    assert violations == []


def test_session_boundary_guard_helper_detects_violation() -> None:
    violations = _collect_sqlite_boundary_violations(
        {
            "src/openminion/modules/session/fake_runtime.py": (
                "import sqlite3\n"
                "def bad(conn):\n"
                "    return conn._conn.execute('select 1')\n"
            )
        },
        allow_under=("src/openminion/modules/session/storage/",),
    )
    assert violations == [
        "src/openminion/modules/session/fake_runtime.py:sqlite3",
        "src/openminion/modules/session/fake_runtime.py:_conn.execute",
    ]


def test_skill_surface_does_not_catch_sqlite_error_type() -> None:
    skill_path = Path("src/openminion/modules/skill/runtime/skill.py")
    content = skill_path.read_text(encoding="utf-8")
    assert "except sqlite3.Error" not in content
