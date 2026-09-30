"""Report which OpenMinion release milestones are actually public on main."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path

from packaging.version import Version

from scripts.ci.publish_runtime_manifest import BASE, RAW


DESKTOP_REPOSITORY = "openminion/desktop"
CERTIFICATION_PATH = re.compile(
    r"releases/runtime-certification/v1/qualification/"
    r"(?P<version>\d+\.\d+\.\d+)/[A-Za-z0-9][A-Za-z0-9._-]{0,127}\.json"
)
COMMIT = re.compile(r"[a-f0-9]{40}")
DIGEST = re.compile(r"[a-f0-9]{64}")


def _is_certified_source(record: dict, version: str) -> bool:
    compatibility = record.get("desktop_compatibility")
    certification = record.get("certification")
    if not isinstance(compatibility, dict) or not isinstance(certification, dict):
        return False
    minimum = compatibility.get("min_version")
    maximum = compatibility.get("max_version_exclusive")
    if not isinstance(minimum, str) or not isinstance(maximum, str):
        return False
    try:
        valid_range = Version(minimum) < Version(maximum)
    except ValueError:
        return False
    path = certification.get("path")
    path_match = CERTIFICATION_PATH.fullmatch(path) if isinstance(path, str) else None
    return (
        valid_range
        and certification.get("repository") == DESKTOP_REPOSITORY
        and isinstance(certification.get("commit"), str)
        and COMMIT.fullmatch(certification["commit"]) is not None
        and path_match is not None
        and path_match.group("version") == version
        and isinstance(certification.get("sha256"), str)
        and DIGEST.fullmatch(certification["sha256"]) is not None
    )


def _records(repository: Path, distribution: str, version: str) -> list[dict]:
    feed = json.loads(
        (repository / BASE / distribution / "channels/stable.json").read_text()
    )
    if (
        feed.get("schema_version"),
        feed.get("product"),
        feed.get("distribution"),
        feed.get("channel"),
    ) != (1, "openminion", distribution, "stable"):
        raise ValueError("runtime feed identity is invalid")
    records = []
    for reference in feed["releases"]:
        if reference["runtime_version"] != version:
            continue
        path_pattern = (
            rf"{re.escape(BASE)}/{re.escape(distribution)}/releases/"
            rf"{re.escape(version)}/(?P<release_id>[A-Za-z0-9][A-Za-z0-9._-]{{0,127}})\.json"
        )
        match = re.fullmatch(
            re.escape(RAW) + rf"/[a-f0-9]{{40}}/(?P<path>{path_pattern})",
            reference["manifest_url"],
        )
        if match is None:
            raise ValueError("runtime record URL does not match its feed identity")
        path = repository / match.group("path")
        content = path.read_bytes()
        digest = reference.get("manifest_sha256")
        if (
            not isinstance(digest, str)
            or DIGEST.fullmatch(digest) is None
            or hashlib.sha256(content).hexdigest() != digest
        ):
            raise ValueError("runtime record differs from its feed reference")
        record = json.loads(content)
        if (
            record.get("schema_version"),
            record.get("product"),
            record.get("distribution"),
            record.get("channel"),
            record.get("promotion_status"),
            record.get("runtime_version"),
            record.get("release_id"),
        ) != (
            1,
            "openminion",
            distribution,
            "stable",
            "promoted",
            version,
            match.group("release_id"),
        ):
            raise ValueError(
                "runtime record identity does not match its feed reference"
            )
        if distribution == "pypi" and (
            not isinstance(record.get("pypi"), dict)
            or record["pypi"].get("package") != "openminion"
            or record["pypi"].get("version") != version
        ):
            raise ValueError(
                "source package identity does not match its feed reference"
            )
        if distribution == "binary" and not record.get("artifacts"):
            raise ValueError("binary record has no artifacts")
        records.append(record)
    return records


def release_status(repository: Path, version: str) -> dict:
    Version(version)
    source = _records(repository, "pypi", version)
    binary = _records(repository, "binary", version)
    certified = [record for record in source if _is_certified_source(record, version)]
    return {
        "runtime_version": version,
        "production_package_recorded": bool(source),
        "source_manifest_published": bool(source),
        "desktop_source_update_certified": bool(certified),
        "binary_runtime_published": bool(binary),
        "full_public_e2e": False,
        "full_public_e2e_reason": (
            "requires separately recorded packaged public upgrade, restart, "
            "same-chat, and owned-daemon acceptance"
        ),
    }


def latest_version(repository: Path) -> str:
    feed = json.loads((repository / BASE / "pypi/channels/stable.json").read_text())
    if not feed["releases"]:
        raise ValueError("source stable feed is empty")
    return str(max(Version(item["runtime_version"]) for item in feed["releases"]))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repository", type=Path, default=Path("."))
    parser.add_argument("--version")
    parser.add_argument("--latest", action="store_true")
    parser.add_argument(
        "--require",
        choices=("source", "desktop-source", "binary"),
        help="Fail unless the selected public milestone is complete",
    )
    parser.add_argument("--github-summary", type=Path)
    args = parser.parse_args()
    if bool(args.version) == args.latest:
        parser.error("choose exactly one of --version or --latest")
    version = latest_version(args.repository) if args.latest else args.version
    status = release_status(args.repository, version)
    required_field = {
        "source": "source_manifest_published",
        "desktop-source": "desktop_source_update_certified",
        "binary": "binary_runtime_published",
    }.get(args.require)
    if required_field and not status[required_field]:
        raise SystemExit(
            f"{args.require} release milestone is not complete for {version}"
        )
    rendered = json.dumps(status, indent=2, sort_keys=True)
    print(rendered)
    if args.github_summary:
        lines = [
            f"## Runtime release status: {version}",
            "",
            f"- Production package recorded: `{status['production_package_recorded']}`",
            f"- Source manifest published: `{status['source_manifest_published']}`",
            f"- Desktop source update certified: `{status['desktop_source_update_certified']}`",
            f"- Binary runtime published: `{status['binary_runtime_published']}`",
            "- Full public E2E: `False` (metadata alone cannot prove it)",
            "",
        ]
        args.github_summary.write_text("\n".join(lines), encoding="utf-8")


if __name__ == "__main__":
    main()
