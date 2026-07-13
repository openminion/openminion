from __future__ import annotations

from pathlib import Path

from openminion.cli.presentation.custom_commands import (
    CustomCommand,
    discover_custom_commands,
    discover_with_warnings,
    render_command,
)


# ── Discovery ─────────────────────────────────────────────────────


def test_discover_finds_project_commands(tmp_path: Path) -> None:
    project = tmp_path / "proj" / ".openminion" / "commands"
    project.mkdir(parents=True)
    (project / "review.md").write_text("Please review:\n$ARGUMENTS")

    result = discover_custom_commands(project_dir=project, user_dir=None)
    assert "/review" in result
    assert result["/review"].source == "project"


def test_discover_finds_user_commands(tmp_path: Path) -> None:
    user = tmp_path / "data" / "commands"
    user.mkdir(parents=True)
    (user / "summarize.md").write_text("Summarize: $1")

    result = discover_custom_commands(project_dir=None, user_dir=user)
    assert "/summarize" in result
    assert result["/summarize"].source == "user"


def test_project_shadows_user_on_collision(tmp_path: Path) -> None:
    project = tmp_path / "proj" / ".openminion" / "commands"
    project.mkdir(parents=True)
    (project / "foo.md").write_text("project version")

    user = tmp_path / "data" / "commands"
    user.mkdir(parents=True)
    (user / "foo.md").write_text("user version")

    result = discover_custom_commands(project_dir=project, user_dir=user)
    assert result["/foo"].source == "project"
    assert result["/foo"].body == "project version"


def test_discover_handles_missing_dirs(tmp_path: Path) -> None:
    result = discover_custom_commands(
        project_dir=tmp_path / "does-not-exist",
        user_dir=tmp_path / "also-does-not-exist",
    )
    assert result == {}


def test_discover_skips_non_md_files(tmp_path: Path) -> None:
    project = tmp_path / "proj" / ".openminion" / "commands"
    project.mkdir(parents=True)
    (project / "foo.md").write_text("real")
    (project / "bar.txt").write_text("not picked up")
    (project / "baz.py").write_text("nope")

    result = discover_custom_commands(project_dir=project, user_dir=None)
    assert set(result.keys()) == {"/foo"}


def test_discover_with_warnings_reports_invalid_name(tmp_path: Path) -> None:
    project = tmp_path / "proj" / ".openminion" / "commands"
    project.mkdir(parents=True)
    (project / "Has-Spaces .md").write_text("invalid")

    result, warnings = discover_with_warnings(project_dir=project, user_dir=None)
    assert result == {}
    assert any("invalid slash name" in w for w in warnings)


# ── Frontmatter parsing ──────────────────────────────────────────


def test_frontmatter_description_model_agent_extracted(tmp_path: Path) -> None:
    project = tmp_path / ".openminion" / "commands"
    project.mkdir(parents=True)
    (project / "x.md").write_text(
        "---\n"
        "description: Run a code review\n"
        "model: anthropic/claude-3-5-sonnet-latest\n"
        "agent: reviewer\n"
        "---\n"
        "Review the code:\n$ARGUMENTS"
    )

    result = discover_custom_commands(project_dir=project, user_dir=None)
    cmd = result["/x"]
    assert cmd.description == "Run a code review"
    assert cmd.model == "anthropic/claude-3-5-sonnet-latest"
    assert cmd.agent == "reviewer"
    assert cmd.body.startswith("Review the code:")


def test_frontmatter_optional(tmp_path: Path) -> None:
    project = tmp_path / ".openminion" / "commands"
    project.mkdir(parents=True)
    (project / "y.md").write_text("just a body\n$ARGUMENTS")

    result = discover_custom_commands(project_dir=project, user_dir=None)
    cmd = result["/y"]
    assert cmd.description == ""
    assert cmd.model == ""
    assert cmd.body == "just a body\n$ARGUMENTS"


def test_frontmatter_handles_quoted_values(tmp_path: Path) -> None:
    project = tmp_path / ".openminion" / "commands"
    project.mkdir(parents=True)
    (project / "z.md").write_text(
        '---\ndescription: "Has: colons and spaces"\n---\nbody'
    )

    result = discover_custom_commands(project_dir=project, user_dir=None)
    assert result["/z"].description == "Has: colons and spaces"


# ── Render interpolation ─────────────────────────────────────────


def _cmd(body: str, *, slash: str = "/test") -> CustomCommand:
    return CustomCommand(
        slash=slash, body=body, source="project", path=Path("/fake.md")
    )


def test_render_arguments_placeholder() -> None:
    rendered = render_command(_cmd("review: $ARGUMENTS"), arg_string="src/foo.py")
    assert rendered == "review: src/foo.py"


def test_render_positional_args() -> None:
    rendered = render_command(
        _cmd("first=$1 second=$2 third=$3"), arg_string="alpha beta gamma"
    )
    assert rendered == "first=alpha second=beta third=gamma"


def test_render_missing_positional_substitutes_empty() -> None:
    rendered = render_command(_cmd("only=$1 and=$2"), arg_string="alpha")
    assert rendered == "only=alpha and="


def test_render_arguments_preserved_when_no_args() -> None:
    rendered = render_command(_cmd("review: $ARGUMENTS"), arg_string="")
    assert rendered == "review: "


def test_render_at_file_inlines_file_contents(tmp_path: Path) -> None:
    target = tmp_path / "data.txt"
    target.write_text("file-contents-here")
    rendered = render_command(
        _cmd("see: @data.txt"), arg_string="", working_dir=tmp_path
    )
    assert "file-contents-here" in rendered


def test_render_at_file_preserves_token_when_missing(tmp_path: Path) -> None:
    rendered = render_command(
        _cmd("see: @nonexistent.txt"), arg_string="", working_dir=tmp_path
    )
    # Token preserved verbatim when the file doesn't exist.
    assert "@nonexistent.txt" in rendered


def test_render_bang_cmd_inlines_stdout(tmp_path: Path) -> None:
    rendered = render_command(
        _cmd("output: !`echo hello`"),
        arg_string="",
        working_dir=tmp_path,
    )
    assert "output: hello" in rendered


def test_render_bang_cmd_timeout_surfaces_marker(tmp_path: Path) -> None:
    rendered = render_command(
        _cmd("waiting: !`sleep 10`"),
        arg_string="",
        working_dir=tmp_path,
    )
    assert "timed out" in rendered


def test_render_combination_args_then_file_then_cmd(tmp_path: Path) -> None:
    target = tmp_path / "spec.md"
    target.write_text("(spec body)")
    rendered = render_command(
        _cmd("review $1 against @spec.md (date: !`echo 2026`)"),
        arg_string="my-feature",
        working_dir=tmp_path,
    )
    assert "review my-feature against (spec body)" in rendered
    assert "date: 2026" in rendered


# ── Integration: discover + render round-trip ────────────────────


def test_discover_then_render_full_flow(tmp_path: Path) -> None:
    project = tmp_path / ".openminion" / "commands"
    project.mkdir(parents=True)
    (project / "ship.md").write_text(
        "---\ndescription: Ship a feature\n---\nShip: $ARGUMENTS"
    )

    discovered = discover_custom_commands(project_dir=project, user_dir=None)
    cmd = discovered["/ship"]
    rendered = render_command(cmd, arg_string="auth-flow")
    assert rendered == "Ship: auth-flow"
