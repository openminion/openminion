#!/usr/bin/env python3

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path


def _build_pythonpath(repo_root: Path) -> str:
    entries: list[str] = []

    def _append(path_value: Path) -> None:
        resolved = str(path_value.resolve())
        if resolved not in entries:
            entries.append(resolved)

    openminion_src = repo_root / "openminion" / "src"
    if openminion_src.exists():
        _append(openminion_src)

    for package_dir in sorted(repo_root.glob("openminion-*/src")):
        if package_dir.exists():
            _append(package_dir)

    return os.pathsep.join(entries)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run deterministic CLI-chat E2E gate.")
    parser.add_argument(
        "--repo-root",
        default=str(Path(__file__).resolve().parents[4]),
        help="Repository root path.",
    )
    parser.add_argument(
        "--python-bin",
        default=sys.executable,
        help="Python interpreter to run `python -m openminion`.",
    )
    parser.add_argument(
        "--agent",
        default="",
        help="Agent ID to use for chat smoke. Defaults to the config default agent.",
    )
    parser.add_argument(
        "--session",
        default="ci-chat-gate",
        help="Session ID to use for chat smoke.",
    )
    parser.add_argument(
        "--config",
        default=".tmp/per-agent.json",
        help="Config path relative to `OPENMINION_HOME` (or absolute path).",
    )
    parser.add_argument(
        "--output",
        default="transcript.txt",
        help="Output transcript path (relative to the generated CLI-chat E2E root or absolute path).",
    )
    parser.add_argument(
        "--timeout-seconds",
        type=int,
        default=180,
        help="Subprocess timeout in seconds.",
    )
    parser.add_argument(
        "--require-scorecard",
        action="store_true",
        help="Fail the gate when the smartness scorecard artifact is missing or failing.",
    )
    parser.add_argument(
        "--scorecard-path",
        default="brain-smartness-scorecard.json",
        help="Scorecard artifact path relative to the generated CLI-chat E2E root (or absolute path).",
    )
    return parser.parse_args()


def resolve_data_root(home_root: Path) -> Path:
    # Keep gate artifacts under the effective home-root default data tree even
    # when the surrounding test process is carrying unrelated OPENMINION_DATA_ROOT
    # overrides from other suites.
    return (home_root / ".openminion").resolve()


def resolve_artifacts_root(home_root: Path) -> Path:
    return (resolve_data_root(home_root) / "runtime" / "cli-chat-e2e").resolve()


def _normalize_artifact_relative_path(raw_path: Path) -> Path:
    parts = raw_path.parts
    if parts[:2] == ("artifacts", "cli-chat-e2e"):
        return Path(*parts[2:]) if len(parts) > 2 else Path()
    if parts[:3] == (".openminion", "runtime", "cli-chat-e2e"):
        return Path(*parts[3:]) if len(parts) > 3 else Path()
    return raw_path


def _normalize_output_path(
    *,
    raw_path: Path,
    artifacts_root: Path,
    repo_root: Path,
) -> Path:
    if raw_path.is_absolute():
        repo_artifacts_root = (repo_root / "artifacts" / "cli-chat-e2e").resolve()
        try:
            relative = raw_path.resolve().relative_to(repo_artifacts_root)
        except ValueError:
            return raw_path
        return (artifacts_root / relative).resolve()
    return (artifacts_root / _normalize_artifact_relative_path(raw_path)).resolve()


def _resolve_agent_id(
    *,
    requested_agent: str,
    config_payload: dict[str, object],
) -> str:
    requested = str(requested_agent or "").strip()
    agents_raw = config_payload.get("agents")
    agents = agents_raw if isinstance(agents_raw, dict) else {}
    if requested:
        return requested

    default_agent = str(config_payload.get("default_agent", "") or "").strip()
    if default_agent:
        return default_agent

    agent_ids = [str(key).strip() for key in agents if str(key).strip()]
    if len(agent_ids) == 1:
        return agent_ids[0]

    raise ValueError(
        "CLI chat gate could not resolve an agent id: pass --agent or set "
        "'default_agent' in the config."
    )


def main() -> int:
    args = parse_args()
    repo_root = Path(args.repo_root).resolve()
    openminion_root = repo_root / "openminion"
    if not openminion_root.exists():
        raise FileNotFoundError(f"Missing openminion directory: {openminion_root}")

    home_root = os.environ.get("OPENMINION_HOME", "").strip() or str(repo_root)
    config_path = Path(str(args.config))
    if not config_path.is_absolute():
        config_path = (Path(home_root) / config_path).resolve()
    if not config_path.exists():
        raise FileNotFoundError(f"Missing config file: {config_path}")
    config_payload = json.loads(config_path.read_text(encoding="utf-8"))
    resolved_agent = _resolve_agent_id(
        requested_agent=str(args.agent),
        config_payload=config_payload,
    )

    output_path = Path(str(args.output))
    # Keep CLI gate evidence under the canonical generated-runtime root for the
    # effective home/data-root contract instead of creating a repo-top-level
    # artifacts/ tree.
    artifacts_root = resolve_artifacts_root(Path(home_root))
    output_path = _normalize_output_path(
        raw_path=output_path,
        artifacts_root=artifacts_root,
        repo_root=repo_root,
    )
    if output_path.name == "transcript.txt":
        config_stem = config_path.stem.strip() or "config"
        output_path = output_path.with_name(
            f"{args.session}-{resolved_agent}-{config_stem}.txt"
        )
    if output_path.exists():
        # Preserve prior runs (common in local debugging) without forcing
        # a timestamp or changing CI-friendly default names.
        for idx in range(2, 10_000):
            candidate = output_path.with_name(
                f"{output_path.stem}-{idx}{output_path.suffix}"
            )
            if not candidate.exists():
                output_path = candidate
                break
    output_path.parent.mkdir(parents=True, exist_ok=True)

    env = os.environ.copy()
    env["PYTHONPATH"] = _build_pythonpath(repo_root)
    env.setdefault("OPENMINION_HOME", home_root)
    env.setdefault("OPENMINION_DATA_ROOT", str(Path(home_root) / ".openminion"))

    cmd = [
        str(args.python_bin),
        "-m",
        "openminion",
        "--config",
        str(config_path),
        "chat",
        "--agent",
        resolved_agent,
        "--session",
        str(args.session),
        "--quiet",
        "--no-progress",
    ]

    proc = subprocess.run(
        cmd,
        cwd=str(openminion_root),
        env=env,
        input="hi\n/exit\n",
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        timeout=max(1, int(args.timeout_seconds)),
        check=False,
    )

    transcript = proc.stdout or ""
    output_path.write_text(transcript, encoding="utf-8")

    if proc.returncode != 0:
        print(
            f"CLI chat gate failed with exit code {proc.returncode}.", file=sys.stderr
        )
        print(f"Transcript: {output_path}", file=sys.stderr)
        return proc.returncode

    if "chat ready" not in transcript:
        print("CLI chat gate missing `chat ready` marker.", file=sys.stderr)
        print(f"Transcript: {output_path}", file=sys.stderr)
        return 1

    assistant_prefix = re.compile(
        rf"\[{re.escape(str(args.session))}\|{re.escape(resolved_agent)}\]\s+{re.escape(resolved_agent)}:"
    )
    if not assistant_prefix.search(transcript):
        print("CLI chat gate missing assistant response marker.", file=sys.stderr)
        print(f"Transcript: {output_path}", file=sys.stderr)
        return 1

    if args.require_scorecard:
        scorecard_path = Path(str(args.scorecard_path))
        if not scorecard_path.is_absolute():
            scorecard_path = (
                artifacts_root / _normalize_artifact_relative_path(scorecard_path)
            ).resolve()
        if not scorecard_path.exists():
            print(
                f"CLI chat gate missing required scorecard artifact: {scorecard_path}",
                file=sys.stderr,
            )
            return 1
        try:
            payload = json.loads(scorecard_path.read_text(encoding="utf-8"))
        except Exception as exc:  # noqa: BLE001
            print(f"CLI chat gate could not parse scorecard: {exc}", file=sys.stderr)
            return 1
        if not bool(payload.get("overall_pass")):
            print(
                f"CLI chat gate failed scorecard thresholds: {scorecard_path}",
                file=sys.stderr,
            )
            return 1

    print(f"CLI chat E2E gate passed. Transcript: {output_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
