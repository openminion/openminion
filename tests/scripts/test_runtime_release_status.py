"""Release status reports only milestones proven by published metadata."""

import hashlib
import json

import pytest

from scripts.ci.publish_runtime_manifest import BASE, encoded
from scripts.ci.runtime_release_status import release_status


def write_release(repository, distribution, record):
    record = {
        "schema_version": 1,
        "product": "openminion",
        "distribution": distribution,
        "channel": "stable",
        "promotion_status": "promoted",
        **record,
    }
    if distribution == "pypi":
        record.setdefault(
            "pypi",
            {
                "package": "openminion",
                "version": record["runtime_version"],
            },
        )
    else:
        record.setdefault("artifacts", [{"platform": "darwin", "arch": "arm64"}])
    path = (
        repository
        / BASE
        / distribution
        / "releases"
        / record["runtime_version"]
        / f"{record['release_id']}.json"
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    content = encoded(record)
    path.write_bytes(content)
    feed = repository / BASE / distribution / "channels/stable.json"
    feed.parent.mkdir(parents=True, exist_ok=True)
    feed.write_bytes(
        encoded(
            {
                "schema_version": 1,
                "product": "openminion",
                "distribution": distribution,
                "channel": "stable",
                "releases": [
                    {
                        "runtime_version": record["runtime_version"],
                        "manifest_url": (
                            "https://raw.githubusercontent.com/openminion/openminion/"
                            + "a" * 40
                            + "/"
                            + str(path.relative_to(repository))
                        ),
                        "manifest_sha256": hashlib.sha256(content).hexdigest(),
                    }
                ],
            }
        )
    )


def write_empty_feed(repository, distribution):
    feed = repository / BASE / distribution / "channels/stable.json"
    feed.parent.mkdir(parents=True, exist_ok=True)
    feed.write_bytes(
        encoded(
            {
                "schema_version": 1,
                "product": "openminion",
                "distribution": distribution,
                "channel": "stable",
                "releases": [],
            }
        )
    )


def test_source_only_release_is_not_reported_as_a_desktop_or_full_e2e_release(tmp_path):
    write_release(
        tmp_path,
        "pypi",
        {
            "runtime_version": "1.0.0",
            "release_id": "source.1",
            "desktop_compatibility": None,
        },
    )
    write_empty_feed(tmp_path, "binary")
    status = release_status(tmp_path, "1.0.0")
    assert status["production_package_recorded"] is True
    assert status["source_manifest_published"] is True
    assert status["desktop_source_update_certified"] is False
    assert status["binary_runtime_published"] is False
    assert status["full_public_e2e"] is False


def test_certified_source_and_binary_are_distinct_from_full_public_e2e(tmp_path):
    write_release(
        tmp_path,
        "pypi",
        {
            "runtime_version": "1.0.0",
            "release_id": "cert.1",
            "desktop_compatibility": {
                "min_version": "1.0.0",
                "max_version_exclusive": "2.0.0",
            },
            "certification": {
                "repository": "openminion/desktop",
                "commit": "a" * 40,
                "path": (
                    "releases/runtime-certification/v1/qualification/"
                    "1.0.0/desktop-test.json"
                ),
                "sha256": "b" * 64,
            },
        },
    )
    write_release(
        tmp_path,
        "binary",
        {
            "runtime_version": "1.0.0",
            "release_id": "binary.1",
            "desktop_compatibility": {
                "min_version": "1.0.0",
                "max_version_exclusive": "2.0.0",
            },
        },
    )
    status = release_status(tmp_path, "1.0.0")
    assert status["desktop_source_update_certified"] is True
    assert status["binary_runtime_published"] is True
    assert status["full_public_e2e"] is False


def test_malformed_certification_is_not_reported_as_complete(tmp_path):
    write_release(
        tmp_path,
        "pypi",
        {
            "runtime_version": "1.0.0",
            "release_id": "cert.1",
            "desktop_compatibility": {
                "min_version": "1.0.0",
                "max_version_exclusive": "2.0.0",
            },
            "certification": {
                "repository": "openminion/desktop",
                "commit": "a" * 40,
                "path": (
                    "releases/runtime-certification/v1/qualification/"
                    "0.9.0/desktop-test.json"
                ),
                "sha256": "b" * 64,
            },
        },
    )
    write_empty_feed(tmp_path, "binary")

    assert release_status(tmp_path, "1.0.0")["desktop_source_update_certified"] is False


def test_record_path_must_match_the_feed_distribution(tmp_path):
    write_release(
        tmp_path,
        "pypi",
        {
            "runtime_version": "1.0.0",
            "release_id": "source.1",
            "desktop_compatibility": None,
        },
    )
    feed_path = tmp_path / BASE / "pypi/channels/stable.json"
    feed = json.loads(feed_path.read_text())
    feed["releases"][0]["manifest_url"] = feed["releases"][0]["manifest_url"].replace(
        "/pypi/", "/binary/"
    )
    feed_path.write_bytes(encoded(feed))
    write_empty_feed(tmp_path, "binary")

    with pytest.raises(ValueError, match="feed identity"):
        release_status(tmp_path, "1.0.0")


def test_record_identity_must_match_the_feed_reference(tmp_path):
    write_release(
        tmp_path,
        "pypi",
        {
            "runtime_version": "1.0.0",
            "release_id": "source.1",
            "desktop_compatibility": None,
            "product": "not-openminion",
        },
    )
    write_empty_feed(tmp_path, "binary")

    with pytest.raises(ValueError, match="record identity"):
        release_status(tmp_path, "1.0.0")
