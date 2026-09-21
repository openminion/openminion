"""Publish production source metadata with record-before-feed fast-forward commits."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
from datetime import datetime
from pathlib import Path
from urllib.request import Request, urlopen

from packaging.version import Version

from scripts.ci.binary_manifest import official_binary_record
from scripts.ci.release_manifest import official_record


REPOSITORY = "openminion/openminion"
BRANCH = "main"
BASE = "releases/runtime/v1"
RAW = f"https://raw.githubusercontent.com/{REPOSITORY}"


def verify_producer(
    environment: dict,
    run: dict,
    jobs: list[dict],
    tag_commit: str,
    expected_commit: str,
    tag: str,
    expected_attempt: int,
) -> None:
    """Admit only protected, successful production-tag builds from this repository."""
    if (
        not any(
            rule["type"] == "required_reviewers" and rule.get("reviewers")
            for rule in environment["protection_rules"]
        )
        or environment["deployment_branch_policy"] is None
    ):
        raise ValueError(
            "configure protected publication reviewers and deployment refs first"
        )
    if (
        run["path"],
        run["event"],
        run["conclusion"],
        run["head_repository"]["full_name"],
        run["head_sha"],
        run["head_branch"],
        run["run_attempt"],
    ) != (
        ".github/workflows/release.yml",
        "push",
        "success",
        REPOSITORY,
        expected_commit,
        tag,
        expected_attempt,
    ):
        raise ValueError("untrusted or failed source producer")
    version = Version(tag.removeprefix("v"))
    if (
        not tag.startswith("v")
        or version.is_prerelease
        or version.is_devrelease
        or version.local
        or tag_commit != expected_commit
    ):
        raise ValueError("producer tag is not an unchanged production release")
    if not any(
        job["name"] == "Publish to PyPI (final release tags, gated)"
        and job["conclusion"] == "success"
        for job in jobs
    ):
        raise ValueError("no successful production PyPI job")


def encoded(value: dict) -> bytes:
    return (json.dumps(value, indent=2, sort_keys=True) + "\n").encode()


def record_path(record: dict) -> str:
    return f"{BASE}/{record['distribution']}/releases/{record['runtime_version']}/{record['release_id']}.json"


def add_reference(feed: dict, record: dict, commit: str, digest: str) -> dict:
    """Prefer this immutable revision without dropping older supported records."""
    source = record["distribution"]
    if (
        feed["schema_version"],
        feed["product"],
        feed["distribution"],
        feed["channel"],
    ) != (1, "openminion", source, "stable"):
        raise ValueError("existing feed identity mismatch")
    reference = {
        "runtime_version": record["runtime_version"],
        "manifest_url": f"{RAW}/{commit}/{record_path(record)}",
        "manifest_sha256": digest,
    }
    references = [reference, *[item for item in feed["releases"] if item != reference]]
    if len(references) > 100:
        raise ValueError("feed retention needs maintainer review; refusing truncation")
    references.sort(key=lambda item: Version(item["runtime_version"]), reverse=True)
    return {**feed, "published_at": record["published_at"], "releases": references}


def write_immutable(path: Path, content: bytes) -> None:
    if path.exists():
        if path.read_bytes() != content:
            raise ValueError("immutable release identity already has different bytes")
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)


def git(root: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(root), *args],
        check=True,
        capture_output=True,
        text=True,
        timeout=60,
    ).stdout.strip()


def read_public(url: str, expected: bytes) -> None:
    request = Request(url, headers={"Cache-Control": "no-cache"})
    with urlopen(request, timeout=15) as response:
        if response.url != url:
            raise ValueError("unexpected public metadata redirect")
        content = response.read(1024 * 1024 + 1)
    if content != expected:
        raise ValueError("public readback differs; publication is unconfirmed")


def github(*args: str) -> str:
    return subprocess.run(
        ["gh", *args, "--repo", REPOSITORY],
        check=True,
        capture_output=True,
        text=True,
        timeout=60,
    ).stdout.strip()


def verify_published(workspace: Path, feed_ref: str = BRANCH) -> None:
    """Verify public main feeds and reachable commit-pinned records after merge."""
    for source in ("pypi", "binary"):
        feed_path = f"{BASE}/{source}/channels/stable.json"
        feed_bytes = (workspace / feed_path).read_bytes()
        read_public(f"{RAW}/{feed_ref}/{feed_path}", feed_bytes)
        for reference in json.loads(feed_bytes)["releases"]:
            match = re.fullmatch(
                re.escape(RAW)
                + rf"/([a-f0-9]{{40}})/({BASE}/{source}/releases/[A-Za-z0-9._-]+/[A-Za-z0-9._-]+\.json)",
                reference["manifest_url"],
            )
            if match is None:
                raise ValueError(
                    "record URL must pin the agreed repository and full SHA"
                )
            commit, path = match.groups()
            content = (workspace / path).read_bytes()
            if hashlib.sha256(content).hexdigest() != reference["manifest_sha256"]:
                raise ValueError("record digest differs from feed")
            git(workspace, "merge-base", "--is-ancestor", commit, "HEAD")
            read_public(reference["manifest_url"], content)


def publish(record: dict, workspace: Path) -> dict:
    """Prepare a metadata-only PR; never bypass main protection or auto-merge."""
    workspace.mkdir(parents=True, exist_ok=False)
    git(workspace, "init")
    git(workspace, "remote", "add", "origin", f"https://github.com/{REPOSITORY}.git")
    git(workspace, "config", "user.name", "OpenMinion runtime publisher")
    git(
        workspace,
        "config",
        "user.email",
        "41898282+github-actions[bot]@users.noreply.github.com",
    )
    git(workspace, "fetch", "origin", f"{BRANCH}:refs/remotes/origin/{BRANCH}")
    git(workspace, "checkout", "-b", BRANCH, f"origin/{BRANCH}")
    path = record_path(record)
    record_bytes = encoded(record)
    feed_path = f"{BASE}/{record['distribution']}/channels/stable.json"
    if (workspace / path).exists():
        write_immutable(workspace / path, record_bytes)
        references = json.loads((workspace / feed_path).read_bytes())["releases"]
        if not any(
            item["manifest_url"].endswith("/" + path)
            and item["manifest_sha256"] == hashlib.sha256(record_bytes).hexdigest()
            for item in references
        ):
            raise ValueError("existing record is not published in main's feed")
        verify_published(workspace)
        return {
            "status": "published",
            "feed_commit": git(workspace, "rev-parse", "HEAD"),
            "record_path": path,
        }
    branch = f"runtime-publication/{record['release_id']}"
    requests = json.loads(
        github(
            "pr",
            "list",
            "--base",
            BRANCH,
            "--state",
            "open",
            "--limit",
            "1000",
            "--json",
            "headRefName,url",
        )
    )
    if len(requests) >= 1000:
        raise ValueError(
            "open PR listing reached its cap; publication needs maintainer review"
        )
    if any(
        item["headRefName"].startswith("runtime-publication/")
        and item["headRefName"] != branch
        for item in requests
    ):
        raise ValueError(
            "merge the pending runtime metadata PR before another publication"
        )
    request = next((item for item in requests if item["headRefName"] == branch), None)
    if git(workspace, "ls-remote", "--heads", "origin", branch):
        git(workspace, "fetch", "origin", branch)
        git(workspace, "checkout", "-b", branch, "FETCH_HEAD")
        git(workspace, "merge-base", "--is-ancestor", f"origin/{BRANCH}", "HEAD")
    else:
        git(workspace, "checkout", "-b", branch)
    changed_paths = git(
        workspace, "diff", "--name-only", f"origin/{BRANCH}", "HEAD"
    ).splitlines()
    if not set(changed_paths) <= {path, feed_path}:
        raise ValueError("publication branch contains non-request changes")
    destination = workspace / path
    existed = destination.exists()
    write_immutable(destination, record_bytes)
    if not existed:
        git(workspace, "add", path)
        git(
            workspace,
            "commit",
            "-m",
            f"docs(release): publish {record['distribution']} runtime {record['release_id']}",
        )
        git(workspace, "push", "origin", f"HEAD:refs/heads/{branch}")
    # On rerun, recover the original record commit instead of manufacturing a new identity.
    record_commit = git(workspace, "log", "-1", "--format=%H", "--", path)
    read_public(f"{RAW}/{record_commit}/{path}", record_bytes)
    feed = json.loads((workspace / feed_path).read_bytes())
    updated = add_reference(
        feed, record, record_commit, hashlib.sha256(record_bytes).hexdigest()
    )
    # An identical successful request is a no-op, including its existing timestamp.
    if updated["releases"] != feed["releases"]:
        (workspace / feed_path).write_bytes(encoded(updated))
        git(workspace, "add", feed_path)
        git(
            workspace,
            "commit",
            "-m",
            f"docs(release): advance {record['distribution']} stable runtime feed",
        )
        git(workspace, "push", "origin", f"HEAD:refs/heads/{branch}")
    feed_commit = git(workspace, "rev-parse", "HEAD")
    verify_published(workspace, feed_commit)
    url = (
        request["url"]
        if request
        else github(
            "pr",
            "create",
            "--base",
            BRANCH,
            "--head",
            branch,
            "--title",
            f"Publish OpenMinion {record['runtime_version']} runtime metadata",
            "--body",
            f"- publish verified {record['distribution']} runtime metadata only\n- retain both runtime feeds and commit-pinned records\n- merge with a merge commit, not squash/rebase, to retain record SHAs\n\nValidation\n- producer and public artifact identities match\n- anonymous record and proposed feed readback passed\n- main feed remains unchanged until protected merge\n\nAfter merge, verify public main readback and merge main back into dev.",
        )
    )
    return {
        "status": "pending_merge",
        "pull_request_url": url,
        "record_commit": record_commit,
        "feed_commit": feed_commit,
        "record_path": path,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--version")
    parser.add_argument("--source-commit")
    parser.add_argument("--release-id")
    parser.add_argument(
        "--published-at",
        help="Producer completion timestamp; stable across reruns",
    )
    parser.add_argument("--workspace", type=Path)
    parser.add_argument("--producer-wheel", type=Path)
    parser.add_argument("--binary-release-tag")
    parser.add_argument("--binary-release-id")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--verify-main", action="store_true")
    args = parser.parse_args()
    if args.verify_main:
        if args.workspace is None:
            parser.error("main verification requires its full-history checkout")
        verify_published(args.workspace)
        print(json.dumps({"status": "public_main_verified"}))
        return
    if args.binary_release_tag or args.binary_release_id:
        if not args.binary_release_tag or not args.binary_release_id:
            parser.error("binary publication requires release tag and release id")
        if any((args.version, args.source_commit, args.release_id, args.published_at)):
            parser.error("binary publication does not accept source-release inputs")
        record = official_binary_record(args.binary_release_tag, args.binary_release_id)
        if args.dry_run:
            print(
                json.dumps(
                    {"record_path": record_path(record), "record": record}, indent=2
                )
            )
            return
        if args.workspace is None:
            parser.error("publication requires a fresh workspace directory")
        print(json.dumps(publish(record, args.workspace)))
        return
    if not all((args.version, args.source_commit, args.release_id, args.published_at)):
        parser.error(
            "publication requires version, source-commit, release-id and published-at"
        )
    if not args.published_at.endswith("Z"):
        parser.error("published-at must be a UTC timestamp")
    datetime.fromisoformat(args.published_at)
    record = official_record(args.version, args.source_commit, args.release_id)
    if args.producer_wheel:
        verify_producer_wheel(record, args.producer_wheel)
    record["published_at"] = args.published_at
    if args.dry_run:
        print(
            json.dumps({"record_path": record_path(record), "record": record}, indent=2)
        )
    else:
        if args.workspace is None:
            parser.error("publication requires a fresh workspace directory")
        if args.producer_wheel is None:
            parser.error("publication requires the approved producer wheel")
        print(json.dumps(publish(record, args.workspace)))


def verify_producer_wheel(record: dict, wheel: Path) -> None:
    """Bind source provenance to the producer's exact wheel, without executing it."""
    identity = record["pypi"]
    if (
        wheel.name != identity["filename"]
        or wheel.stat().st_size != identity["size_bytes"]
    ):
        raise ValueError("producer wheel differs from official release")
    with wheel.open("rb") as content:
        digest = hashlib.file_digest(content, "sha256").hexdigest()
    if digest != identity["sha256"]:
        raise ValueError("producer wheel differs from official release")


if __name__ == "__main__":
    main()
