"""Create source runtime metadata from the official production PyPI wheel."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from datetime import UTC, datetime
from email.parser import BytesParser
from pathlib import Path
from tempfile import SpooledTemporaryFile
from time import monotonic
from urllib.parse import urlparse
from urllib.request import urlopen
from zipfile import ZipFile

from packaging.version import Version


DESKTOP_REPOSITORY = "openminion/desktop"
DESKTOP_EVIDENCE_BASE = "releases/runtime-certification/v1"
DESKTOP_TARGETS = {"darwin-arm64", "linux-x64", "win32-x64"}
QUALIFICATION_CHECKS = {
    "old_runtime_reply",
    "owned_daemon_shutdown",
    "packaged_startup",
    "prepare_update",
    "runtime_integrity",
    "same_chat_reply",
    "startup_activation",
}
BINARY_QUALIFICATION_CHECKS = {*QUALIFICATION_CHECKS, "native_trust"}


def _stable_three_part_version(value: str, name: str) -> Version:
    if not re.fullmatch(r"\d+\.\d+\.\d+", value):
        raise ValueError(f"{name} must be a three-part stable version")
    parsed = Version(value)
    if str(parsed) != value or parsed.is_prerelease or parsed.is_devrelease:
        raise ValueError(f"{name} must be a three-part stable version")
    return parsed


def evidence_path(kind: str, version: str, evidence_id: str) -> str:
    if kind not in {"qualification", "binary-qualification", "acceptance"}:
        raise ValueError("invalid certification evidence kind")
    _stable_three_part_version(version, "runtime version")
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}", evidence_id):
        raise ValueError("invalid evidence_id")
    return f"{DESKTOP_EVIDENCE_BASE}/{kind}/{version}/{evidence_id}.json"


def validate_binary_qualification_evidence(
    evidence: dict,
    candidate: dict,
    verification: dict,
    release_tag: str,
) -> None:
    """Validate exact packaged-Desktop qualification for immutable binary bytes."""
    if (
        evidence.get("schema_version"),
        evidence.get("kind"),
    ) != (1, "openminion-binary-runtime-qualification"):
        raise ValueError("invalid binary qualification evidence identity")
    runtime = evidence.get("runtime")
    desktop = evidence.get("desktop")
    compatibility = evidence.get("compatibility")
    targets = evidence.get("targets")
    if not all(isinstance(item, dict) for item in (runtime, desktop, compatibility)):
        raise ValueError("binary qualification evidence is incomplete")
    if runtime != {
        "version": candidate.get("runtime_version"),
        "release_tag": release_tag,
    }:
        raise ValueError("binary qualification does not match the runtime Release")
    desktop_version = _stable_three_part_version(
        desktop.get("version", ""), "desktop version"
    )
    if desktop_version == Version("0.0.0"):
        raise ValueError("development Desktop version cannot certify a release")
    if not re.fullmatch(r"[a-f0-9]{40}", desktop.get("source_commit", "")):
        raise ValueError("desktop source commit must be a full SHA")
    if compatibility != candidate.get("desktop_compatibility"):
        raise ValueError("binary qualification compatibility differs from candidate")
    minimum = _stable_three_part_version(
        compatibility.get("min_version", ""), "minimum Desktop version"
    )
    maximum = _stable_three_part_version(
        compatibility.get("max_version_exclusive", ""),
        "maximum Desktop version",
    )
    if not minimum <= desktop_version < maximum:
        raise ValueError("qualified Desktop version is outside its claimed bounds")
    native = verification.get("targets") if isinstance(verification, dict) else None
    if not isinstance(native, dict) or not isinstance(targets, list):
        raise ValueError("binary qualification lacks target evidence")
    candidates = {
        f"{item.get('platform')}-{item.get('arch')}": item
        for item in candidate.get("artifacts", [])
        if isinstance(item, dict)
    }
    if set(candidates) != DESKTOP_TARGETS or len(targets) != len(DESKTOP_TARGETS):
        raise ValueError("binary qualification requires every supported Desktop target")
    actual_targets = set()
    for target in targets:
        if not isinstance(target, dict):
            raise ValueError("invalid binary target qualification evidence")
        identity = f"{target.get('platform')}-{target.get('arch')}"
        candidate_target = candidates.get(identity)
        native_target = native.get(identity)
        checks = target.get("checks")
        cli = (
            candidate_target.get("cli") if isinstance(candidate_target, dict) else None
        )
        daemon = (
            candidate_target.get("daemon")
            if isinstance(candidate_target, dict)
            else None
        )
        if (
            candidate_target is None
            or not isinstance(cli, dict)
            or not isinstance(daemon, dict)
            or not isinstance(native_target, dict)
            or target.get("cli_sha256") != cli.get("sha256")
            or target.get("daemon_sha256") != daemon.get("sha256")
            or target.get("verification_id") != native_target.get("verification_id")
            or not re.fullmatch(r"[a-f0-9]{64}", target.get("package_sha256", ""))
            or not re.fullmatch(r"[a-f0-9]{64}", target.get("app_asar_sha256", ""))
            or target.get("result") != "passed"
            or not isinstance(checks, list)
            or set(checks) != BINARY_QUALIFICATION_CHECKS
            or len(checks) != len(BINARY_QUALIFICATION_CHECKS)
        ):
            raise ValueError("invalid binary target qualification evidence")
        actual_targets.add(identity)
    if actual_targets != DESKTOP_TARGETS:
        raise ValueError("binary qualification target set is incomplete or duplicated")
    completed_at = evidence.get("completed_at", "")
    if not isinstance(completed_at, str) or not completed_at.endswith("Z"):
        raise ValueError("binary qualification completion must be a UTC timestamp")
    datetime.fromisoformat(completed_at)


def validate_qualification_evidence(evidence: dict, source: dict) -> dict:
    """Validate exact packaged-Desktop qualification for one source wheel."""
    if (
        evidence.get("schema_version"),
        evidence.get("kind"),
    ) != (1, "openminion-source-runtime-qualification"):
        raise ValueError("invalid source qualification evidence identity")
    runtime = evidence.get("runtime")
    desktop = evidence.get("desktop")
    compatibility = evidence.get("compatibility")
    targets = evidence.get("targets")
    if not all(isinstance(item, dict) for item in (runtime, desktop, compatibility)):
        raise ValueError("source qualification evidence is incomplete")
    wheel = source["pypi"]
    if runtime != {
        "version": source["runtime_version"],
        "source_release_id": source["release_id"],
        "wheel_sha256": wheel["sha256"],
        "wheel_size_bytes": wheel["size_bytes"],
    }:
        raise ValueError("qualification evidence does not match the source wheel")
    desktop_version = _stable_three_part_version(
        desktop.get("version", ""), "desktop version"
    )
    if desktop_version == Version("0.0.0"):
        raise ValueError("development Desktop version cannot certify a release")
    if not re.fullmatch(r"[a-f0-9]{40}", desktop.get("source_commit", "")):
        raise ValueError("desktop source commit must be a full SHA")
    minimum = _stable_three_part_version(
        compatibility.get("min_version", ""), "minimum Desktop version"
    )
    maximum = _stable_three_part_version(
        compatibility.get("max_version_exclusive", ""),
        "maximum Desktop version",
    )
    if not minimum <= desktop_version < maximum:
        raise ValueError("qualified Desktop version is outside its claimed bounds")
    if not isinstance(targets, list) or len(targets) != len(DESKTOP_TARGETS):
        raise ValueError("qualification requires every supported Desktop target")
    actual_targets = set()
    for target in targets:
        if not isinstance(target, dict):
            raise ValueError("invalid target qualification evidence")
        identity = f"{target.get('platform')}-{target.get('arch')}"
        actual_targets.add(identity)
        checks = target.get("checks")
        if (
            not re.fullmatch(r"[a-f0-9]{64}", target.get("package_sha256", ""))
            or not re.fullmatch(r"[a-f0-9]{64}", target.get("app_asar_sha256", ""))
            or not re.fullmatch(
                r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}",
                target.get("verification_id", ""),
            )
            or target.get("result") != "passed"
            or not isinstance(checks, list)
            or len(checks) != len(QUALIFICATION_CHECKS)
            or set(checks) != QUALIFICATION_CHECKS
        ):
            raise ValueError("invalid target qualification evidence")
    if actual_targets != DESKTOP_TARGETS:
        raise ValueError("qualification target set is incomplete or duplicated")
    completed_at = evidence.get("completed_at", "")
    if not isinstance(completed_at, str) or not completed_at.endswith("Z"):
        raise ValueError("qualification completion must be a UTC timestamp")
    datetime.fromisoformat(completed_at)
    return {
        "min_version": str(minimum),
        "max_version_exclusive": str(maximum),
    }


def certified_source_record(
    source: dict,
    release_id: str,
    evidence: dict,
    evidence_commit: str,
    evidence_id: str,
    evidence_bytes: bytes,
) -> dict:
    """Create a new immutable compatibility revision for an existing source wheel."""
    if (
        source.get("distribution") != "pypi"
        or source.get("desktop_compatibility") is not None
    ):
        raise ValueError("source certification requires an uncertified PyPI record")
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}", release_id):
        raise ValueError("invalid certification release_id")
    if release_id == source.get("release_id"):
        raise ValueError("certification must use a new immutable release_id")
    if not re.fullmatch(r"[a-f0-9]{40}", evidence_commit):
        raise ValueError("evidence commit must be a full SHA")
    compatibility = validate_qualification_evidence(evidence, source)
    path = evidence_path("qualification", source["runtime_version"], evidence_id)
    return {
        **source,
        "release_id": release_id,
        "published_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "desktop_compatibility": compatibility,
        "certification": {
            "repository": DESKTOP_REPOSITORY,
            "commit": evidence_commit,
            "path": path,
            "sha256": hashlib.sha256(evidence_bytes).hexdigest(),
        },
    }


def source_record(
    version: str, source_commit: str, release_id: str, metadata: dict
) -> dict:
    """Return an uncertified source record; never infer Desktop compatibility."""
    parsed = Version(version)
    if (
        str(parsed) != version
        or parsed.is_prerelease
        or parsed.is_devrelease
        or parsed.local
    ):
        raise ValueError("stable requires a canonical production version")
    if not re.fullmatch(r"[a-f0-9]{40}", source_commit):
        raise ValueError("source_commit must be a full commit SHA")
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}", release_id):
        raise ValueError("invalid release_id")
    if (
        metadata["info"]["name"] != "openminion"
        or metadata["info"]["version"] != version
    ):
        raise ValueError("PyPI identity does not match the release")
    filename = f"openminion-{version}-py3-none-any.whl"
    wheels = [item for item in metadata["urls"] if item["filename"] == filename]
    if len(wheels) != 1:
        raise ValueError("expected one official universal wheel")
    wheel = wheels[0]
    url = urlparse(wheel["url"])
    if (
        url.scheme != "https"
        or url.netloc != "files.pythonhosted.org"
        or url.query
        or url.fragment
        or wheel.get("yanked", False)
        or wheel["packagetype"] != "bdist_wheel"
        or not re.fullmatch(r"[a-f0-9]{64}", wheel["digests"]["sha256"])
        or type(wheel["size"]) is not int
        or wheel["size"] <= 0
        or not wheel.get("requires_python")
    ):
        raise ValueError("invalid official wheel metadata")
    return {
        "schema_version": 1,
        "product": "openminion",
        "distribution": "pypi",
        "channel": "stable",
        "promotion_status": "promoted",
        "runtime_version": version,
        "release_id": release_id,
        "published_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "source_commit": source_commit,
        "release_notes_url": f"https://github.com/openminion/openminion/releases/tag/v{version}",
        "desktop_compatibility": None,
        "pypi": {
            "package": "openminion",
            "version": version,
            "requires_python": wheel["requires_python"],
            "url": wheel["url"],
            "filename": filename,
            "sha256": wheel["digests"]["sha256"],
            "size_bytes": wheel["size"],
        },
    }


def official_record(version: str, source_commit: str, release_id: str) -> dict:
    """Read production metadata and verify the wheel before publication."""
    Version(version)
    if not re.fullmatch(r"[0-9A-Za-z.+!-]+", version):
        raise ValueError("invalid version")
    with urlopen(
        f"https://pypi.org/pypi/openminion/{version}/json", timeout=15
    ) as response:
        body = response.read(1024 * 1024 + 1)
    if len(body) > 1024 * 1024:
        raise ValueError("PyPI response exceeds metadata limit")
    record = source_record(version, source_commit, release_id, json.loads(body))
    wheel = record["pypi"]
    if wheel["size_bytes"] > 1024**3:
        raise ValueError("wheel exceeds artifact limit")
    digest = hashlib.sha256()
    size = 0
    deadline = monotonic() + 15 * 60
    with (
        SpooledTemporaryFile(max_size=1024 * 1024) as archive_bytes,
        urlopen(wheel["url"], timeout=15) as response,
    ):
        if urlparse(response.url).netloc != "files.pythonhosted.org":
            raise ValueError("unexpected wheel origin")
        while chunk := response.read(1024 * 1024):
            size += len(chunk)
            if size > wheel["size_bytes"] or monotonic() > deadline:
                raise ValueError("wheel exceeds declared size")
            digest.update(chunk)
            archive_bytes.write(chunk)
        if size != wheel["size_bytes"] or digest.hexdigest() != wheel["sha256"]:
            raise ValueError("official wheel integrity mismatch")
        with ZipFile(archive_bytes) as archive:
            path = f"openminion-{version}.dist-info/METADATA"
            if (
                archive.namelist().count(path) != 1
                or archive.getinfo(path).file_size > 1024 * 1024
            ):
                raise ValueError("invalid wheel metadata")
            metadata = BytesParser().parsebytes(archive.read(path))
        if (
            metadata["Name"] != "openminion"
            or metadata["Version"] != version
            or metadata["Requires-Python"] != wheel["requires_python"]
        ):
            raise ValueError("wheel package metadata differs from PyPI identity")
    return record


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--version", required=True)
    parser.add_argument("--source-commit", required=True)
    parser.add_argument("--release-id", required=True)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    record = official_record(args.version, args.source_commit, args.release_id)
    args.output.write_text(json.dumps(record, indent=2, sort_keys=True) + "\n")


if __name__ == "__main__":
    main()
