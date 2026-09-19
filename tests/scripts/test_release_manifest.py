"""Production source metadata does not assert Desktop certification."""

import hashlib
import io
import json
from unittest.mock import patch
from zipfile import ZipFile

import pytest

from scripts.ci.release_manifest import official_record, source_record


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


def test_source_record_keeps_uncertified_bounds_and_exact_wheel():
    result = source_record("1.0.0", "b" * 40, "source.1", metadata())
    assert result["desktop_compatibility"] is None
    assert result["promotion_status"] == "promoted"
    assert result["pypi"]["sha256"] == "a" * 64
    assert "artifacts" not in result


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
