from __future__ import annotations

import argparse
import io
import logging
import unittest
import os
import subprocess
import sys
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

from openminion.cli.parser.flags import add_json_output_flag
from openminion.cli.presentation.json_output import print_json_payload
from openminion.services.gateway.protocol import GatewayProtocolSession
from openminion.services.integration.skill_harness import run_skill_harness


def run_verify(args) -> int:
    suite = str(args.suite).strip().lower()
    project_root = _resolve_project_root()
    results: list[tuple[str, int]] = []
    env_snapshot = dict(os.environ)

    if suite in {"unit", "all"}:
        results.append(("unit", _run_unit_tests(args=args, project_root=project_root)))
        _restore_env(env_snapshot)

    if suite in {"smoke", "all"}:
        _restore_env(env_snapshot)
        results.append(("smoke", _run_smoke_checks(args=args)))

    if suite in {"skills", "all"}:
        _restore_env(env_snapshot)
        results.append(
            ("skills", _run_skill_checks(args=args, project_root=project_root))
        )

    overall = 0 if all(code == 0 for _, code in results) else 1

    if args.json:
        payload = {
            "ok": overall == 0,
            "suite": suite,
            "results": [
                {"name": name, "ok": code == 0, "exit_code": code}
                for name, code in results
            ],
        }
        print_json_payload(payload)
    else:
        print(f"verify: {'OK' if overall == 0 else 'FAIL'} suite={suite}")
        for name, code in results:
            print(f"- {name}: {'ok' if code == 0 else 'fail'}")

    return overall


def _restore_env(snapshot: dict[str, str]) -> None:
    os.environ.clear()
    os.environ.update(snapshot)


def _resolve_project_root() -> Path:
    return Path(__file__).resolve().parents[4]


def _run_unit_tests(*, args, project_root: Path) -> int:
    tests_dir = project_root / "tests"
    if not tests_dir.exists():
        print(f"verify unit: tests directory not found: {tests_dir}")
        return 1

    suite = unittest.defaultTestLoader.discover(
        start_dir=str(tests_dir),
        pattern=args.pattern,
        top_level_dir=str(project_root),
    )
    runner = unittest.TextTestRunner(verbosity=2 if args.verbose else 1)
    result = runner.run(suite)
    return 0 if result.wasSuccessful() else 1


def _run_smoke_checks(*, args) -> int:
    checks: list[tuple[str, int]] = []
    outputs: list[tuple[str, str]] = []
    previous_disable = logging.root.manager.disable
    logging.disable(logging.CRITICAL)
    try:
        doctor_cmd = [
            "doctor",
            "--check-turn",
            "--message",
            args.message,
            "--target",
            args.target,
        ]
        if args.channel:
            doctor_cmd += ["--channel", args.channel]
        if args.agent_id:
            doctor_cmd += ["--agent-id", args.agent_id]
        doctor_code, doctor_output = _run_smoke_subprocess(
            args=args, command_args=doctor_cmd
        )
        checks.append(("doctor", doctor_code))
        outputs.append(("doctor", doctor_output))

        protocol_stream = io.StringIO()
        with redirect_stdout(protocol_stream), redirect_stderr(protocol_stream):
            checks.append(("protocol", _run_protocol_smoke_check()))
        outputs.append(("protocol", protocol_stream.getvalue().strip()))

        agent_cmd = [
            "agent-check",
            "--message",
            args.message,
            "--target",
            args.target,
        ]
        if args.channel:
            agent_cmd += ["--channel", args.channel]
        if args.agent_id:
            agent_cmd += ["--agent-id", args.agent_id]
        agent_code, agent_output = _run_smoke_subprocess(
            args=args, command_args=agent_cmd
        )
        checks.append(("agent-check", agent_code))
        outputs.append(("agent-check", agent_output))
    finally:
        logging.disable(previous_disable)

    if args.verbose:
        for name, content in outputs:
            if content:
                print(f"verify smoke output ({name}):")
                print(content)

    if not args.json:
        for name, code in checks:
            print(f"verify smoke: {name} => {'ok' if code == 0 else 'fail'}")
    return 0 if all(code == 0 for _, code in checks) else 1


def _run_smoke_subprocess(*, args, command_args: list[str]) -> tuple[int, str]:
    cmd = [sys.executable, "-m", "openminion"]
    if args.config:
        cmd += ["--config", args.config]
    cmd.extend(command_args)
    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        env=dict(os.environ),
    )
    output = "\n".join(
        [chunk for chunk in (result.stdout, result.stderr) if chunk]
    ).strip()
    return result.returncode, output


def _run_protocol_smoke_check() -> int:
    session = GatewayProtocolSession()
    response = session.handle_frame(
        {
            "type": "req",
            "id": "verify-connect",
            "method": "connect",
            "params": {
                "min_protocol": 1,
                "max_protocol": 1,
                "client": {"name": "verify"},
            },
        }
    )
    if not response.get("ok"):
        return 1
    payload = response.get("payload")
    if not isinstance(payload, dict):
        return 1
    protocol = payload.get("protocol")
    if not isinstance(protocol, int):
        return 1
    return 0


def _run_skill_checks(*, args, project_root: Path) -> int:
    report = run_skill_harness(project_root)
    if args.verbose and not args.json:
        print(
            "verify skills: "
            f"total={report.total_skills} passed={report.passed_skills} "
            f"warnings={report.warning_count} errors={report.error_count}"
        )
        for error in report.global_errors:
            print(f"- global error: {error}")
        for result in report.results:
            print(
                f"- skill={result.skill_root} status={'ok' if result.ok else 'fail'} "
                f"warnings={len(result.warnings)} errors={len(result.errors)}"
            )
            for warning in result.warnings:
                print(f"  warning: {warning}")
            for error in result.errors:
                print(f"  error: {error}")
    elif not args.json:
        print(
            "verify skills: "
            f"{'ok' if report.ok else 'fail'} "
            f"(skills={report.total_skills} warnings={report.warning_count} errors={report.error_count})"
        )
    return 0 if report.ok else 1


def register(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    verify = subparsers.add_parser(
        "verify",
        help="Run iterative quality gates (unit, smoke, and skill harness)",
    )
    verify.add_argument(
        "suite",
        nargs="?",
        choices=["unit", "smoke", "skills", "all"],
        default="all",
        help="Verification suite to run (default: all)",
    )
    verify.add_argument(
        "--pattern",
        default="test_*.py",
        help="Unit test discovery pattern for verify unit/all (default: test_*.py)",
    )
    verify.add_argument(
        "--message",
        default="verify ping",
        help="Smoke-test input message for verify smoke/all",
    )
    verify.add_argument(
        "--target",
        default="verify",
        help="Smoke-test target for verify smoke/all",
    )
    verify.add_argument(
        "--channel",
        default=None,
        help="Smoke-test channel for verify smoke/all (default: selected agent default channel)",
    )
    verify.add_argument(
        "--agent-id",
        default=None,
        help="Agent id for smoke checks (default: resolve_default_agent_id(config))",
    )
    verify.add_argument(
        "--verbose", action="store_true", help="Verbose unit test output"
    )
    add_json_output_flag(verify)
    verify.set_defaults(handler=run_verify, needs_app=False)
