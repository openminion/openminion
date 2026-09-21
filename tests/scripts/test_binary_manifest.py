"""Binary runtime promotion accepts only immutable, native-verified releases."""

from copy import deepcopy

import pytest

from scripts.ci.binary_manifest import promoted_record


VERSION = "1.2.3"
RELEASE_ID = "build.1"
TAG = f"runtime-v{VERSION}-{RELEASE_ID}"


def executable(name: str, digest: str) -> dict:
    return {"filename": name, "sha256": digest * 64, "size_bytes": 10}


def candidate() -> dict:
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
            {
                "platform": "darwin",
                "arch": "arm64",
                "format": "executables",
                "min_os_version": "14.0",
                "cli": executable("openminion", "a"),
                "daemon": executable("openminiond", "b"),
            }
        ],
    }


def release(item: dict) -> dict:
    assets = []
    for identity in (item["artifacts"][0]["cli"], item["artifacts"][0]["daemon"]):
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
        "targets": {"darwin-arm64": {"verification_id": "native.macos.1"}},
    }


def test_promotes_paired_executables_with_native_evidence() -> None:
    item = candidate()
    record = promoted_record(item, release(item), verification(), release_id=RELEASE_ID)
    assert record["promotion_status"] == "promoted"
    assert record["release_id"] == RELEASE_ID
    assert record["artifacts"][0]["format"] == "executables"
    assert record["artifacts"][0]["verification_id"] == "native.macos.1"
    assert record["artifacts"][0]["daemon"]["filename"] == "openminiond"


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
        promoted_record(item, published, evidence, release_id=RELEASE_ID)


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
            promoted_record(item, published, verification(), release_id=RELEASE_ID)


def test_rejects_duplicate_target() -> None:
    item = candidate()
    item["artifacts"].append(deepcopy(item["artifacts"][0]))
    with pytest.raises(ValueError, match="target"):
        promoted_record(item, release(item), verification(), release_id=RELEASE_ID)
