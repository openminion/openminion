from __future__ import annotations

from pathlib import Path


def test_no_top_level_openminion_shim_packages_left() -> None:
    src_root = Path(__file__).resolve().parents[1] / "src"
    shim_dirs = sorted(p.name for p in src_root.glob("openminion_*") if p.is_dir())
    assert shim_dirs == []
