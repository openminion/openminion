from __future__ import annotations

import tomllib
from pathlib import Path

from openminion import __version__ as package_version
from openminion.base.version import OPENMINION_VERSION

SHARED_VERSION_OWNER_FILES = (
    Path("src/openminion/modules/controlplane/channels/telegram/__init__.py"),
    Path("src/openminion/services/cron/__init__.py"),
    Path("src/openminion/modules/session/__init__.py"),
    Path("src/openminion/modules/memory/__init__.py"),
    Path("src/openminion/services/runtime/__init__.py"),
    Path("src/openminion/modules/task/__init__.py"),
    Path("src/openminion/modules/tool/__init__.py"),
    Path("src/openminion/modules/identity/__init__.py"),
    Path("src/openminion/tools/gws/__init__.py"),
    Path("src/openminion/cli/commands/debug/providers/core.py"),
    Path("src/openminion/modules/identity/controlplane/main.py"),
    Path("src/openminion/modules/storage/templates/alembic/env.py"),
    Path("src/openminion/tools/mcp/constants.py"),
    Path("src/openminion/tools/weather/providers/openmeteo/constants.py"),
)


def test_package_release_artifacts_exist() -> None:
    root = Path(__file__).resolve().parents[1]
    assert (root / "LICENSE").is_file()
    assert (root / "RELEASING.md").is_file()
    assert (root / "pyproject.toml").is_file()


def test_package_readme_mentions_release_runbook() -> None:
    readme = (Path(__file__).resolve().parents[1] / "README.md").read_text()
    assert "RELEASING.md" in readme
    assert "Apache-2.0" in readme


def test_package_policy_and_validation_docs_exist() -> None:
    root = Path(__file__).resolve().parents[1]
    assert (root / "API_COMPATIBILITY.md").is_file()
    assert (root / "docs" / "README.md").is_file()
    assert (root / "docs" / "certification-readiness-matrix.md").is_file()
    assert (root / "docs" / "runtime-surfaces.md").is_file()
    assert (root / "docs" / "source-tree-owner-map.md").is_file()
    assert (root / "docs" / "standalone-claim-alignment.md").is_file()
    assert (root / "docs" / "testing-and-validation.md").is_file()
    assert (root / "examples").is_dir()


def test_package_readme_mentions_package_docs_and_validation() -> None:
    readme = (Path(__file__).resolve().parents[1] / "README.md").read_text()
    assert "docs/README.md" in readme
    assert "docs/runtime-surfaces.md" in readme
    assert "API_COMPATIBILITY.md" in readme
    assert "RELEASING.md" in readme
    assert "docs/source-tree-owner-map.md" in readme
    assert "docs/testing-and-validation.md" in readme


def test_package_metadata_declares_canonical_public_urls() -> None:
    pyproject = tomllib.loads(
        (Path(__file__).resolve().parents[1] / "pyproject.toml").read_text()
    )

    assert pyproject["project"]["urls"] == {
        "Homepage": "https://www.openminion.com",
        "Repository": "https://github.com/OpenMinion/openminion",
        "Documentation": "https://www.openminion.com/docs",
        "Changelog": "https://github.com/OpenMinion/openminion/blob/main/CHANGELOG.md",
    }


def test_package_version_owner_matches_public_metadata() -> None:
    pyproject = tomllib.loads(
        (Path(__file__).resolve().parents[1] / "pyproject.toml").read_text()
    )
    assert OPENMINION_VERSION == pyproject["project"]["version"]
    assert package_version == OPENMINION_VERSION


def test_current_package_version_is_centralized_across_runtime_surfaces() -> None:
    root = Path(__file__).resolve().parents[1]
    init_text = (root / "src" / "openminion" / "__init__.py").read_text()
    assert "__version__ = OPENMINION_VERSION" in init_text
    assert f'__version__ = "{OPENMINION_VERSION}"' not in init_text

    quoted_version = f'"{OPENMINION_VERSION}"'
    for relative_path in SHARED_VERSION_OWNER_FILES:
        text = (root / relative_path).read_text()
        assert "OPENMINION_VERSION" in text
        assert quoted_version not in text
