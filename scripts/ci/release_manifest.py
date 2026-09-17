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
