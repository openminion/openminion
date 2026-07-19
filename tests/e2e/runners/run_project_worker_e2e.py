from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys

_ROOT = Path(__file__).resolve().parents[3]
_PYTHON = _ROOT / ".venv" / "bin" / "python3.11"

if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from tests.e2e.project_worker.harness import (  # noqa: E402
    scenario_ids,
    scenarios_for_suite,
    soak_pilot_specs,
    write_project_pilot_artifacts,
)
from tests.e2e.project_worker.harness.registry import PROJECT_WORKER_SCENARIOS  # noqa: E402


def suite_names() -> tuple[str, ...]:
    return ("local", "pilot", "live", "deep", "soak", "all")


def _default_pilot_output_dir() -> Path:
    return (
        _ROOT.parent
        / "workspace-tmp"
        / "long-horizon-project-worker-v3-2026-07-03"
        / "pilots"
    )


def _selected_scenarios(name: str):
    by_id = {scenario.scenario_id: scenario for scenario in PROJECT_WORKER_SCENARIOS}
    if name in by_id:
        return (by_id[name],)
    return scenarios_for_suite(name)


def _pytest_targets(name: str) -> tuple[str, ...]:
    targets: list[str] = []
    seen: set[str] = set()
    for scenario in _selected_scenarios(name):
        for target in scenario.pytest_targets:
            if target not in seen:
                seen.add(target)
                targets.append(target)
    return tuple(targets)


def _run(name: str) -> int:
    scenarios = _selected_scenarios(name)
    if not scenarios:
        options = ", ".join((*suite_names(), *scenario_ids()))
        print(f"usage: run_project_worker_e2e.py [{options}]", file=sys.stderr)
        return 2
    env = os.environ.copy()
    env.setdefault("PYTHONDONTWRITEBYTECODE", "1")
    if any(scenario.live for scenario in scenarios):
        env["OPENMINION_LIVE_CLI_FOCUS_E2E"] = "1"
    if any(scenario.complex for scenario in scenarios):
        env["OPENMINION_LIVE_CLI_FOCUS_COMPLEX_E2E"] = "1"
    command = [
        str(_PYTHON),
        "-m",
        "pytest",
        "-q",
        *_pytest_targets(name),
        "-ra",
    ]
    return subprocess.call(command, cwd=_ROOT, env=env)


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    mode = args[0] if args else "local"
    if mode in {"--list", "list"}:
        for name in (
            "pilot-artifacts",
            "soak-artifacts",
            *suite_names(),
            *scenario_ids(),
        ):
            print(name)
        return 0
    if mode == "pilot-artifacts":
        output_dir = Path(args[1]) if len(args) > 1 else _default_pilot_output_dir()
        artifacts = write_project_pilot_artifacts(output_dir)
        for artifact in artifacts:
            print(f"{artifact.pilot_id}: {artifact.json_path}")
        return 0
    if mode == "soak-artifacts":
        output_dir = Path(args[1]) if len(args) > 1 else _default_pilot_output_dir()
        artifacts = write_project_pilot_artifacts(output_dir, specs=soak_pilot_specs())
        for artifact in artifacts:
            print(f"{artifact.pilot_id}: {artifact.json_path}")
        return 0
    return _run(mode)


if __name__ == "__main__":
    raise SystemExit(main())
