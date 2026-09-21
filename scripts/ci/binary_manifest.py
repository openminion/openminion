"""Verify an immutable runtime Release and create its promoted binary record."""

from __future__ import annotations

import hashlib
import json
import re
from time import monotonic
from urllib.parse import quote, urlparse
from urllib.request import Request, urlopen

from packaging.version import Version


RUNTIME_REPOSITORY = "openminion/runtime"
GITHUB_API = f"https://api.github.com/repos/{RUNTIME_REPOSITORY}"
MAX_METADATA_SIZE = 1024 * 1024
MAX_ARTIFACT_SIZE = 1024**3


def read_url(url: str, limit: int) -> bytes:
    request = Request(url, headers={"Accept": "application/vnd.github+json"})
    deadline = monotonic() + 15 * 60
    body = bytearray()
    with urlopen(request, timeout=15) as response:
        final = urlparse(response.url)
        if final.scheme != "https" or final.hostname not in {
            "api.github.com",
            "github.com",
            "release-assets.githubusercontent.com",
        }:
            raise ValueError("unexpected runtime release origin")
        while chunk := response.read(1024 * 1024):
            body.extend(chunk)
            if len(body) > limit or monotonic() > deadline:
                raise ValueError("runtime release response exceeds its limit")
    return bytes(body)


def release_metadata(tag: str) -> dict:
    if not re.fullmatch(r"runtime-v[0-9A-Za-z.+!-]+-[A-Za-z0-9._-]+", tag):
        raise ValueError("invalid runtime release tag")
    return json.loads(
        read_url(f"{GITHUB_API}/releases/tags/{quote(tag)}", MAX_METADATA_SIZE)
    )


def asset_bytes(asset: dict, limit: int) -> bytes:
    if (
        type(asset.get("size")) is not int
        or asset["size"] <= 0
        or asset["size"] > limit
    ):
        raise ValueError("invalid runtime release asset size")
    body = read_url(asset["browser_download_url"], limit)
    if len(body) != asset["size"]:
        raise ValueError("runtime release asset size differs")
    return body


def _stable_version(value: object) -> str:
    if not isinstance(value, str):
        raise ValueError("invalid binary runtime version")
    parsed = Version(value)
    if (
        str(parsed) != value
        or parsed.is_prerelease
        or parsed.is_devrelease
        or parsed.local
    ):
        raise ValueError("binary runtime requires a canonical production version")
    return value


def _artifact_identity(value: object) -> dict:
    if not isinstance(value, dict):
        raise ValueError("invalid binary executable identity")
    if (
        not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", str(value.get("filename", "")))
        or not re.fullmatch(r"[a-f0-9]{64}", str(value.get("sha256", "")))
        or type(value.get("size_bytes")) is not int
        or value["size_bytes"] <= 0
        or value["size_bytes"] > MAX_ARTIFACT_SIZE
    ):
        raise ValueError("invalid binary executable identity")
    return {
        "filename": value["filename"],
        "sha256": value["sha256"],
        "size_bytes": value["size_bytes"],
    }


def promoted_record(
    candidate: dict,
    release: dict,
    verification: dict,
    *,
    release_id: str,
) -> dict:
    version = _stable_version(candidate.get("runtime_version"))
    if (
        candidate.get("schema_version") != 1
        or candidate.get("product") != "openminion"
        or candidate.get("distribution") != "binary"
        or candidate.get("channel") != "stable"
        or candidate.get("promotion_status") != "candidate"
        or not re.fullmatch(r"[a-f0-9]{40}", str(candidate.get("source_commit", "")))
        or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}", release_id)
    ):
        raise ValueError("invalid binary candidate identity")
    bounds = candidate.get("desktop_compatibility")
    if not isinstance(bounds, dict):
        raise ValueError("promoted binary requires Desktop compatibility")
    minimum = bounds.get("min_version")
    maximum = bounds.get("max_version_exclusive")
    if (
        not isinstance(minimum, str)
        or not isinstance(maximum, str)
        or not re.fullmatch(r"\d+\.\d+\.\d+", minimum)
        or not re.fullmatch(r"\d+\.\d+\.\d+", maximum)
        or Version(minimum) >= Version(maximum)
    ):
        raise ValueError("invalid Desktop compatibility bounds")
    expected_tag = f"runtime-v{version}-{release_id}"
    if (
        release.get("tag_name") != expected_tag
        or release.get("draft") is not False
        or release.get("prerelease") is not False
        or release.get("immutable") is not True
        or not release.get("published_at")
    ):
        raise ValueError("runtime Release is not immutable and final")
    assets = release.get("assets")
    if not isinstance(assets, list) or any(
        not isinstance(item, dict) for item in assets
    ):
        raise ValueError("invalid runtime Release assets")
    by_name = {item.get("name"): item for item in assets}
    if len(by_name) != len(assets):
        raise ValueError("duplicate runtime Release asset")
    checks = verification.get("targets") if isinstance(verification, dict) else None
    if verification.get("schema_version") != 1 or not isinstance(checks, dict):
        raise ValueError("invalid native verification evidence")

    promoted = []
    targets = set()
    for value in candidate.get("artifacts", []):
        if not isinstance(value, dict):
            raise ValueError("invalid binary target")
        target = f"{value.get('platform')}-{value.get('arch')}"
        if (
            target not in {"darwin-arm64", "linux-x64", "win32-x64"}
            or target in targets
            or value.get("format") != "executables"
        ):
            raise ValueError("invalid binary target")
        evidence = checks.get(target)
        if not isinstance(evidence, dict) or not re.fullmatch(
            r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}",
            str(evidence.get("verification_id", "")),
        ):
            raise ValueError("binary target lacks native verification evidence")
        target_record = {
            "platform": value["platform"],
            "arch": value["arch"],
            "format": "executables",
            "min_os_version": str(value.get("min_os_version", "")),
            "verification_id": evidence["verification_id"],
        }
        if not target_record["min_os_version"]:
            raise ValueError("binary target lacks an OS baseline")
        if value["platform"] == "linux":
            if value.get("libc_family") != "glibc" or not value.get("min_libc_version"):
                raise ValueError("Linux target lacks its libc baseline")
            target_record.update(
                libc_family="glibc", min_libc_version=value["min_libc_version"]
            )
        for role in ("cli", "daemon"):
            identity = _artifact_identity(value.get(role))
            asset = by_name.get(identity["filename"])
            if asset is None:
                raise ValueError("runtime Release is missing an executable")
            expected_url = (
                f"https://github.com/{RUNTIME_REPOSITORY}/releases/download/"
                f"{expected_tag}/{identity['filename']}"
            )
            if (
                asset.get("size") != identity["size_bytes"]
                or asset.get("digest") != ("sha256:" + identity["sha256"])
                or asset.get("browser_download_url") != expected_url
            ):
                raise ValueError("runtime Release identity differs from candidate")
            target_record[role] = {
                **identity,
                "url": asset["browser_download_url"],
            }
        targets.add(target)
        promoted.append(target_record)
    if not promoted:
        raise ValueError("binary candidate has no targets")
    promoted.sort(key=lambda item: (item["platform"], item["arch"]))
    return {
        "schema_version": 1,
        "product": "openminion",
        "distribution": "binary",
        "channel": "stable",
        "promotion_status": "promoted",
        "runtime_version": version,
        "release_id": release_id,
        "published_at": release["published_at"],
        "source_commit": candidate["source_commit"],
        "release_notes_url": candidate["release_notes_url"],
        "desktop_compatibility": bounds,
        "artifacts": promoted,
    }


def official_binary_record(tag: str, release_id: str) -> dict:
    release = release_metadata(tag)
    assets = {asset["name"]: asset for asset in release.get("assets", [])}
    try:
        candidate = json.loads(asset_bytes(assets["candidate.json"], MAX_METADATA_SIZE))
        verification = json.loads(
            asset_bytes(assets["verification.json"], MAX_METADATA_SIZE)
        )
    except KeyError as exc:
        raise ValueError("runtime Release lacks publication evidence") from exc
    record = promoted_record(candidate, release, verification, release_id=release_id)
    for target in record["artifacts"]:
        for role in ("cli", "daemon"):
            identity = target[role]
            body = asset_bytes(assets[identity["filename"]], MAX_ARTIFACT_SIZE)
            if hashlib.sha256(body).hexdigest() != identity["sha256"]:
                raise ValueError("runtime Release bytes differ from candidate")
    return record
