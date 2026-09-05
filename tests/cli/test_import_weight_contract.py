from __future__ import annotations

import json
import importlib.util
import os
from pathlib import Path
import subprocess
import sys


_REPO_ROOT = Path(__file__).resolve().parents[2]
_SOURCE_ROOT = _REPO_ROOT / "src"
_MODULE_SENTINEL = "OWPR_IMPORTED_MODULES="


def _root_help_imports() -> tuple[subprocess.CompletedProcess[str], set[str]]:
    script = f"""
import json
import sys
from openminion.cli.main import main
try:
    main(["--help"])
except SystemExit as exc:
    if exc.code not in (None, 0):
        raise
print({_MODULE_SENTINEL!r} + json.dumps(sorted(sys.modules)))
"""
    env = os.environ.copy()
    env["PYTHONPATH"] = os.pathsep.join((str(_SOURCE_ROOT), str(_REPO_ROOT)))
    completed = subprocess.run(
        [sys.executable, "-c", script],
        cwd=_REPO_ROOT,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )
    module_line = next(
        line
        for line in completed.stdout.splitlines()
        if line.startswith(_MODULE_SENTINEL)
    )
    return completed, set(json.loads(module_line.removeprefix(_MODULE_SENTINEL)))


def test_root_help_avoids_runtime_and_renderer_imports() -> None:
    completed, imported = _root_help_imports()

    assert completed.returncode == 0
    assert "usage: openminion" in completed.stdout
    forbidden_prefixes = (
        "openminion.services.bootstrap.onboarding",
        "openminion.modules.llm.providers",
        "openminion.modules.tool.contracts",
        "rich",
        "prompt_toolkit",
    )
    assert not {
        name
        for name in imported
        if any(
            name == prefix or name.startswith(f"{prefix}.")
            for prefix in forbidden_prefixes
        )
    }


def test_performance_runner_owns_both_import_surface_scenarios(
    tmp_path: Path, monkeypatch
) -> None:
    runner = _REPO_ROOT / "scripts" / "smoke" / "performance_baseline.py"
    spec = importlib.util.spec_from_file_location("owpr_import_surface_runner", runner)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    monkeypatch.setattr(
        module, "_capture_tracemalloc_metrics", lambda *args, **kwargs: None
    )
    options = module.RunOptions(
        workspace_root=_REPO_ROOT.parent,
        output_root=tmp_path / "measurements",
        python=Path(sys.executable),
        runs=1,
        timeout_seconds=30,
        include_importtime=False,
        profile=False,
        threshold_mode="off",
    )

    scenario_ids = {
        "terminal_import_surface",
        "interactive_runtime_import_surface",
    }
    assert scenario_ids <= set(module.DEFAULT_SCENARIOS)
    for scenario_id in scenario_ids:
        run = module.run_scenario(scenario_id, options)
        assert run.ok is True
