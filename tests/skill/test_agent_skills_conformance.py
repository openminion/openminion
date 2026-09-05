from __future__ import annotations

import os
from pathlib import Path

import pytest

from openminion.modules.skill.interfaces import SkillIngestAuthority
from openminion.modules.skill.diagnostics.harness import validate_skill
from openminion.modules.skill.runtime.skill import Skill
from openminion.modules.skill.runtime.skill import resources as bundle_resources


def _config(tmp_path: Path) -> dict[str, object]:
    return {
        "skill": {
            "sqlite_path": str(tmp_path / "skill.db"),
            "blob_root": str(tmp_path / "blob"),
            "fallback_root": str(tmp_path / "fallback"),
            "wal": False,
        }
    }


def test_agent_skills_metadata_and_resources_are_passive_and_progressive(
    tmp_path: Path,
) -> None:
    root = tmp_path / "portable-skill"
    (root / "references").mkdir(parents=True)
    (root / "assets").mkdir()
    (root / "scripts").mkdir()
    (root / "references" / "guide.md").write_text("Reference details", encoding="utf-8")
    (root / "assets" / "template.txt").write_text("template", encoding="utf-8")
    (root / "scripts" / "check.py").write_text(
        "print('not executed')", encoding="utf-8"
    )
    skill_path = root / "SKILL.md"
    skill_path.write_text(
        """---
name: portable-skill
description: Use portable resources safely.
license: Apache-2.0
compatibility: Requires a POSIX-like shell.
metadata:
  author: OpenMinion
allowed-tools: tool.shell tool.fetch
---

# Procedure
Read references/guide.md before acting.
""",
        encoding="utf-8",
    )

    ctl = Skill(_config(tmp_path))
    try:
        skill_id, version_hash, warnings = ctl.ingest_file(
            skill_path,
            authority=SkillIngestAuthority.local_operator(
                surface="test.agent_skills", principal_id="local:test"
            ),
        )
        assert "admission.pending" in warnings
        package = ctl.get_skill(skill_id, version_hash)
        portable = package.bundle_metadata["agent_skills"]
        assert portable["license"] == "Apache-2.0"
        assert portable["allowed_tools"] == ["tool.shell", "tool.fetch"]
        assert package.tools == []
        assert {item["path"] for item in package.resources} == {
            "references/guide.md",
            "assets/template.txt",
            "scripts/check.py",
        }
        assert all(item["executable"] is False for item in package.resources)

        loaded = ctl.read_skill_resource(
            skill_id=skill_id,
            version_hash=version_hash,
            resource_path="references/guide.md",
            max_chars=9,
        )
        assert loaded["content"] == "Reference"
        assert loaded["truncated"] is True
    finally:
        ctl.close()


def test_bundle_symlink_is_not_admitted_as_a_resource(tmp_path: Path) -> None:
    root = tmp_path / "portable-skill"
    (root / "references").mkdir(parents=True)
    outside = tmp_path / "outside.txt"
    outside.write_text("outside", encoding="utf-8")
    (root / "references" / "outside.txt").symlink_to(outside)
    skill_path = root / "SKILL.md"
    skill_path.write_text("# Procedure\nDo the task.", encoding="utf-8")

    ctl = Skill(_config(tmp_path))
    try:
        skill_id, version_hash, warnings = ctl.ingest_file(skill_path)
        assert "bundle.resources.symlink_skipped" in warnings
        assert ctl.get_skill(skill_id, version_hash).resources == []
    finally:
        ctl.close()


def test_bundle_directory_symlink_is_not_traversed(tmp_path: Path) -> None:
    root = tmp_path / "portable-skill"
    root.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.txt").write_text("outside", encoding="utf-8")
    (root / "linked").symlink_to(outside, target_is_directory=True)
    skill_path = root / "SKILL.md"
    skill_path.write_text("# Procedure\nDo the task.", encoding="utf-8")

    ctl = Skill(_config(tmp_path))
    try:
        skill_id, version_hash, warnings = ctl.ingest_file(skill_path)
        assert "bundle.resources.symlink_skipped" in warnings
        assert ctl.get_skill(skill_id, version_hash).resources == []
    finally:
        ctl.close()


def test_bundle_non_regular_file_is_skipped(tmp_path: Path) -> None:
    if not hasattr(os, "mkfifo"):
        pytest.skip("FIFO creation requires POSIX")
    root = tmp_path / "portable-skill"
    root.mkdir()
    os.mkfifo(root / "events.pipe")
    skill_path = root / "SKILL.md"
    skill_path.write_text("# Procedure\nDo the task.", encoding="utf-8")

    ctl = Skill(_config(tmp_path))
    try:
        skill_id, version_hash, warnings = ctl.ingest_file(skill_path)
        assert "bundle.resources.non_regular_skipped" in warnings
        assert ctl.get_skill(skill_id, version_hash).resources == []
    finally:
        ctl.close()


def test_bundle_resource_count_limit_stops_sorted_walk(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(bundle_resources, "SKILL_BUNDLE_MAX_RESOURCES", 2)
    root = tmp_path / "portable-skill"
    root.mkdir()
    for name in ("c.txt", "a.txt", "b.txt"):
        (root / name).write_text(name, encoding="utf-8")
    skill_path = root / "SKILL.md"
    skill_path.write_text("# Procedure\nDo the task.", encoding="utf-8")

    ctl = Skill(_config(tmp_path))
    try:
        skill_id, version_hash, warnings = ctl.ingest_file(skill_path)
        assert "bundle.resources.count_limit" in warnings
        assert [
            item["path"] for item in ctl.get_skill(skill_id, version_hash).resources
        ] == ["a.txt", "b.txt"]
    finally:
        ctl.close()


def test_bundle_resource_byte_limits_are_enforced(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(bundle_resources, "SKILL_BUNDLE_MAX_RESOURCE_BYTES", 3)
    monkeypatch.setattr(bundle_resources, "SKILL_BUNDLE_MAX_TOTAL_RESOURCE_BYTES", 5)
    root = tmp_path / "portable-skill"
    root.mkdir()
    (root / "a.txt").write_bytes(b"abc")
    (root / "b.txt").write_bytes(b"def")
    (root / "0-large.txt").write_bytes(b"large")
    skill_path = root / "SKILL.md"
    skill_path.write_text("# Procedure\nDo the task.", encoding="utf-8")

    ctl = Skill(_config(tmp_path))
    try:
        skill_id, version_hash, warnings = ctl.ingest_file(skill_path)
        assert "bundle.resources.file_size_limit" in warnings
        assert "bundle.resources.total_size_limit" in warnings
        assert [
            item["path"] for item in ctl.get_skill(skill_id, version_hash).resources
        ] == ["a.txt"]
    finally:
        ctl.close()


def test_bundle_preserves_generic_supporting_files_without_nested_skills(
    tmp_path: Path,
) -> None:
    root = tmp_path / "portable-skill"
    (root / "agents").mkdir(parents=True)
    (root / "eval-viewer").mkdir()
    (root / "tests").mkdir()
    (root / ".hidden").mkdir()
    (root / "nested").mkdir()
    (root / "agents" / "openai.yaml").write_text("name: ignored", encoding="utf-8")
    for relative_path in (
        "agents/analyzer.md",
        "agents/comparator.md",
        "agents/grader.md",
        "eval-viewer/generate_review.py",
        "eval-viewer/viewer.html",
        "tests/test_collect_inventory.py",
    ):
        path = root / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(relative_path, encoding="utf-8")
    (root / ".hidden" / "secret.txt").write_text("hidden", encoding="utf-8")
    (root / "nested" / "SKILL.md").write_text("# Nested", encoding="utf-8")
    (root / "nested" / "data.txt").write_text("nested", encoding="utf-8")
    skill_path = root / "SKILL.md"
    skill_path.write_text("# Procedure\nDo the task.", encoding="utf-8")

    ctl = Skill(_config(tmp_path))
    try:
        skill_id, version_hash, _warnings = ctl.ingest_file(skill_path)
        package = ctl.get_skill(skill_id, version_hash)
        assert {item["path"] for item in package.resources} == {
            "agents/analyzer.md",
            "agents/comparator.md",
            "agents/grader.md",
            "eval-viewer/generate_review.py",
            "eval-viewer/viewer.html",
            "tests/test_collect_inventory.py",
        }
        assert {item["kind"] for item in package.resources} == {"supporting"}
        assert all(item["executable"] is False for item in package.resources)
        loaded = ctl.read_skill_resource(
            skill_id=skill_id,
            version_hash=version_hash,
            resource_path="tests/test_collect_inventory.py",
        )
        assert loaded["content"] == "tests/test_collect_inventory.py"
    finally:
        ctl.close()


def test_portable_conformance_is_separate_from_native_harness(tmp_path: Path) -> None:
    root = tmp_path / "native-only"
    root.mkdir()
    (root / "SKILL.md").write_text(
        "## Purpose\nInventory a host.\n\n## Procedure\nCollect facts.\n",
        encoding="utf-8",
    )

    result = validate_skill(root)

    assert result.ok is True
    assert result.portable_conformance == {
        "ok": False,
        "errors": [
            "portable.front_matter_required",
            "portable.name_required",
            "portable.description_required",
        ],
    }


def test_portable_conformance_accepts_required_front_matter(tmp_path: Path) -> None:
    root = tmp_path / "portable-skill"
    root.mkdir()
    (root / "SKILL.md").write_text(
        "---\nname: portable-skill\ndescription: Collect facts.\n---\n"
        "\n## Purpose\nInventory a host.\n\n## Procedure\nCollect facts.\n",
        encoding="utf-8",
    )

    result = validate_skill(root)

    assert result.ok is True
    assert result.portable_conformance == {"ok": True, "errors": []}


def test_portable_conformance_rejects_malformed_yaml(tmp_path: Path) -> None:
    root = tmp_path / "portable-skill"
    root.mkdir()
    (root / "SKILL.md").write_text(
        "---\nname: portable-skill\ndescription: [unclosed\n---\n"
        "\n## Procedure\nCollect facts.\n",
        encoding="utf-8",
    )

    result = validate_skill(root)

    assert result.portable_conformance["ok"] is False
    assert "portable.front_matter_invalid" in result.portable_conformance["errors"]


@pytest.mark.parametrize(
    ("front_matter", "expected_error"),
    [
        ("description: Collect facts.", "portable.name_required"),
        (
            "name: Not Portable\ndescription: Collect facts.",
            "portable.name_invalid",
        ),
        ("name: portable-skill", "portable.description_required"),
        (
            f"name: portable-skill\ndescription: {'x' * 1025}",
            "portable.description_too_long",
        ),
        ("name: 123\ndescription: Collect facts.", "portable.name_invalid"),
        ("name: portable-skill\ndescription: 456", "portable.description_required"),
    ],
)
def test_portable_conformance_rejects_invalid_required_fields(
    tmp_path: Path, front_matter: str, expected_error: str
) -> None:
    root = tmp_path / "portable-skill"
    root.mkdir()
    (root / "SKILL.md").write_text(
        f"---\n{front_matter}\n---\n\n## Procedure\nCollect facts.\n",
        encoding="utf-8",
    )

    result = validate_skill(root)

    assert expected_error in result.portable_conformance["errors"]


def test_portable_front_matter_must_be_first_content(tmp_path: Path) -> None:
    root = tmp_path / "portable-skill"
    root.mkdir()
    (root / "SKILL.md").write_text(
        "\n---\nname: portable-skill\ndescription: Collect facts.\n---\n",
        encoding="utf-8",
    )

    result = validate_skill(root)

    assert "portable.front_matter_required" in result.portable_conformance["errors"]
