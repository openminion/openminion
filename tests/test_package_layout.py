from __future__ import annotations

import re
from pathlib import Path


def test_root_layout_stays_clean_and_intentional() -> None:
    root = Path(__file__).resolve().parents[1]

    assert (root / "docs" / "README.md").is_file()
    assert (root / "docs" / "source-tree-owner-map.md").is_file()
    assert (root / "docs" / "testing-and-validation.md").is_file()
    assert (root / "examples").is_dir()

    assert not (root / "fixtures").exists()
    assert not (root / "handoff").exists()
    assert not (root / "docs" / "reference").exists()
    assert not (root / "src" / "openminion" / "README.md").exists()


def test_docs_surface_contains_expected_package_refs() -> None:
    root = Path(__file__).resolve().parents[1] / "docs"

    expected = {
        "certification-readiness-matrix.md",
        "runtime-surfaces.md",
        "source-tree-owner-map.md",
        "standalone-claim-alignment.md",
        "testing-and-validation.md",
    }

    assert expected.issubset({path.name for path in root.iterdir() if path.is_file()})


def test_public_markdown_docs_stay_package_local_and_portable() -> None:
    root = Path(__file__).resolve().parents[1]
    markdown_files = [
        root / "README.md",
        root / "API_COMPATIBILITY.md",
        root / "RELEASING.md",
        root / "docs" / "README.md",
        *sorted(
            path for path in (root / "docs").glob("*.md") if path.name != "README.md"
        ),
    ]

    local_path_markers = ("/Users/", "file://")
    internal_repo_markers = ("docs/discussions/", "docs/trackers/")
    relative_link_pattern = re.compile(r"\]\((?!https?://|mailto:|#)([^)]+)\)")

    for markdown_file in markdown_files:
        content = markdown_file.read_text()
        assert not any(marker in content for marker in local_path_markers), (
            f"{markdown_file} contains a machine-local path"
        )
        assert not any(marker in content for marker in internal_repo_markers), (
            f"{markdown_file} leaks internal repo planning paths"
        )

        for target in relative_link_pattern.findall(content):
            assert not target.startswith("/"), (
                f"{markdown_file} contains an absolute local link target: {target}"
            )
