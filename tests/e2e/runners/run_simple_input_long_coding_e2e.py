from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time

_ROOT = Path(__file__).resolve().parents[3]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from tests.helpers.runtime_roots import isolate_runtime_roots  # noqa: E402

isolate_runtime_roots(prefix="openminion-silc-")

_CONFIG_ENV = "OPENMINION_CLI_FOCUS_E2E_CONFIG"
_ARTIFACT_ENV = "OPENMINION_SILC_E2E_ARTIFACT_ROOT"
_SCENARIOS = (
    "plain-restart-repair",
    "research-then-code",
    "delegated-review",
)


def _root(env: dict[str, str]) -> Path:
    configured = str(env.get(_ARTIFACT_ENV, "")).strip()
    if configured:
        return Path(configured).expanduser().resolve()
    return (
        _ROOT.parent
        / "workspace-tmp"
        / f"simple-input-long-coding-{time.strftime('%Y%m%dT%H%M%S')}"
    )


def _run(targets: tuple[str, ...], env: dict[str, str]) -> int:
    return subprocess.call(
        [sys.executable, "-m", "pytest", "-q", *targets, "-ra"],
        cwd=_ROOT,
        env=env,
    )


def _evidence(root: Path, *, live_result: int | None) -> list[dict[str, object]]:
    evidence = []
    for scenario_id in _SCENARIOS:
        path = root / f"silc-{scenario_id}-evidence.json"
        if path.is_file():
            payload = json.loads(path.read_text(encoding="utf-8"))
            evidence.append(
                {
                    "scenario_id": scenario_id,
                    "disposition": payload.get("disposition", "unavailable"),
                    "path": path.name,
                }
            )
        elif live_result is not None:
            evidence.append(
                {
                    "scenario_id": scenario_id,
                    "disposition": "unavailable",
                    "path": None,
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
    root = _root(env)
    root.mkdir(parents=True, exist_ok=True)
    env[_ARTIFACT_ENV] = str(root)
    env["OPENMINION_CLI_FOCUS_E2E_ARTIFACT_ROOT"] = str(root)
    results: dict[str, int] = {}
    if mode in {"local", "all"}:
        results["local"] = _run(
            ("tests/e2e/cli/focus/test_simple_input_project.py",), env
        )
    if mode in {"live", "all"} and not results.get("local"):
        config_path = Path(str(env.get(_CONFIG_ENV, ""))).expanduser()
        if not config_path.is_file():
            print(f"live SILC E2E requires {_CONFIG_ENV}", file=sys.stderr)
            return 2
        config = json.loads(config_path.read_text(encoding="utf-8"))
        env.setdefault("OPENMINION_CLI_FOCUS_E2E_AGENT", str(config["default_agent"]))
        env["OPENMINION_LIVE_CLI_FOCUS_E2E"] = "1"
        env["OPENMINION_LIVE_CLI_FOCUS_COMPLEX_E2E"] = "1"
        config.setdefault("module_configs", {}).setdefault("brain", {})[
            "request_handoff"
        ] = {"enabled": True}
        with tempfile.TemporaryDirectory(prefix="openminion-silc-config-") as tmp:
            derived_config = Path(tmp) / "agents.json"
            derived_config.write_text(
                json.dumps(config, indent=2, sort_keys=True) + "\n", encoding="utf-8"
            )
            derived_config.chmod(0o600)
            env[_CONFIG_ENV] = str(derived_config)
            results["live"] = _run(
                ("tests/e2e/cli/focus/test_live_simple_input_project.py",), env
            )
    (root / "runner-summary.json").write_text(
        json.dumps(
            {
                "artifact_root": str(root),
                "mode": mode,
                "results": results,
                "scenario_evidence": _evidence(root, live_result=results.get("live")),
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
