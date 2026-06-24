from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / "scripts" / "validate" / "focus" / "terminal_styles.py"
BASELINE = REPO_ROOT / "scripts" / "baselines" / "terminal_styles_baseline.txt"
BASELINE_HELPER = REPO_ROOT / "scripts" / "common" / "baseline_files.py"


def _run(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
    )


def _copy_script_fixture(fake_repo: Path) -> Path:
    fake_scripts = fake_repo / "scripts"
    fake_scripts.mkdir()
    fake_common = fake_scripts / "common"
    fake_common.mkdir()
    fake_baselines = fake_scripts / "baselines"
    fake_baselines.mkdir()
    shutil.copy(SCRIPT, fake_scripts / SCRIPT.name)
    shutil.copy(BASELINE_HELPER, fake_common / BASELINE_HELPER.name)
    return fake_scripts


def test_script_exists_and_runs() -> None:
    assert SCRIPT.is_file()
    result = _run()
    assert result.returncode in (0, 1)


def test_clean_baseline_passes() -> None:
    result = _run()
    assert result.returncode == 0
    assert "clean" in result.stdout


def test_baseline_file_exists_and_is_empty() -> None:
    assert BASELINE.is_file()
    content = BASELINE.read_text(encoding="utf-8").strip()
    assert content == ""


def test_planted_violation_fails(tmp_path: Path) -> None:
    # We can't modify the real tree, so we test via subprocess
    # with a planted scenario: create a tmpfile, copy the script,
    # and patch the path it walks.
    fake_repo = tmp_path / "fake_repo"
    fake_focus = fake_repo / "src" / "openminion" / "cli" / "tui" / "terminal"
    fake_focus.mkdir(parents=True)
    (fake_focus / "planted.py").write_text(
        'from rich.text import Text\nx = Text("planted", style="red")\n'
    )
    fake_scripts = _copy_script_fixture(fake_repo)
    fake_baselines = fake_scripts / "baselines"
    # Empty baseline.
    (fake_baselines / "terminal_styles_baseline.txt").write_text("")

    result = subprocess.run(
        [sys.executable, str(fake_scripts / SCRIPT.name)],
        cwd=fake_repo,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 1
    assert "planted.py" in result.stdout
    assert "red" in result.stdout


def test_baseline_match_suppresses_violation(tmp_path: Path) -> None:
    fake_repo = tmp_path / "fake_repo"
    fake_focus = fake_repo / "src" / "openminion" / "cli" / "tui" / "terminal"
    fake_focus.mkdir(parents=True)
    (fake_focus / "baselined.py").write_text(
        'from rich.text import Text\nx = Text("ok", style="red")\n'
    )
    fake_scripts = _copy_script_fixture(fake_repo)
    fake_baselines = fake_scripts / "baselines"
    # Baseline includes the planted line.
    (fake_baselines / "terminal_styles_baseline.txt").write_text(
        "src/openminion/cli/tui/terminal/baselined.py:2:red\n"
    )

    result = subprocess.run(
        [sys.executable, str(fake_scripts / SCRIPT.name)],
        cwd=fake_repo,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0
    assert "clean" in result.stdout


def test_update_baseline_writes_current_violations(tmp_path: Path) -> None:
    fake_repo = tmp_path / "fake_repo"
    fake_focus = fake_repo / "src" / "openminion" / "cli" / "tui" / "terminal"
    fake_focus.mkdir(parents=True)
    (fake_focus / "x.py").write_text(
        "from rich.text import Text\n"
        'a = Text("a", style="red")\n'
        'b = Text("b", style="bold yellow")\n'
    )
    fake_scripts = _copy_script_fixture(fake_repo)
    fake_baselines = fake_scripts / "baselines"
    (fake_baselines / "terminal_styles_baseline.txt").write_text("")

    result = subprocess.run(
        [sys.executable, str(fake_scripts / SCRIPT.name), "--update-baseline"],
        cwd=fake_repo,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0
    baseline_content = (fake_baselines / "terminal_styles_baseline.txt").read_text()
    assert "x.py:2:red" in baseline_content
    assert "x.py:3:yellow" in baseline_content


def test_pattern_does_not_match_token_rich_style_calls(tmp_path: Path) -> None:
    fake_repo = tmp_path / "fake_repo"
    fake_focus = fake_repo / "src" / "openminion" / "cli" / "tui" / "terminal"
    fake_focus.mkdir(parents=True)
    (fake_focus / "ok.py").write_text(
        "from rich.text import Text\n"
        "x = Text('ok', style=token_rich_style(StyleToken.ERROR))\n"
        "y = Text('ok2', style=_ERR_STYLE)\n"
    )
    fake_scripts = _copy_script_fixture(fake_repo)
    fake_baselines = fake_scripts / "baselines"
    (fake_baselines / "terminal_styles_baseline.txt").write_text("")

    result = subprocess.run(
        [sys.executable, str(fake_scripts / SCRIPT.name)],
        cwd=fake_repo,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0


def test_bare_dim_and_bold_do_not_trigger(tmp_path: Path) -> None:
    fake_repo = tmp_path / "fake_repo"
    fake_focus = fake_repo / "src" / "openminion" / "cli" / "tui" / "terminal"
    fake_focus.mkdir(parents=True)
    (fake_focus / "modifiers.py").write_text(
        "from rich.text import Text\n"
        'a = Text("a", style="dim")\n'
        'b = Text("b", style="bold")\n'
        'c = Text("c", style="italic")\n'
        'd = Text("d", style="dim italic")\n'
    )
    fake_scripts = _copy_script_fixture(fake_repo)
    fake_baselines = fake_scripts / "baselines"
    (fake_baselines / "terminal_styles_baseline.txt").write_text("")

    result = subprocess.run(
        [sys.executable, str(fake_scripts / SCRIPT.name)],
        cwd=fake_repo,
        capture_output=True,
        text=True,
    )
    # Modifiers alone should not trigger.
    assert result.returncode == 0, result.stdout
