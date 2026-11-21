"""Stored bundle export and its local operator CLI boundary."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest
import yaml

from openminion.modules.skill.cli import main
from openminion.modules.skill.errors import SkillError
from openminion.modules.skill.runtime.skill import Skill
from openminion.modules.skill.runtime.skill import resources
from tests.skill.admission_helpers import ingest_file_and_admit, ingest_text_and_admit


MARKDOWN = """---
name: retained-bundle
description: A retained procedure with passive resources.
---

# Procedure
Read references/guide.md and assets/data.bin.

# Verification
Check the result before reporting success.
"""
COMPANION = """# This comment is not retained.
interface:
  display_name: Retained bundle
policy:
  allow_implicit_invocation: false
"""


@pytest.fixture
def skill(tmp_path: Path):
    config = {
        "skill": {
            "sqlite_path": str(tmp_path / "skill.db"),
            "blob_root": str(tmp_path / "blobs"),
            "fallback_root": str(tmp_path / "fallback"),
            "wal": False,
        }
    }
    (tmp_path / "skill.json").write_text(json.dumps(config), encoding="utf-8")
    ctl = Skill(config)
    try:
        yield ctl
    finally:
        ctl.close()


@pytest.fixture
def bundle(skill: Skill, tmp_path: Path) -> tuple[str, str]:
    root = tmp_path / "source"
    for directory in ("agents", "references", "assets"):
        (root / directory).mkdir(parents=True)
    (root / "SKILL.md").write_text(MARKDOWN, encoding="utf-8")
    (root / "agents/openai.yaml").write_text(COMPANION, encoding="utf-8")
    (root / "references/guide.md").write_text(
        "Keep authored private text.", encoding="utf-8"
    )
    (root / "assets/data.bin").write_bytes(b"\x00\xff\xfe" * 20_000)
    skill_id, version, _ = ingest_file_and_admit(skill, root / "SKILL.md")
    return skill_id, version


def test_export_uses_pinned_retained_bytes(
    skill: Skill, bundle: tuple[str, str], tmp_path: Path
) -> None:
    skill_id, version_a = bundle
    _, version_b, _ = ingest_text_and_admit(
        skill, "retained-bundle", MARKDOWN + "\nChanged."
    )
    shutil.rmtree(tmp_path / "source")
    target = tmp_path / "export"

    result = skill.export_bundle(skill_id, version_a, target)

    assert result == {
        "ok": True,
        "skill_id": skill_id,
        "version_hash": version_a,
        "destination": str(target),
        "paths": [
            "SKILL.md",
            "agents/openai.yaml",
            "assets/data.bin",
            "references/guide.md",
        ],
        "warnings": ["export.openai_yaml_semantic_only"],
    }
    assert (target / "SKILL.md").read_bytes() == MARKDOWN.encode()
    assert (target / "assets/data.bin").read_bytes() == b"\x00\xff\xfe" * 20_000
    assert (target / "references/guide.md").read_text() == "Keep authored private text."
    assert yaml.safe_load(
        (target / "agents/openai.yaml").read_text()
    ) == yaml.safe_load(COMPANION)
    assert not (target / "agents/openai.yaml").read_text().startswith("#")
    assert skill.get_skill(skill_id).version_hash == version_b
    assert {
        str(path.relative_to(target)) for path in target.rglob("*") if path.is_file()
    } == set(result["paths"])


def test_export_round_trip_requires_local_admission(
    skill: Skill, bundle: tuple[str, str], tmp_path: Path
) -> None:
    skill_id, version = bundle
    target = tmp_path / "export"
    skill.export_bundle(skill_id, version, target)
    fresh = Skill(
        {
            "skill": {
                "sqlite_path": str(tmp_path / "fresh.db"),
                "blob_root": str(tmp_path / "fresh-blobs"),
                "fallback_root": str(tmp_path / "fresh-fallback"),
                "wal": False,
            }
        }
    )
    try:
        imported_id, imported_version, _ = fresh.ingest_file(target / "SKILL.md")
        imported = fresh.get_skill(imported_id, imported_version)
        original = skill.get_skill(skill_id, version)
        assert imported.sections == original.sections
        assert imported.resources == original.resources
        assert (
            imported.bundle_metadata["payload"] == original.bundle_metadata["payload"]
        )
        assert fresh.get_skill_version_state(
            skill_id=imported_id, version_hash=imported_version
        ) == {
            "admission_state": "pending",
            "active_version_hash": None,
        }
        assert fresh.catalog_summaries("test-agent") == []
    finally:
        fresh.close()
    duplicate_id, duplicate_version, warnings = skill.ingest_file(target / "SKILL.md")
    assert (duplicate_id, duplicate_version) == bundle
    assert "admission.duplicate_content" in warnings
    assert skill.get_skill(skill_id).version_hash == version


def test_export_pending_version_does_not_create_destination(
    skill: Skill, tmp_path: Path
) -> None:
    skill_id, version, _ = skill.ingest_text("retained-bundle", MARKDOWN)
    target = tmp_path / "export"
    with pytest.raises(SkillError, match="SKILL_EXPORT_NOT_ADMITTED"):
        skill.export_bundle(skill_id, version, target)
    assert not target.exists()


def test_export_plain_markdown_while_replacement_is_pending(
    skill: Skill, tmp_path: Path
) -> None:
    skill_id, version_a, _ = ingest_text_and_admit(skill, "retained-bundle", MARKDOWN)
    _, version_b, _ = skill.ingest_text(
        "retained-bundle", MARKDOWN + "\nPending change."
    )
    target = tmp_path / "export"
    result = skill.export_bundle(skill_id, version_a, target)
    assert result["paths"] == ["SKILL.md"]
    assert result["warnings"] == []
    assert (target / "SKILL.md").read_bytes() == MARKDOWN.encode()
    assert skill.get_skill(skill_id).version_hash == version_a
    assert (
        skill.get_skill_version_state(skill_id=skill_id, version_hash=version_b)[
            "admission_state"
        ]
        == "pending"
    )


@pytest.mark.parametrize(
    "version,code",
    [("", "INVALID_ARGUMENT"), ("  ", "INVALID_ARGUMENT"), ("f" * 64, "NOT_FOUND")],
)
def test_export_requires_exact_existing_version(
    skill: Skill, bundle: tuple[str, str], tmp_path: Path, version: str, code: str
) -> None:
    with pytest.raises(SkillError, match=code):
        skill.export_bundle(bundle[0], version, tmp_path / "export")
    assert not (tmp_path / "export").exists()


@pytest.mark.parametrize(
    "source", ["", "review:proposal-1", "https://example.invalid/SKILL.md"]
)
def test_export_does_not_regenerate_unsupported_sources(
    skill: Skill,
    bundle: tuple[str, str],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    source: str,
) -> None:
    package = skill.get_skill(*bundle)
    package.source_artifact_ref = source
    monkeypatch.setattr(skill, "get_skill", lambda *_: package)
    with pytest.raises(SkillError, match="SKILL_EXPORT_SOURCE_UNAVAILABLE"):
        skill.export_bundle(*bundle, tmp_path / "export")
    assert not (tmp_path / "export").exists()


@pytest.mark.parametrize("kind", ["source", "resource"])
@pytest.mark.parametrize(
    "damage,code",
    [
        ("missing", "SKILL_EXPORT_SOURCE_UNAVAILABLE"),
        ("corrupt", "SKILL_EXPORT_INTEGRITY_ERROR"),
    ],
)
def test_export_checks_blob_integrity_before_writing(
    skill: Skill,
    bundle: tuple[str, str],
    tmp_path: Path,
    kind: str,
    damage: str,
    code: str,
) -> None:
    package = skill.get_skill(*bundle)
    digest = (
        package.source_artifact_ref.removeprefix("artifact://sha256/")
        if kind == "source"
        else package.resources[0]["sha256"]
    )
    path = Path(skill._blob_store.stat(digest)["path"])
    if damage == "missing":
        path.unlink()
    else:
        path.write_bytes(b"corrupt")
    with pytest.raises(SkillError, match=code):
        skill.export_bundle(*bundle, tmp_path / "export")
    assert not (tmp_path / "export").exists()


@pytest.mark.parametrize(
    "names",
    [
        ["../escape"],
        ["/absolute"],
        ["a/../../escape"],
        ["a\\b"],
        ["C:escape"],
        ["a/./b"],
        ["a//b"],
        ["bad\x00name"],
        ["SKILL.md"],
        ["skill.MD"],
        ["agents/openai.yaml"],
        ["same", "same"],
        ["Name", "name"],
        ["file", "file/child"],
        ["dir/child", "dir"],
        ["Dir/a", "dir/b"],
        ["caf\u00e9", "cafe\u0301"],
    ],
)
def test_export_rejects_unsafe_or_colliding_paths(
    skill: Skill,
    bundle: tuple[str, str],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    names: list[str],
) -> None:
    package = skill.get_skill(*bundle)
    resource = package.resources[0]
    package.resources = [{**resource, "path": name} for name in names]
    monkeypatch.setattr(skill, "get_skill", lambda *_: package)
    with pytest.raises(SkillError, match="INVALID_ARGUMENT"):
        skill.export_bundle(*bundle, tmp_path / "export")
    assert not (tmp_path / "export").exists()
    assert not (tmp_path / "escape").exists()


@pytest.mark.parametrize(
    "kind",
    [
        "existing",
        "missing-parent",
        "symlink",
        "broken-link",
        "ancestor-link",
        "traversal",
    ],
)
def test_export_rejects_invalid_destination(
    skill: Skill, bundle: tuple[str, str], tmp_path: Path, kind: str
) -> None:
    target = tmp_path / "export"
    if kind == "existing":
        target.mkdir()
        (target / "keep").write_bytes(b"keep")
    elif kind == "missing-parent":
        target = tmp_path / "missing/export"
    elif kind in {"symlink", "broken-link", "ancestor-link"}:
        target.symlink_to(
            tmp_path / ("source" if kind != "broken-link" else "missing"),
            target_is_directory=True,
        )
        if kind == "ancestor-link":
            target /= "child"
    else:
        target = tmp_path / "source/../export"
    with pytest.raises(SkillError, match="INVALID_ARGUMENT"):
        skill.export_bundle(*bundle, target)
    if kind == "existing":
        assert (target / "keep").read_bytes() == b"keep"
    assert (tmp_path / "source/SKILL.md").read_text() == MARKDOWN


@pytest.mark.parametrize(
    "limit,value",
    [
        ("SKILL_BUNDLE_MAX_RESOURCES", 1),
        ("SKILL_BUNDLE_MAX_RESOURCE_BYTES", 10),
        ("SKILL_BUNDLE_MAX_TOTAL_RESOURCE_BYTES", 60_000),
    ],
)
def test_export_enforces_resource_limits(
    skill: Skill,
    bundle: tuple[str, str],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    limit: str,
    value: int,
) -> None:
    monkeypatch.setattr(resources, limit, value)
    with pytest.raises(SkillError, match="SKILL_EXPORT_INTEGRITY_ERROR"):
        skill.export_bundle(*bundle, tmp_path / "export")
    assert not (tmp_path / "export").exists()


def test_export_rejects_stored_size_mismatch(
    skill: Skill,
    bundle: tuple[str, str],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    package = skill.get_skill(*bundle)
    package.resources[0]["size_bytes"] += 1
    monkeypatch.setattr(skill, "get_skill", lambda *_: package)
    with pytest.raises(SkillError, match="SKILL_EXPORT_INTEGRITY_ERROR"):
        skill.export_bundle(*bundle, tmp_path / "export")
    assert not (tmp_path / "export").exists()


def test_export_cleans_partial_directory_on_write_failure(
    skill: Skill,
    bundle: tuple[str, str],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = tmp_path / "export"
    original_open = Path.open

    def fail_companion(path, mode="r", *args, **kwargs):
        if path == target / "agents/openai.yaml":
            raise OSError("injected write failure")
        return original_open(path, mode, *args, **kwargs)

    monkeypatch.setattr(Path, "open", fail_companion)
    with pytest.raises(SkillError, match="SKILL_EXPORT_WRITE_FAILED"):
        skill.export_bundle(*bundle, target)
    assert not target.exists()
    assert skill.get_skill(bundle[0]).version_hash == bundle[1]


def test_export_cli_returns_content_free_result(
    skill: Skill,
    bundle: tuple[str, str],
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    target = tmp_path / "export"
    assert (
        main(
            [
                "--config",
                str(tmp_path / "skill.json"),
                "export",
                bundle[0],
                "--version",
                bundle[1],
                "--destination",
                str(target),
            ]
        )
        == 0
    )
    result = json.loads(capsys.readouterr().out)
    assert result["version_hash"] == bundle[1]
    assert result["destination"] == str(target)
    assert "Keep authored private text." not in json.dumps(result)
    assert (target / "SKILL.md").read_text() == MARKDOWN


def test_export_cli_reports_pending_error(
    skill: Skill, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    skill_id, version, _ = skill.ingest_text("retained-bundle", MARKDOWN)
    with pytest.raises(SystemExit) as exc:
        main(
            [
                "--config",
                str(tmp_path / "skill.json"),
                "export",
                skill_id,
                "--version",
                version,
                "--destination",
                str(tmp_path / "export"),
            ]
        )
    assert exc.value.code == 1
    assert (
        json.loads(capsys.readouterr().out)["error"]["code"]
        == "SKILL_EXPORT_NOT_ADMITTED"
    )


@pytest.mark.parametrize("missing", ["--version", "--destination"])
def test_export_cli_requires_explicit_arguments(missing: str) -> None:
    options = {"--version": "f" * 64, "--destination": "export"}
    argv = ["export", "retained-bundle"]
    for key, value in options.items():
        if key != missing:
            argv.extend([key, value])
    with pytest.raises(SystemExit) as exc:
        main(argv)
    assert exc.value.code == 2


@pytest.mark.parametrize("command", ["disable", "remove"])
def test_dispatch_rejects_blank_mutation_reason_before_lookup(
    skill: Skill, tmp_path: Path, capsys: pytest.CaptureFixture[str], command: str
) -> None:
    with pytest.raises(SystemExit) as exc:
        main(
            [
                "--config",
                str(tmp_path / "skill.json"),
                command,
                "missing-skill",
                "--reason",
                "  ",
            ]
        )
    assert exc.value.code == 1
    error = json.loads(capsys.readouterr().out)["error"]
    assert error["code"] == "INVALID_ARGUMENT"
    assert error["message"] == f"--reason is required for {command}"
