"""Classify one trusted release event before build and publication work."""

from __future__ import annotations

import argparse
import runpy
from pathlib import Path

from packaging.version import InvalidVersion, Version


SUPPORTED_PRERELEASE_MARKERS = ("alpha", "beta", "rc")
MILESTONE_VERSION = Version(".".join(("0", "1", "0")))
MILESTONE_CONTRACT_MARKERS = {
    Path("README.md"): "0.1.x compatibility contract",
    Path("API_COMPATIBILITY.md"): "## 0.1.x compatibility contract",
    Path("RELEASING.md"): "## 0.1 milestone gate",
}


def source_version_text(repository: Path) -> str:
    owner = repository / "src/openminion/base/version.py"
    return str(runpy.run_path(owner)["OPENMINION_VERSION"])


def source_version(repository: Path) -> Version:
    return Version(source_version_text(repository))


def validate_milestone_contract(repository: Path, version: Version) -> None:
    """Require the documented compatibility boundary before 0.1+ publication."""
    if version.release < MILESTONE_VERSION.release:
        return

    missing: list[str] = []
    for path, marker in MILESTONE_CONTRACT_MARKERS.items():
        target = repository / path
        if not target.is_file() or marker not in target.read_text(encoding="utf-8"):
            missing.append(str(path))
    if missing:
        raise ValueError(
            "0.1+ publication requires the milestone contract in: " + ", ".join(missing)
        )


def release_target(
    *,
    event_name: str,
    ref: str,
    requested_target: str,
    repository: Path,
) -> str:
    expected_text = source_version_text(repository)
    expected_version = Version(expected_text)
    validate_milestone_contract(repository, expected_version)

    if event_name == "workflow_dispatch":
        if ref != "refs/heads/main":
            raise ValueError("final TestPyPI publication must be dispatched from main")
        if requested_target != "testpypi":
            raise ValueError("manual release dispatch supports only testpypi")
        return "testpypi"

    if event_name != "push" or not ref.startswith("refs/tags/v"):
        raise ValueError(
            "release publication requires a manual dispatch or v* tag push"
        )

    tag = ref.removeprefix("refs/tags/v")
    try:
        version = Version(tag)
    except InvalidVersion as exc:
        raise ValueError(f"invalid release tag version: {tag}") from exc

    if tag != expected_text:
        raise ValueError(
            f"tag version {tag} does not match source version {expected_text}"
        )
    if version.is_devrelease or version.local is not None:
        raise ValueError("development and local versions are not publishable")
    if version.is_prerelease:
        if not any(marker in tag.lower() for marker in SUPPORTED_PRERELEASE_MARKERS):
            raise ValueError("prerelease tags must use alpha, beta, or rc spelling")
        return "testpypi"
    return "pypi"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--event-name", required=True)
    parser.add_argument("--ref", required=True)
    parser.add_argument("--requested-target", default="")
    parser.add_argument("--repository", type=Path, default=Path.cwd())
    parser.add_argument("--github-output", type=Path)
    args = parser.parse_args()

    target = release_target(
        event_name=args.event_name,
        ref=args.ref,
        requested_target=args.requested_target,
        repository=args.repository,
    )
    print(target)
    if args.github_output is not None:
        with args.github_output.open("a") as output:
            output.write(f"target={target}\n")


if __name__ == "__main__":
    main()
