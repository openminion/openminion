from __future__ import annotations

from pathlib import Path

from scripts.validate.memory_boundary import validate


def _write_memory_file(root: Path, name: str, text: str) -> None:
    path = root / "src" / "openminion" / "services" / "agent" / "memory" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def test_boundary_accepts_thin_alias(tmp_path: Path) -> None:
    _write_memory_file(tmp_path, "__init__.py", "")
    _write_memory_file(
        tmp_path, "capsule.py", "def normalize_memory_provider(x):\n    return x\n"
    )
    _write_memory_file(
        tmp_path,
        "extraction.py",
        '"""Compatibility aliases for module-owned memory extraction helpers."""\n'
        "from openminion.modules.memory.runtime.extraction.records import _content_text\n"
        '__all__ = ["_content_text"]\n',
    )

    assert validate(tmp_path) == []


def test_boundary_rejects_domain_logic_in_alias_file(tmp_path: Path) -> None:
    _write_memory_file(tmp_path, "__init__.py", "")
    _write_memory_file(tmp_path, "capsule.py", "")
    _write_memory_file(
        tmp_path,
        "extraction.py",
        '"""Compatibility aliases for module-owned memory extraction helpers."""\n'
        "def _extract_facts(text):\n"
        "    return []\n",
    )

    errors = validate(tmp_path)
    assert any("defines runtime logic" in error for error in errors)


def test_boundary_rejects_gateway_import_from_service_memory_domain(
    tmp_path: Path,
) -> None:
    _write_memory_file(tmp_path, "__init__.py", "")
    _write_memory_file(tmp_path, "capsule.py", "")
    _write_memory_file(
        tmp_path,
        "gateway_adapter.py",
        "from openminion.services.agent.memory.extraction import _content_text\n",
    )

    errors = validate(tmp_path)
    assert any("imports service memory domain owner" in error for error in errors)
