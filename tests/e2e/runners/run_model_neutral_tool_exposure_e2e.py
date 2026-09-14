from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import time


_ROOT = Path(__file__).resolve().parents[3]
_CONFIG_ENV = "OPENMINION_CLI_FOCUS_E2E_CONFIG"
_ARTIFACT_ENV = "OPENMINION_MNTE_E2E_ARTIFACT_ROOT"
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from tests.helpers.runtime_roots import isolate_runtime_roots  # noqa: E402

isolate_runtime_roots(prefix="openminion-mnte-")

_LOCAL_TARGETS = (
    "tests/brain/tool_loops/test_shortlisting.py",
    "tests/brain/test_coding_handler_characterization.py",
    "tests/brain/modes/test_coding_contracts.py",
    "tests/brain/modes/test_coding_mode_integration.py",
    "tests/brain/tool_loops/test_engine.py",
    (
        "tests/modules/brain/loop/tools/test_engine_characterization.py::"
        "test_loop_keeps_requested_tools_active_across_later_requests"
    ),
    "tests/services/runtime/test_project_worker.py",
    "tests/brain/modes/test_delegate_e2e.py",
)
_LIVE_TARGETS = (
    "tests/e2e/cli/focus/test_live_model_neutral_tool_exposure.py",
)


def _artifact_root(env: dict[str, str]) -> Path:
    configured = str(env.get(_ARTIFACT_ENV, "")).strip()
    if configured:
        return Path(configured).expanduser()
    return (
        _ROOT.parent
        / "workspace-tmp"
        / f"model-neutral-tool-exposure-{time.strftime('%Y%m%dT%H%M%S')}"
    )


def _run(targets: tuple[str, ...], *, env: dict[str, str]) -> int:
    return subprocess.call(
        [sys.executable, "-m", "pytest", "-q", *targets, "-ra"],
        cwd=_ROOT,
        env=env,
    )


def _scenario_evidence(root: Path, *, live_result: int | None) -> list[dict]:
    evidence = []
    for path in sorted(root.rglob("mnte-*-live-evidence.json")):
        payload = json.loads(path.read_text(encoding="utf-8"))
        evidence.append(
            {
                "path": str(path.relative_to(root)),
                "disposition": payload.get("disposition", "unavailable"),
                "scenario_id": payload.get("scenario_id"),
                "scenario_results": payload.get("scenario_results", []),
            }
        )
    if live_result is not None and not evidence:
        evidence.append(
            {
                "path": None,
                "disposition": "pass" if live_result == 0 else "failed_without_evidence",
                "scenario_id": "live-corpus",
                "scenario_results": [],
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
        print("usage: run_model_neutral_tool_exposure_e2e.py [local|live|all]", file=sys.stderr)
        return 2

    env = os.environ.copy()
    env.setdefault("PYTHONDONTWRITEBYTECODE", "1")
    env.setdefault("PYTHONPATH", "src")
    root = _artifact_root(env)
    root.mkdir(parents=True, exist_ok=True)
    env[_ARTIFACT_ENV] = str(root)
    env.setdefault("OPENMINION_CLI_FOCUS_E2E_ARTIFACT_ROOT", str(root / "focus"))

    results: dict[str, int] = {}
    if mode in {"local", "all"}:
        results["local"] = _run(_LOCAL_TARGETS, env=env)
    if mode in {"live", "all"} and not results.get("local"):
        config_path = Path(str(env.get(_CONFIG_ENV, ""))).expanduser()
        if not config_path.is_file():
            print(f"live MNTE E2E requires {_CONFIG_ENV}", file=sys.stderr)
            return 2
        config = json.loads(config_path.read_text(encoding="utf-8"))
        env.setdefault(
            "OPENMINION_CLI_FOCUS_E2E_AGENT", str(config["default_agent"])
        )
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
                    root,
                    live_result=results.get("live"),
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
