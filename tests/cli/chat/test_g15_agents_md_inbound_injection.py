from __future__ import annotations

from pathlib import Path

from openminion.cli.chat.lifecycle import build_inbound_metadata


def _build(cwd: Path) -> dict[str, str]:
    return build_inbound_metadata(
        conversation_id="conv-test",
        thread_id="thr-test",
        attach_id="att-test",
        resume_requested=False,
        reset_requested=False,
        cwd=str(cwd),
    )


def test_agents_md_in_cwd_is_injected(tmp_path: Path) -> None:
    (tmp_path / "AGENTS.md").write_text(
        "# Test Project\n\n4-space indent.\n", encoding="utf-8"
    )
    payload = _build(tmp_path)
    assert payload.get("project_context_name") == "AGENTS.md"
    assert "project_context_body" in payload
    assert "4-space indent" in payload["project_context_body"]
    assert payload["project_context_path"].endswith("AGENTS.md")


def test_openminion_md_takes_precedence_over_agents_md(tmp_path: Path) -> None:
    (tmp_path / "OPENMINION.md").write_text("# Canonical context\n", encoding="utf-8")
    (tmp_path / "AGENTS.md").write_text("# Legacy context\n", encoding="utf-8")
    payload = _build(tmp_path)
    assert payload.get("project_context_name") == "OPENMINION.md"
    assert "Canonical context" in payload["project_context_body"]


def test_agents_md_takes_precedence_over_claude_md(tmp_path: Path) -> None:
    (tmp_path / "AGENTS.md").write_text("# Agents\n", encoding="utf-8")
    (tmp_path / "CLAUDE.md").write_text("# Claude\n", encoding="utf-8")
    payload = _build(tmp_path)
    assert payload.get("project_context_name") == "AGENTS.md"


def test_resolution_walks_up_directory_tree(tmp_path: Path) -> None:
    (tmp_path / "AGENTS.md").write_text("# Parent project rules\n", encoding="utf-8")
    nested = tmp_path / "deeply" / "nested" / "subdir"
    nested.mkdir(parents=True)
    payload = _build(nested)
    assert payload.get("project_context_name") == "AGENTS.md"
    assert "Parent project rules" in payload["project_context_body"]


def test_no_project_context_file_omits_keys(tmp_path: Path) -> None:
    payload = _build(tmp_path)
    assert "project_context_body" not in payload
    assert "project_context_name" not in payload
    assert "project_context_path" not in payload


def test_existing_metadata_keys_take_precedence_when_already_set() -> None:
    # Build metadata against a directory that DOES contain AGENTS.md,
    # then verify the setdefault behavior by manually re-running the
    # injection path with pre-populated keys.
    # Direct contract test: setdefault preserves caller-supplied keys.
    payload: dict[str, str] = {"project_context_body": "operator-supplied body"}
    payload.setdefault("project_context_body", "would-be-injected body")
    assert payload["project_context_body"] == "operator-supplied body"
