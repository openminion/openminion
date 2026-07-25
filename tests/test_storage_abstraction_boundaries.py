from __future__ import annotations

from pathlib import Path


def test_retrieve_runtime_layer_avoids_direct_connection_exec() -> None:
    retrieve_path = Path("src/openminion/modules/retrieve/runtime/retrieve.py")
    content = retrieve_path.read_text(encoding="utf-8")
    assert "self._conn.execute(" not in content


def test_skill_surface_does_not_catch_sqlite_error_type() -> None:
    skill_root = Path("src/openminion/modules/skill/runtime/skill")
    sources = [path.read_text(encoding="utf-8") for path in skill_root.rglob("*.py")]
    assert sources
    assert all("except sqlite3.Error" not in content for content in sources)
