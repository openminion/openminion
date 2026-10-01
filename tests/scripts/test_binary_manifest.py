"""Binary runtime promotion accepts only immutable, native-verified releases."""

from copy import deepcopy

import pytest

from scripts.ci.binary_manifest import promoted_record
from scripts.ci.release_manifest import BINARY_QUALIFICATION_CHECKS


VERSION = "1.2.3"
RELEASE_ID = "build.1"
TAG = f"runtime-v{VERSION}-{RELEASE_ID}"


def executable(name: str, digest: str) -> dict:
    return {"filename": name, "sha256": digest * 64, "size_bytes": 10}


def candidate() -> dict:
    def target(platform, arch, suffix, cli_digest, daemon_digest):
        item = {
            "platform": platform,
            "arch": arch,
            "format": "executables",
            "min_os_version": "14.0",
            "cli": executable(f"openminion-{suffix}", cli_digest),
            "daemon": executable(f"openminiond-{suffix}", daemon_digest),
        }
        if platform == "linux":
            item.update(libc_family="glibc", min_libc_version="2.35")
        return item

    return {
        "schema_version": 1,
        "product": "openminion",
        "distribution": "binary",
        "channel": "stable",
        "promotion_status": "candidate",
        "runtime_version": VERSION,
        "release_id": "candidate.1",
        "published_at": "2026-09-20T00:00:00Z",
        "source_commit": "a" * 40,
        "release_notes_url": f"https://github.com/openminion/openminion/releases/tag/v{VERSION}",
        "desktop_compatibility": {
            "min_version": "0.0.0",
            "max_version_exclusive": "1.0.0",
        },
        "artifacts": [
            target("darwin", "arm64", "macos-arm64", "a", "b"),
            target("linux", "x64", "linux-x64", "c", "d"),
            target("win32", "x64", "windows-x64.exe", "e", "f"),
        ],
    }


def release(item: dict) -> dict:
    assets = []
    for target in item["artifacts"]:
        for identity in (target["cli"], target["daemon"]):
            assets.append(
                {
                    "name": identity["filename"],
                    "size": identity["size_bytes"],
                    "digest": "sha256:" + identity["sha256"],
                    "browser_download_url": (
                        f"https://github.com/openminion/runtime/releases/download/{TAG}/"
                        f"{identity['filename']}"
                    ),
                }
            )
    return {
        "tag_name": TAG,
        "draft": False,
        "prerelease": False,
        "immutable": True,
        "published_at": "2026-09-20T01:00:00Z",
        "assets": assets,
    }


def verification() -> dict:
    return {
        "schema_version": 1,
        "targets": {
            "darwin-arm64": {"verification_id": "native.macos.1"},
            "linux-x64": {"verification_id": "native.linux.1"},
            "win32-x64": {"verification_id": "native.windows.1"},
        },
    }


def desktop_evidence(item: dict) -> dict:
    native = verification()["targets"]
    return {
        "schema_version": 1,
        "kind": "openminion-binary-runtime-qualification",
        "runtime": {"version": VERSION, "release_tag": TAG},
        "desktop": {"version": "0.1.0", "source_commit": "b" * 40},
        "compatibility": item["desktop_compatibility"],
        "targets": [
            {
                "platform": target["platform"],
                "arch": target["arch"],
                "cli_sha256": target["cli"]["sha256"],
                "daemon_sha256": target["daemon"]["sha256"],
                "package_sha256": "c" * 64,
                "app_asar_sha256": "d" * 64,
                "verification_id": native[f"{target['platform']}-{target['arch']}"][
                    "verification_id"
                ],
                "result": "passed",
                "checks": sorted(BINARY_QUALIFICATION_CHECKS),
            }
            for target in item["artifacts"]
        ],
        "completed_at": "2026-10-01T00:00:00Z",
    }


def promotion_arguments(item: dict) -> dict:
    evidence = desktop_evidence(item)
    return {
        "release_id": RELEASE_ID,
        "desktop_evidence": evidence,
        "evidence_commit": "b" * 40,
        "evidence_id": "desktop-0.1.0",
        "evidence_bytes": b"desktop evidence",
    }


def test_promotes_paired_executables_with_native_evidence() -> None:
    item = candidate()
    record = promoted_record(item, release(item), verification(), **promotion_arguments(item))
    assert record["promotion_status"] == "promoted"
    assert record["release_id"] == RELEASE_ID
    assert record["artifacts"][0]["format"] == "executables"
    assert record["artifacts"][0]["verification_id"] == "native.macos.1"
    assert record["certification"]["path"].endswith("/desktop-0.1.0.json")


@pytest.mark.parametrize(
    ("surface", "value", "message"),
    [
        ("candidate", ("promotion_status", "promoted"), "candidate identity"),
        ("candidate", ("desktop_compatibility", None), "compatibility"),
        ("release", ("immutable", False), "immutable"),
        ("release", ("draft", True), "immutable"),
        ("verification", ("targets", {}), "verification"),
    ],
)
def test_rejects_unqualified_promotion(surface, value, message) -> None:
    item = candidate()
    published = release(item)
    evidence = verification()
    target = {"candidate": item, "release": published, "verification": evidence}[
        surface
    ]
    target[value[0]] = value[1]
    with pytest.raises(ValueError, match=message):
        promoted_record(item, published, evidence, **promotion_arguments(item))


def test_rejects_changed_or_mislocated_release_asset() -> None:
    item = candidate()
    for change in (
        {"digest": "sha256:" + "c" * 64},
        {"size": 11},
        {"browser_download_url": "https://example.invalid/openminion"},
    ):
        published = release(item)
        published["assets"][0].update(change)
        with pytest.raises(ValueError, match="differs"):
            promoted_record(
                item, published, verification(), **promotion_arguments(item)
            )


def test_rejects_duplicate_target() -> None:
    item = candidate()
    item["artifacts"].append(deepcopy(item["artifacts"][0]))
    with pytest.raises(ValueError, match="target"):
        promoted_record(item, release(item), verification(), **promotion_arguments(item))


def test_rejects_desktop_evidence_for_different_runtime_bytes() -> None:
    item = candidate()
    arguments = promotion_arguments(item)
    arguments["desktop_evidence"]["targets"][0]["cli_sha256"] = "f" * 64
    with pytest.raises(ValueError, match="qualification"):
        promoted_record(item, release(item), verification(), **arguments)
