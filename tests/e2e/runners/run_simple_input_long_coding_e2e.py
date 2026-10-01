from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import time

_ROOT = Path(__file__).resolve().parents[3]
_CONFIG_ENV = "OPENMINION_CLI_FOCUS_E2E_CONFIG"
_ARTIFACT_ENV = "OPENMINION_SILC_E2E_ARTIFACT_ROOT"
_SOURCE_REVISION_ENV = "OPENMINION_SILC_E2E_SOURCE_REVISION"
FROZEN_LIVE_AGENT_ID = "minimax-m2-7-coding"
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from tests.helpers.runtime_roots import isolate_runtime_roots  # noqa: E402

isolate_runtime_roots(prefix="openminion-silc-")

_LOCAL_TARGETS = (
    "tests/e2e/cli/focus/test_simple_input_project.py",
    "tests/scripts/test_simple_input_long_coding_runner.py",
)
_LIVE_TARGETS = (
    "tests/e2e/cli/focus/test_live_simple_input_project.py",
    "tests/e2e/cli/focus/test_live_model_neutral_tool_exposure.py",
)
_SCENARIOS = (
    "plain-multifile-repair",
    "research-then-code",
    "delegated-read-only-review",
)
_MNTE_EVIDENCE_FILES = (
    "focus/mnte-focus-live-evidence.json",
    "mnte/mnte-project-live-evidence.json",
)


def _artifact_root(env: dict[str, str]) -> Path:
    configured = str(env.get(_ARTIFACT_ENV, "")).strip()
    if configured:
        return Path(configured).expanduser().resolve()
    timestamp = time.strftime("%Y%m%dT%H%M%S")
    return _ROOT.parent / "workspace-tmp" / f"simple-input-long-coding-{timestamp}"


def _run(targets: tuple[str, ...], *, env: dict[str, str]) -> int:
    return subprocess.call(
        [sys.executable, "-m", "pytest", "-q", *targets, "-ra"],
        cwd=_ROOT,
        env=env,
    )


def _clean_source_revision(root: Path = _ROOT) -> str:
    status = subprocess.check_output(
        ["git", "status", "--porcelain"], cwd=root, text=True
    ).strip()
    if status:
        raise RuntimeError("live SILC E2E requires a clean source checkout")
    return subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=root, text=True
    ).strip()


def _live_agent_id(config: dict[str, object]) -> str:
    agent_id = str(config.get("default_agent") or "").strip()
    agents = config.get("agents")
    agent = agents.get(agent_id) if isinstance(agents, dict) else None
    if agent_id != FROZEN_LIVE_AGENT_ID or not isinstance(agent, dict):
        raise ValueError(f"live SILC E2E requires default_agent={FROZEN_LIVE_AGENT_ID}")
    if agent.get("default_act_profile") != "coding":
        raise ValueError("live SILC E2E requires the coding act profile")
    return agent_id


def _scenario_evidence(root: Path, *, live_result: int | None) -> list[dict]:
    evidence = []
    for scenario_id in _SCENARIOS:
        path = root / f"{scenario_id}-evidence.json"
        if path.is_file():
            payload = json.loads(path.read_text(encoding="utf-8"))
            evidence.append(
                {
                    "scenario_id": scenario_id,
                    "path": path.name,
                    "disposition": payload.get("disposition", "unavailable"),
                }
            )
        elif live_result is not None:
            evidence.append(
                {
                    "scenario_id": scenario_id,
                    "path": None,
                    "disposition": "unavailable",
                }
            )
    return evidence


def _mnte_evidence(root: Path, *, live_result: int | None) -> list[dict]:
    evidence = []
    for filename in _MNTE_EVIDENCE_FILES:
        path = root / filename
        if path.is_file():
            payload = json.loads(path.read_text(encoding="utf-8"))
            evidence.append(
                {
                    "scenario_id": payload.get("scenario_id", "unavailable"),
                    "path": str(path.relative_to(root)),
                    "disposition": payload.get("disposition", "unavailable"),
                }
            )
        elif live_result is not None:
            evidence.append(
                {
                    "scenario_id": None,
                    "path": None,
                    "disposition": "unavailable",
                }
            )
    return evidence


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    mode = args[0] if args else "local"
    if mode in {"list", "--list"}:
        print("local\nlive\nall")
        return 0
    if mode not in {"local", "live", "all"}:
        print(
            "usage: run_simple_input_long_coding_e2e.py [local|live|all]",
            file=sys.stderr,
        )
        return 2

    env = os.environ.copy()
    env.setdefault("PYTHONDONTWRITEBYTECODE", "1")
    env.setdefault("PYTHONPATH", "src")
    root = _artifact_root(env)
    root.mkdir(parents=True, exist_ok=True)
    env[_ARTIFACT_ENV] = str(root)
    env["OPENMINION_MNTE_E2E_ARTIFACT_ROOT"] = str(root / "mnte")
    env.setdefault("OPENMINION_CLI_FOCUS_E2E_ARTIFACT_ROOT", str(root / "focus"))

    results: dict[str, int] = {}
    if mode in {"local", "all"}:
        results["local"] = _run(_LOCAL_TARGETS, env=env)
    if mode in {"live", "all"} and not results.get("local"):
        config_path = Path(str(env.get(_CONFIG_ENV, ""))).expanduser()
        if not config_path.is_file():
            print(f"live SILC E2E requires {_CONFIG_ENV}", file=sys.stderr)
            return 2
        config = json.loads(config_path.read_text(encoding="utf-8"))
        try:
            agent_id = _live_agent_id(config)
            source_revision = _clean_source_revision()
        except (RuntimeError, ValueError) as exc:
            print(str(exc), file=sys.stderr)
            return 2
        env["OPENMINION_CLI_FOCUS_E2E_AGENT"] = agent_id
        env[_SOURCE_REVISION_ENV] = source_revision
        env["OPENMINION_LIVE_CLI_FOCUS_E2E"] = "1"
        env["OPENMINION_LIVE_CLI_FOCUS_COMPLEX_E2E"] = "1"
        results["live"] = _run(_LIVE_TARGETS, env=env)

    (root / "runner-summary.json").write_text(
        json.dumps(
            {
                "mode": mode,
                "results": results,
                "artifact_root": str(root),
                "scenario_evidence": _scenario_evidence(
                    root, live_result=results.get("live")
                ),
                "mnte_scenario_evidence": _mnte_evidence(
                    root, live_result=results.get("live")
                ),
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    return next((code for code in results.values() if code), 0)


if __name__ == "__main__":
    raise SystemExit(main())
