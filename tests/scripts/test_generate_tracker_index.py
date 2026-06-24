from __future__ import annotations

import importlib.util
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[3]
GENERATOR_PATH = REPO_ROOT / "docs" / "scripts" / "generate_tracker_index.py"


def _load_generator():
    spec = importlib.util.spec_from_file_location(
        "generate_tracker_index", GENERATOR_PATH
    )
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load tracker-index generator from {GENERATOR_PATH}")
    module = importlib.util.module_from_spec(spec)
    sys.modules["generate_tracker_index"] = module
    spec.loader.exec_module(module)
    return module


def _write_tracker(
    root: Path,
    relative: str,
    *,
    title: str,
    original_report: str = "2026-05-01",
    last_updated: str = "2026-05-02",
    status: str = "`in_progress`",
    completion: str = "50% (1/2 done)",
    priority: str = "`P1`",
    owner: str = "codex",
) -> None:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "\n".join(
            [
                f"# {title}",
                "",
                f"Original report: {original_report}",
                f"Last updated: {last_updated}",
                f"Priority: {priority}",
                f"Owner: {owner}",
                f"Overall status: {status}",
                f"Overall completion: {completion}",
                "",
            ]
        ),
        encoding="utf-8",
    )


def test_collect_entries_includes_root_and_bucket_trackers(tmp_path: Path) -> None:
    generator = _load_generator()
    root = tmp_path / "trackers"
    _write_tracker(root, "openminion-execution-tracker.md", title="Program Tracker")
    _write_tracker(root, "wip/a-tracker.md", title="A Tracker")
    _write_tracker(root, "qa/b-tracker.md", title="B Tracker", status="`done`")
    _write_tracker(root, "done/c-tracker.md", title="C Tracker", status="`done`")
    _write_tracker(root, "superseded/ignored.md", title="Ignored Tracker")

    entries = generator.collect_entries(root)

    assert [entry.bucket for entry in entries] == ["root", "wip", "qa", "done"]
    assert [str(entry.path) for entry in entries] == [
        "openminion-execution-tracker.md",
        "wip/a-tracker.md",
        "qa/b-tracker.md",
        "done/c-tracker.md",
    ]
    assert entries[1].title == "A Tracker"
    assert entries[1].overall_completion == "50% (1/2 done)"


def test_render_index_is_deterministic_and_escapes_cells(tmp_path: Path) -> None:
    generator = _load_generator()
    root = tmp_path / "trackers"
    _write_tracker(
        root,
        "wip/pipe-tracker.md",
        title="Pipe | Tracker",
        owner="owner | team",
    )
    entries = generator.collect_entries(root)

    first = generator.render_index(entries)
    second = generator.render_index(entries)

    assert first == second
    assert "<!-- Generated tracker index; do not edit by hand. -->" in first
    assert "| `wip` | 1 |" in first
    assert "[Pipe \\| Tracker](wip/pipe-tracker.md)" in first
    assert "owner \\| team" in first


def test_main_check_detects_stale_output(tmp_path: Path) -> None:
    generator = _load_generator()
    root = tmp_path / "trackers"
    output = root / "INDEX.md"
    _write_tracker(root, "wip/a-tracker.md", title="A Tracker")

    assert generator.main(["--root", str(root), "--output", str(output)]) == 0
    assert (
        generator.main(["--root", str(root), "--output", str(output), "--check"]) == 0
    )

    output.write_text("stale\n", encoding="utf-8")
    assert (
        generator.main(["--root", str(root), "--output", str(output), "--check"]) == 1
    )
