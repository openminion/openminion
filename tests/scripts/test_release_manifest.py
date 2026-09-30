"""Production source metadata does not assert Desktop certification."""

import hashlib
import io
import json
from unittest.mock import patch
from zipfile import ZipFile

import pytest

from scripts.ci.release_manifest import (
    QUALIFICATION_CHECKS,
    certified_source_record,
    official_record,
    source_record,
)


def metadata():
    return {
        "info": {"name": "openminion", "version": "1.0.0"},
        "urls": [
            {
                "filename": "openminion-1.0.0-py3-none-any.whl",
                "url": "https://files.pythonhosted.org/packages/openminion-1.0.0-py3-none-any.whl",
                "packagetype": "bdist_wheel",
                "size": 123,
                "digests": {"sha256": "a" * 64},
                "requires_python": ">=3.11",
            }
        ],
    }


def qualification(source):
    return {
        "schema_version": 1,
        "kind": "openminion-source-runtime-qualification",
        "runtime": {
            "version": source["runtime_version"],
            "source_release_id": source["release_id"],
            "wheel_sha256": source["pypi"]["sha256"],
            "wheel_size_bytes": source["pypi"]["size_bytes"],
        },
        "desktop": {"version": "1.2.3", "source_commit": "c" * 40},
        "compatibility": {
            "min_version": "1.2.0",
            "max_version_exclusive": "1.3.0",
        },
        "targets": [
            {
                "platform": platform,
                "arch": arch,
                "package_sha256": "d" * 64,
                "app_asar_sha256": "e" * 64,
                "verification_id": f"{platform}-{arch}-1",
                "result": "passed",
                "checks": sorted(QUALIFICATION_CHECKS),
            }
            for platform, arch in (
                ("darwin", "arm64"),
                ("linux", "x64"),
                ("win32", "x64"),
            )
        ],
        "completed_at": "2026-09-30T12:00:00Z",
    }


def test_source_record_keeps_uncertified_bounds_and_exact_wheel():
    result = source_record("1.0.0", "b" * 40, "source.1", metadata())
    assert result["desktop_compatibility"] is None
    assert result["promotion_status"] == "promoted"
    assert result["pypi"]["sha256"] == "a" * 64
    assert "artifacts" not in result


def test_certification_creates_a_new_same_wheel_revision_with_evidence_pointer():
    source = source_record("1.0.0", "b" * 40, "source.1", metadata())
    evidence = qualification(source)
    evidence_bytes = json.dumps(evidence).encode()
    result = certified_source_record(
        source,
        "source-certification.1",
        evidence,
        "f" * 40,
        "desktop-1.2.3",
        evidence_bytes,
    )
    assert result["pypi"] == source["pypi"]
    assert result["desktop_compatibility"] == {
        "min_version": "1.2.0",
        "max_version_exclusive": "1.3.0",
    }
    assert result["certification"]["commit"] == "f" * 40
    assert result["certification"]["path"].endswith(
        "/qualification/1.0.0/desktop-1.2.3.json"
    )
    assert source["desktop_compatibility"] is None


@pytest.mark.parametrize(
    "mutation",
    [
        "development-version",
        "wheel",
        "target",
        "checks",
        "duplicate-checks",
        "bounds",
        "release-id",
    ],
)
def test_certification_rejects_unqualified_or_mutated_evidence(mutation):
    source = source_record("1.0.0", "b" * 40, "source.1", metadata())
    evidence = qualification(source)
    release_id = "source-certification.1"
    if mutation == "development-version":
        evidence["desktop"]["version"] = "0.0.0"
        evidence["compatibility"]["min_version"] = "0.0.0"
    elif mutation == "wheel":
        evidence["runtime"]["wheel_sha256"] = "0" * 64
    elif mutation == "target":
        evidence["targets"].pop()
    elif mutation == "checks":
        evidence["targets"][0]["checks"].pop()
    elif mutation == "duplicate-checks":
        evidence["targets"][0]["checks"].append(evidence["targets"][0]["checks"][0])
    elif mutation == "bounds":
        evidence["compatibility"]["min_version"] = "2.0.0"
    else:
        release_id = source["release_id"]
    with pytest.raises(ValueError):
        certified_source_record(
            source,
            release_id,
            evidence,
            "f" * 40,
            "desktop-1.2.3",
            json.dumps(evidence).encode(),
        )


@pytest.mark.parametrize("version", ["1.0.0rc1", "1.0.0.dev1", "1.0.0+local", "01.0.0"])
def test_source_record_rejects_non_production_versions(version):
    with pytest.raises(ValueError):
        source_record(version, "b" * 40, "source.1", metadata())


@pytest.mark.parametrize(
    "field,value",
    [
        ("url", "https://example.com/runtime.whl"),
        ("url", "https://files.pythonhosted.org@evil.example/runtime.whl"),
        ("size", True),
        ("size", 0),
        ("yanked", True),
        ("requires_python", None),
    ],
)
def test_source_record_rejects_invalid_wheel(field, value):
    data = metadata()
    data["urls"][0][field] = value
    with pytest.raises(ValueError):
        source_record("1.0.0", "b" * 40, "source.1", data)


def test_source_record_rejects_ambiguous_wheel_and_path_traversal():
    data = metadata()
    data["urls"] *= 2
    with pytest.raises(ValueError):
        source_record("1.0.0", "b" * 40, "source.1", data)
    with pytest.raises(ValueError):
        source_record("1.0.0", "b" * 40, "../feed", metadata())


@pytest.mark.parametrize("wheel_version", ["1.0.0", "2.0.0"])
def test_official_record_verifies_downloaded_wheel_package_metadata(wheel_version):
    archive_bytes = io.BytesIO()
    with ZipFile(archive_bytes, "w") as archive:
        archive.writestr(
            "openminion-1.0.0.dist-info/METADATA",
            f"Name: openminion\nVersion: {wheel_version}\nRequires-Python: >=3.11\n",
        )
    content = archive_bytes.getvalue()
    data = metadata()
    data["urls"][0].update(
        size=len(content), digests={"sha256": hashlib.sha256(content).hexdigest()}
    )
    wheel_response = io.BytesIO(content)
    wheel_response.url = data["urls"][0]["url"]
    with patch(
        "scripts.ci.release_manifest.urlopen",
        side_effect=[io.BytesIO(json.dumps(data).encode()), wheel_response],
    ):
        if wheel_version == "1.0.0":
            assert official_record("1.0.0", "b" * 40, "source.1")["pypi"][
                "size_bytes"
            ] == len(content)
        else:
            with pytest.raises(ValueError, match="package metadata"):
                official_record("1.0.0", "b" * 40, "source.1")
