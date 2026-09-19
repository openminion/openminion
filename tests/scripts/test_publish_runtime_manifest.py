"""Manifest revisions are immutable and feed advancement retains older records."""

import json
import subprocess
import hashlib
from pathlib import Path
from unittest.mock import patch

import pytest
from scripts.ci import publish_runtime_manifest as publisher

from scripts.ci.publish_runtime_manifest import (
    BASE,
    add_reference,
    encoded,
    publish,
    write_immutable,
)
from scripts.ci.publish_runtime_manifest import verify_producer, verify_producer_wheel


def test_observer_skips_testpypi_tags_before_publication_approval():
    workflow = (
        Path(__file__).resolve().parents[2] / ".github/workflows/runtime-manifests.yml"
    ).read_text()
    for marker in ("alpha", "beta", "rc"):
        assert (
            f"!contains(github.event.workflow_run.head_branch, '{marker}')" in workflow
        )


def seed_main(tmp_path, remote):
    root = tmp_path / "source"
    root.mkdir()
    publisher.git(root, "init", "-b", "main")
    publisher.git(root, "config", "user.name", "Fixture")
    publisher.git(root, "config", "user.email", "fixture@example.invalid")
    (root / "README.md").write_text("# Existing source repository\n")
    (root / "src").mkdir()
    (root / "src/core.py").write_text("unchanged = True\n")
    for source in ("pypi", "binary"):
        path = root / BASE / source / "channels/stable.json"
        path.parent.mkdir(parents=True)
        path.write_bytes(
            encoded(
                {
                    "schema_version": 1,
                    "product": "openminion",
                    "distribution": source,
                    "channel": "stable",
                    "published_at": "2026-09-18T00:00:00Z",
                    "releases": [],
                }
            )
        )
    publisher.git(root, "add", ".")
    publisher.git(root, "commit", "-m", "feat: existing source and empty feeds")
    publisher.git(root, "remote", "add", "origin", str(remote))
    publisher.git(root, "push", "origin", "main")
    return root


@pytest.fixture(autouse=True)
def github_requests(monkeypatch):
    requests = []

    def github(*args):
        if args[:2] == ("pr", "list"):
            return json.dumps(requests)
        assert args[:2] == ("pr", "create")
        assert args[args.index("--base") + 1] == "main"
        branch = args[args.index("--head") + 1]
        requests.append(
            {
                "headRefName": branch,
                "url": "https://github.com/openminion/openminion/pull/1",
            }
        )
        return requests[-1]["url"]

    monkeypatch.setattr(publisher, "github", github)
    return requests


def test_producer_wheel_must_be_the_official_release_bytes(tmp_path):
    path = tmp_path / "openminion-1.0.0-py3-none-any.whl"
    path.write_bytes(b"official")
    record = {
        "pypi": {
            "filename": path.name,
            "size_bytes": 8,
            "sha256": hashlib.sha256(b"official").hexdigest(),
        }
    }
    verify_producer_wheel(record, path)
    path.write_bytes(b"replaced")
    with pytest.raises(ValueError, match="differs"):
        verify_producer_wheel(record, path)


@pytest.mark.parametrize(
    "mutation",
    [
        None,
        "testpypi",
        "failed",
        "fork",
        "source",
        "tag",
        "reviewers",
        "refs",
        "attempt",
    ],
)
def test_publication_admits_only_protected_successful_production_producers(mutation):
    environment = {
        "protection_rules": [{"type": "required_reviewers", "reviewers": [{"id": 1}]}],
        "deployment_branch_policy": {"protected_branches": True},
    }
    run = {
        "path": ".github/workflows/release.yml",
        "event": "push",
        "conclusion": "success",
        "head_repository": {"full_name": "openminion/openminion"},
        "head_sha": "a" * 40,
        "head_branch": "v1.0.0",
        "run_attempt": 1,
    }
    jobs = [
        {"name": "Publish to PyPI (final release tags, gated)", "conclusion": "success"}
    ]
    tag_commit = "a" * 40
    if mutation == "testpypi":
        jobs[0]["name"] = "Publish to TestPyPI (pre-release tags)"
    elif mutation == "failed":
        jobs[0]["conclusion"] = "failure"
    elif mutation == "fork":
        run["head_repository"]["full_name"] = "other/openminion"
    elif mutation == "source":
        run["event"] = "pull_request"
    elif mutation == "tag":
        tag_commit = "b" * 40
    elif mutation == "reviewers":
        environment["protection_rules"] = []
    elif mutation == "refs":
        environment["deployment_branch_policy"] = None
    elif mutation == "attempt":
        run["run_attempt"] = 2
    if mutation:
        with pytest.raises(ValueError):
            verify_producer(environment, run, jobs, tag_commit, "a" * 40, "v1.0.0", 1)
    else:
        verify_producer(environment, run, jobs, tag_commit, "a" * 40, "v1.0.0", 1)


def test_existing_record_is_idempotent_but_never_overwritten(tmp_path):
    path = tmp_path / "record.json"
    write_immutable(path, b"original")
    write_immutable(path, b"original")
    with pytest.raises(ValueError, match="different bytes"):
        write_immutable(path, b"replacement")
    assert path.read_bytes() == b"original"


@pytest.mark.parametrize("failed_read", [None, 1, 2])
def test_real_git_publication_and_interrupted_rerun_retain_immutable_record(
    tmp_path, failed_read
):
    remote = tmp_path / "metadata.git"
    subprocess.run(
        ["git", "init", "--bare", str(remote)], check=True, capture_output=True
    )
    source = seed_main(tmp_path, remote)
    record = {
        "distribution": "pypi",
        "runtime_version": "1.0.0",
        "release_id": "source.1.1",
        "published_at": "2026-09-17T00:00:00Z",
    }
    original_git = publisher.git
    reads = 0

    def local_git(root, *args):
        if args[:3] == ("remote", "add", "origin"):
            return original_git(root, "remote", "add", "origin", str(remote))
        return original_git(root, *args)

    def public(url, expected):
        nonlocal reads
        reads += 1
        if reads == failed_read:
            raise ValueError("publication is unconfirmed")
        reference, path = url.removeprefix(publisher.RAW + "/").split("/", 1)
        content = subprocess.check_output(
            ["git", "--git-dir", str(remote), "show", f"{reference}:{path}"]
        )
        assert content == expected

    with (
        patch.object(publisher, "git", side_effect=local_git),
        patch.object(publisher, "read_public", side_effect=public),
    ):
        if failed_read:
            with pytest.raises(ValueError, match="unconfirmed"):
                publish(record, tmp_path / "first")
            feed = json.loads(
                subprocess.check_output(
                    [
                        "git",
                        "--git-dir",
                        str(remote),
                        "show",
                        f"main:{BASE}/pypi/channels/stable.json",
                    ]
                )
            )
            assert feed["releases"] == []
        else:
            publish(record, tmp_path / "first")
        recovered = publish(record, tmp_path / "recovered")
        repeated = publish(record, tmp_path / "repeated")
        assert recovered == repeated
        assert (
            len(
                json.loads(
                    subprocess.check_output(
                        [
                            "git",
                            "--git-dir",
                            str(remote),
                            "show",
                            f"runtime-publication/source.1.1:{BASE}/pypi/channels/stable.json",
                        ]
                    )
                )["releases"]
            )
            == 1
        )
        changed = {**record, "source_commit": "b" * 40}
        with pytest.raises(ValueError, match="different bytes"):
            publish(changed, tmp_path / "conflicting")
        assert (
            original_git(tmp_path / "conflicting", "rev-parse", "HEAD")
            == recovered["feed_commit"]
        )
        assert recovered["status"] == "pending_merge"
        assert original_git(source, "show", "main:src/core.py") == "unchanged = True"
        original_git(source, "fetch", "origin", "runtime-publication/source.1.1")
        original_git(
            source,
            "merge",
            "--no-ff",
            "FETCH_HEAD",
            "-m",
            "docs: merge runtime metadata",
        )
        original_git(source, "push", "origin", "main")
        published = publish(record, tmp_path / "merged")
        assert published["status"] == "published"
        assert (
            original_git(source, "show", "main:README.md")
            == "# Existing source repository"
        )
        assert original_git(source, "show", "main:src/core.py") == "unchanged = True"
        original_git(
            source, "merge-base", "--is-ancestor", recovered["record_commit"], "main"
        )
        original_git(source, "checkout", "-b", "dev", "main~1")
        (source / "src/draft.py").write_text("unreleased = True\n")
        original_git(source, "add", "src/draft.py")
        original_git(source, "commit", "-m", "feat: unreleased development")
        original_git(
            source, "merge", "main", "-m", "docs: sync published runtime metadata"
        )
        assert (
            original_git(source, "diff", "--name-only", "main", "dev", "--", BASE) == ""
        )
        assert (source / "src/draft.py").read_text() == "unreleased = True\n"


def test_stale_record_push_never_advances_feed(tmp_path):
    remote = tmp_path / "metadata.git"
    subprocess.run(
        ["git", "init", "--bare", str(remote)], check=True, capture_output=True
    )
    seed_main(tmp_path, remote)
    original_git = publisher.git
    pushes = 0

    def local_git(root, *args):
        nonlocal pushes
        if args[:3] == ("remote", "add", "origin"):
            return original_git(root, "remote", "add", "origin", str(remote))
        if args[0] == "push":
            pushes += 1
            if pushes == 1:
                raise subprocess.CalledProcessError(
                    1, ["git", "push"], stderr="non-fast-forward"
                )
        return original_git(root, *args)

    with (
        patch.object(publisher, "git", side_effect=local_git),
        patch.object(publisher, "read_public") as read,
    ):
        with pytest.raises(subprocess.CalledProcessError):
            publish(
                {
                    "distribution": "pypi",
                    "runtime_version": "1.0.0",
                    "release_id": "source.1.1",
                    "published_at": "2026-09-17T00:00:00Z",
                },
                tmp_path / "publication",
            )
        read.assert_not_called()
    assert (
        json.loads(
            subprocess.check_output(
                [
                    "git",
                    "--git-dir",
                    str(remote),
                    "show",
                    f"main:{BASE}/pypi/channels/stable.json",
                ]
            )
        )["releases"]
        == []
    )


def test_feed_retains_older_versions_and_prefers_latest_equal_version_revision():
    previous = {
        "runtime_version": "1.0.0",
        "manifest_url": "old",
        "manifest_sha256": "b" * 64,
    }
    feed = {
        "schema_version": 1,
        "product": "openminion",
        "distribution": "pypi",
        "channel": "stable",
        "published_at": "2026-09-17T00:00:00Z",
        "releases": [previous],
    }
    record = {
        "distribution": "pypi",
        "runtime_version": "1.0.0",
        "release_id": "source.2",
        "published_at": "2026-09-17T01:00:00Z",
    }
    result = add_reference(feed, record, "a" * 40, "c" * 64)
    assert result["releases"][1] == previous
    assert (
        "/" + "a" * 40 + "/releases/runtime/v1/pypi/releases/1.0.0/source.2.json"
        in result["releases"][0]["manifest_url"]
    )
    assert add_reference(result, record, "a" * 40, "c" * 64) == result
    assert feed["releases"] == [previous]


def test_feed_limit_cannot_silently_delete_recovery_records():
    feed = {
        "schema_version": 1,
        "product": "openminion",
        "distribution": "pypi",
        "channel": "stable",
        "releases": [{}] * 100,
    }
    record = {
        "distribution": "pypi",
        "runtime_version": "1.0.0",
        "release_id": "source.1",
    }
    with pytest.raises(ValueError, match="refusing truncation"):
        add_reference(feed, record, "a" * 40, "c" * 64)


@pytest.mark.parametrize("failed_read", [None, 1, 2])
def test_record_readback_precedes_feed_push_and_failures_remain_visible(
    tmp_path, failed_read
):
    root = tmp_path / "publication"
    record = {
        "distribution": "pypi",
        "runtime_version": "1.0.0",
        "release_id": "source.1",
        "published_at": "2026-09-17T00:00:00Z",
    }
    events = []

    def command(workspace, *args):
        events.append(args)
        if args[0] == "ls-remote":
            return ""
        if args[0] == "diff":
            return ""
        if args[0] == "checkout" and args[-1] == "origin/main":
            for source in ("pypi", "binary"):
                path = workspace / BASE / source / "channels/stable.json"
                path.parent.mkdir(parents=True)
                path.write_bytes(
                    encoded(
                        {
                            "schema_version": 1,
                            "product": "openminion",
                            "distribution": source,
                            "channel": "stable",
                            "published_at": record["published_at"],
                            "releases": [],
                        }
                    )
                )
        return "b" * 40

    reads = 0

    def public(url, expected):
        nonlocal reads
        reads += 1
        events.append(("read", url))
        if reads == failed_read:
            raise ValueError("publication is unconfirmed")
        if reads == 1:
            assert (
                json.loads((root / BASE / "pypi/channels/stable.json").read_bytes())[
                    "releases"
                ]
                == []
            )
        assert expected

    with (
        patch("scripts.ci.publish_runtime_manifest.git", side_effect=command),
        patch("scripts.ci.publish_runtime_manifest.read_public", side_effect=public),
    ):
        if failed_read:
            with pytest.raises(ValueError, match="unconfirmed"):
                publish(record, root)
        else:
            assert publish(record, root)["record_commit"] == "b" * 40
    push_indices = [index for index, event in enumerate(events) if event[0] == "push"]
    read_indices = [index for index, event in enumerate(events) if event[0] == "read"]
    assert push_indices[0] < read_indices[0]
    assert len(push_indices) == (1 if failed_read == 1 else 2)
    if len(push_indices) == 2:
        assert read_indices[0] < push_indices[1] < read_indices[1]
    assert (
        json.loads((root / BASE / "binary/channels/stable.json").read_bytes())[
            "releases"
        ]
        == []
    )


def test_pending_metadata_request_blocks_another_release(tmp_path, github_requests):
    github_requests.append(
        {"headRefName": "runtime-publication/source.other", "url": "existing"}
    )
    with patch.object(publisher, "git", return_value="") as commands:
        with pytest.raises(ValueError, match="pending runtime metadata PR"):
            publish(
                {
                    "distribution": "pypi",
                    "runtime_version": "1.0.0",
                    "release_id": "source.1",
                },
                tmp_path / "publication",
            )
    assert not any(call.args[1] == "push" for call in commands.call_args_list)


def test_truncated_pr_listing_cannot_hide_a_pending_metadata_request(
    tmp_path, github_requests
):
    github_requests.extend(
        {"headRefName": f"ordinary/{index}", "url": "existing"} for index in range(1000)
    )
    with patch.object(publisher, "git", return_value="") as commands:
        with pytest.raises(ValueError, match="listing reached its cap"):
            publish(
                {
                    "distribution": "pypi",
                    "runtime_version": "1.0.0",
                    "release_id": "source.1",
                },
                tmp_path / "publication",
            )
    assert not any(call.args[1] == "push" for call in commands.call_args_list)


@pytest.mark.parametrize("failure", ["url", "digest", "unreachable"])
def test_main_verification_rejects_invalid_or_unreachable_record(tmp_path, failure):
    record = b"record\n"
    path = f"{BASE}/pypi/releases/1.0.0/source.1.json"
    destination = tmp_path / path
    destination.parent.mkdir(parents=True)
    destination.write_bytes(record)
    reference = {
        "manifest_url": f"{publisher.RAW}/{'a' * 40}/{path}",
        "manifest_sha256": hashlib.sha256(record).hexdigest(),
    }
    if failure == "url":
        reference["manifest_url"] = reference["manifest_url"].replace("a" * 40, "main")
    elif failure == "digest":
        reference["manifest_sha256"] = "b" * 64
    for source in ("pypi", "binary"):
        feed = tmp_path / BASE / source / "channels/stable.json"
        feed.parent.mkdir(parents=True)
        feed.write_bytes(encoded({"releases": [reference] if source == "pypi" else []}))
    with (
        patch.object(publisher, "read_public"),
        patch.object(
            publisher,
            "git",
            side_effect=subprocess.CalledProcessError(1, ["git", "merge-base"]),
        ),
    ):
        with pytest.raises(
            subprocess.CalledProcessError if failure == "unreachable" else ValueError
        ):
            publisher.verify_published(tmp_path)
