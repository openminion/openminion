from pathlib import Path

from scripts.validate.action_pins import find_unpinned_actions, main


PIN = "a" * 40


def _write_workflow(root: Path, content: str) -> None:
    workflow = root / ".github" / "workflows" / "ci.yml"
    workflow.parent.mkdir(parents=True)
    workflow.write_text(content, encoding="utf-8")


def test_find_unpinned_actions_reports_mutable_external_ref(tmp_path: Path) -> None:
    _write_workflow(
        tmp_path,
        "jobs:\n"
        "  test:\n"
        "    steps:\n"
        "      - uses: actions/checkout@v4\n"
        "      - uses: docker://alpine:3.20\n",
    )

    findings = find_unpinned_actions(tmp_path)

    assert [(item.path.as_posix(), item.line, item.reference) for item in findings] == [
        (".github/workflows/ci.yml", 4, "actions/checkout@v4"),
        (".github/workflows/ci.yml", 5, "docker://alpine:3.20"),
    ]


def test_find_unpinned_actions_accepts_immutable_and_local_refs(tmp_path: Path) -> None:
    _write_workflow(
        tmp_path,
        "jobs:\n"
        "  test:\n"
        "    steps:\n"
        f"      - uses: actions/checkout@{PIN} # v4.4.0\n"
        f"      - uses: docker://alpine@sha256:{'b' * 64}\n"
        "      - uses: ./.github/actions/local\n",
    )

    assert find_unpinned_actions(tmp_path) == []
    assert main(["--root", str(tmp_path)]) == 0
