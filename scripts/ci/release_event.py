"""Classify one trusted release event before build and publication work."""

from __future__ import annotations

import argparse
import runpy
from pathlib import Path

from packaging.version import InvalidVersion, Version


SUPPORTED_PRERELEASE_MARKERS = ("alpha", "beta", "rc")


def source_version(repository: Path) -> Version:
    owner = repository / "src/openminion/base/version.py"
    return Version(str(runpy.run_path(owner)["OPENMINION_VERSION"]))


def release_target(
    *,
    event_name: str,
    ref: str,
    requested_target: str,
    repository: Path,
) -> str:
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

    expected = source_version(repository)
    if version != expected:
        raise ValueError(
            f"tag version {version} does not match source version {expected}"
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
