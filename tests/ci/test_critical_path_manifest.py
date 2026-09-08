"""Keep the fast regression manifest executable and easy to extend."""

from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
MANIFEST = Path(__file__).with_name("critical-path.tsv")


def test_critical_path_manifest_names_existing_tests_once() -> None:
    rows = [
        line.split("\t")
        for line in MANIFEST.read_text(encoding="utf-8").splitlines()
        if line and not line.startswith("#")
    ]

    assert rows
    assert all(len(row) == 2 for row in rows)

    paths = [path for _, path in rows]
    assert len(paths) == len(set(paths))
    assert all(path.startswith("tests/") for path in paths)
    assert all((REPO_ROOT / path).is_file() for path in paths)
