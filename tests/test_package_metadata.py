from __future__ import annotations

import subprocess
import tomllib
import re
from pathlib import Path

from openminion import __version__ as package_version
from openminion.base.version import OPENMINION_VERSION
from openminion.modules.llm.reasoning import ThinkingCtl
from openminion.modules.llm.reasoning import __version__ as reasoning_version

VERSION_LITERAL_ALLOWLIST = {
    Path("src/openminion/base/version.py"),
}

VERSION_LITERAL_OWNERS = {
    OPENMINION_VERSION: "OPENMINION_VERSION",
}

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


def test_default_terminal_renderer_dependencies_are_core() -> None:
    pyproject = tomllib.loads(
        (Path(__file__).resolve().parents[1] / "pyproject.toml").read_text()
    )
    dependencies = pyproject["project"]["dependencies"]

    assert any(dep.startswith("prompt-toolkit") for dep in dependencies)
    assert any(dep.startswith("textual") for dep in dependencies)


def test_package_version_owner_matches_public_metadata() -> None:
    pyproject = tomllib.loads(
        (Path(__file__).resolve().parents[1] / "pyproject.toml").read_text()
    )
    assert pyproject["project"]["dynamic"] == ["version"]
    assert "version" not in pyproject["project"]
    assert pyproject["tool"]["setuptools"]["dynamic"]["version"] == {
        "attr": "openminion.base.version.OPENMINION_VERSION"
    }
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


def test_current_package_version_literal_stays_in_version_owner() -> None:
    root = Path(__file__).resolve().parents[1]
    findings: list[str] = []
    tracked_files = subprocess.check_output(
        ["git", "ls-files"],
        cwd=root,
        text=True,
    ).splitlines()
    for raw_path in tracked_files:
        path = root / raw_path
        if not path.is_file():
            continue
        relative_path = Path(raw_path)
        if relative_path in VERSION_LITERAL_ALLOWLIST:
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        for version_literal, owner_name in VERSION_LITERAL_OWNERS.items():
            version_pattern = re.compile(
                rf"(?<![0-9.]){re.escape(version_literal)}(?![0-9A-Za-z.])"
            )
            if version_pattern.search(text):
                findings.append(f"{relative_path}: {owner_name}")

    assert findings == []


def test_openminion_version_has_single_named_owner() -> None:
    root = Path(__file__).resolve().parents[1]
    findings: list[str] = []
    token_pattern = re.compile(r"\bOPENMINION_[A-Z_]*VERSION\b")
    tracked_files = subprocess.check_output(
        ["git", "ls-files"],
        cwd=root,
        text=True,
    ).splitlines()
    for raw_path in tracked_files:
        path = root / raw_path
        if not path.is_file():
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        for token in set(token_pattern.findall(text)):
            if token != "OPENMINION_VERSION":
                findings.append(f"{raw_path}: {token}")

    assert findings == []


def test_reasoning_package_version_uses_canonical_owner() -> None:
    assert reasoning_version == OPENMINION_VERSION
    assert ThinkingCtl().get_version() == OPENMINION_VERSION


def test_scaffold_version_uses_canonical_owner() -> None:
    root = Path(__file__).resolve().parents[1]
    scaffold_text = (
        root / "src" / "openminion" / "cli" / "commands" / "scaffold.py"
    ).read_text()
    assert "OPENMINION_VERSION" in scaffold_text
    assert f'"{OPENMINION_VERSION}"' not in scaffold_text


def test_public_surface_since_map_uses_canonical_owner() -> None:
    root = Path(__file__).resolve().parents[1]
    init_text = (root / "src" / "openminion" / "__init__.py").read_text()
    assert "OPENMINION_VERSION" in init_text
    assert f'"{OPENMINION_VERSION}"' not in init_text
