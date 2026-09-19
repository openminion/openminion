from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import hashlib
from importlib.metadata import PackageNotFoundError, version
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import xml.etree.ElementTree as ET
from urllib.parse import urlparse

from openminion.base.config.base import ConfigError
from openminion.base.config.runtime.profile import (
    build_runtime_config,
    resolve_runtime_profile,
)
from openminion.base.redaction import redact_mapping
from openminion.cli.config import load_cli_config


_ROOT = Path(__file__).resolve().parents[3]
_CONFIG_ENV = "OPENMINION_CLI_FOCUS_E2E_CONFIG"
_SUMMARY_ENV = "OPENMINION_CLI_FOCUS_E2E_SUMMARY_OUTPUT"
_TIMEOUT_ENV = "OPENMINION_CLI_FOCUS_E2E_RUNNER_TIMEOUT_SECONDS"
_BASELINE_ENV = "OPENMINION_CLI_FOCUS_E2E_BASELINE"
BASELINE_CASES = (
    "tests/e2e/cli/focus/test_live_basic.py::test_live_focus_basic_turn[exact_reply]",
    "tests/e2e/cli/focus/test_live_tools.py::test_live_focus_tool_scenarios[time_tool]",
)


@dataclass(frozen=True)
class Suite:
    paths: tuple[str, ...]
    extra_args: tuple[str, ...] = ()
    live: bool = False
    complex: bool = False


SUITES: dict[str, Suite] = {
    "baseline-live": Suite(BASELINE_CASES, live=True),
    "local": Suite(("tests/e2e/cli/focus/test_local.py",)),
    "onboarding": Suite(
        ("tests/e2e/cli/focus/test_onboarding.py",),
        ("-k", "not live_provider"),
    ),
    "onboarding-live": Suite(
        ("tests/e2e/cli/focus/test_onboarding.py",),
        ("-k", "live_provider"),
        live=True,
    ),
    "matrix": Suite(("tests/e2e/cli/focus/test_deep_smoke_matrix.py",)),
    "adversarial-local": Suite(
        (
            "tests/e2e/cli/focus/test_deep_smoke_matrix.py",
            "tests/e2e/cli/focus/test_local.py",
            "tests/e2e/cli/focus/test_harness_artifacts.py",
            "tests/e2e/cli/focus/test_harness_assertions.py",
            "tests/cli/test_default_invocation.py",
            "tests/cli/test_focus_backend_selection.py",
            "tests/cli/presentation/test_permissions_menu.py",
            "tests/cli/interactive/terminal/test_overlays.py",
            "tests/cli/interactive/terminal/test_focus_input_queue.py",
            "tests/cli/interactive/terminal/test_streaming.py",
            "tests/cli/interactive/terminal/test_streaming_integration.py",
            "tests/cli/interactive/terminal/test_streaming_visuals.py",
            "tests/cli/interactive/terminal/test_pty_scrollback.py",
            "tests/cli/interactive/terminal/test_verbosity_render.py",
            "tests/cli/interactive/terminal/test_transcript.py",
            "tests/cli/interactive/terminal/test_fia_keybindings.py",
            "tests/tools/exec/test_sandbox_e2e.py",
            "tests/tools/exec/test_session_semantics.py",
            "tests/tools/exec/test_telemetry_ops.py",
            "tests/tools/test_policy_exec_approvals.py",
            "tests/tools/test_approval_pending.py",
            "tests/tools/exec/test_interfaces_contract.py",
            "tests/e2e/test_cli_chat_probe_runner.py",
            "tests/brain/modes/test_delegate_e2e.py",
            "tests/brain/modes/test_decompose_e2e.py",
            "tests/brain/modes/test_delegate_integration.py",
            "tests/brain/modes/test_decompose_integration.py",
            "tests/brain/modes/test_async_delegate_unit.py",
            "tests/brain/modes/test_async_delegate_integration.py",
            "tests/brain/tool_loops/test_plan_control.py",
            "tests/brain/loop/test_plan_control_progress_bridge.py",
            "tests/tools/test_agent_delegation.py",
            "tests/modules/brain/loop/tools/test_engine_characterization.py",
            "tests/modules/brain/loop/tools/test_mutating_file_repetition.py",
            "tests/brain/tools/test_readonly_gate.py",
            "tests/cli/interactive/terminal/test_fpc_readonly_mode.py",
            "tests/tools/git/test_git_recovery.py",
            "tests/tools/github/test_write_policy.py",
            "tests/brain/loop/test_provider_retry_policy.py",
            "tests/brain/runtime/test_recovery_pipeline.py",
            "tests/tools/search/test_provider_chain.py",
            "tests/tools/fetch/test_plugin.py",
            "tests/tools/weather/test_plugin.py",
            "tests/tools/time/test_plugin.py",
        ),
    ),
    "core": Suite(("tests/e2e/cli/focus/test_live_basic.py",), live=True),
    "tools": Suite(("tests/e2e/cli/focus/test_live_tools.py",), live=True),
    "approval": Suite(
        (
            "tests/cli/interactive/terminal/test_overlays.py",
            "tests/cli/interactive/terminal/test_streaming_integration.py",
        ),
        ("-k", "approval"),
    ),
    "research": Suite(
        ("tests/e2e/cli/focus/test_live_complex.py",),
        ("-k", "research"),
        live=True,
        complex=True,
    ),
    "coding": Suite(
        ("tests/e2e/cli/focus/test_live_complex.py",),
        ("-k", "coding"),
        live=True,
        complex=True,
    ),
    "long-running": Suite(
        ("tests/e2e/cli/focus/test_live_complex.py",),
        ("-k", "long"),
        live=True,
        complex=True,
    ),
    "soak": Suite(
        ("tests/e2e/cli/focus/test_live_soak.py",),
        live=True,
        complex=True,
    ),
    "queued-input": Suite(
        (
            "tests/cli/interactive/terminal/test_focus_input_queue.py",
            "tests/cli/interactive/terminal/test_status_line.py",
        ),
        ("-k", "queued"),
    ),
    "hlpe": Suite(("tests/e2e/cli/focus/test_live_high_level_request.py",)),
    "progress-visibility": Suite(
        (
            "tests/cli/status",
            "tests/cli/interactive/terminal/test_single_indicator_rule.py",
            "tests/cli/interactive/terminal/test_status_line.py",
            "tests/cli/interactive/terminal/test_streaming.py",
        ),
    ),
    "tier-a": Suite(
        (
            "tests/cli/interactive/terminal/test_overlays.py",
            "tests/cli/interactive/terminal/test_fia_slash_catalog.py",
            "tests/policy/test_policy_service.py",
            "tests/brain/test_confirmation_replay_bridge_integration.py",
            "tests/integration/test_parallel_rollout_patch_apply.py",
        ),
    ),
    "regression": Suite(
        (
            "tests/e2e/cli/focus/test_local.py",
            "tests/cli/interactive",
            "tests/cli/presentation",
            "tests/cli/status",
        ),
    ),
    "live": Suite(
        (
            "tests/e2e/cli/focus/test_live_basic.py",
            "tests/e2e/cli/focus/test_live_tools.py",
        ),
        live=True,
    ),
    "complex": Suite(
        ("tests/e2e/cli/focus/test_live_complex.py",),
        live=True,
        complex=True,
    ),
    "deep": Suite(
        (
            "tests/e2e/cli/focus/test_live_basic.py",
            "tests/e2e/cli/focus/test_live_tools.py",
            "tests/e2e/cli/focus/test_live_complex.py",
        ),
        live=True,
        complex=True,
    ),
    "all": Suite(("tests/e2e/cli/focus",), live=True, complex=True),
}


def suite_names() -> tuple[str, ...]:
    return tuple(sorted(SUITES))


def _run(
    paths: tuple[str, ...],
    *,
    env: dict[str, str],
    extra_args: tuple[str, ...] = (),
    timeout_seconds: int | None = None,
) -> int:
    command = [
        sys.executable,
        "-m",
        "pytest",
        "-q",
        *paths,
        *(extra_args or []),
        "-ra",
    ]
    try:
        return subprocess.call(command, cwd=_ROOT, env=env, timeout=timeout_seconds)
    except subprocess.TimeoutExpired:
        rendered = " ".join(command)
        print(
            f"OpenMinion Focus E2E runner timed out after {timeout_seconds}s: {rendered}",
            file=sys.stderr,
        )
        return 124


def _runner_timeout_seconds(env: dict[str, str], suite: Suite) -> int | None:
    raw = str(env.get(_TIMEOUT_ENV, "")).strip()
    if raw:
        try:
            parsed = int(raw)
        except ValueError:
            parsed = 0
        if parsed > 0:
            return parsed
    if suite.paths == BASELINE_CASES:
        return 480
    if suite.complex:
        return 4200
    if suite.live:
        return 1500
    return None


def _live_preflight(env: dict[str, str]) -> str:
    if os.name != "posix":
        return "live Focus E2E requires macOS or Linux for PTY support"
    config_ref = str(env.get(_CONFIG_ENV, "")).strip()
    if not config_ref:
        return f"set {_CONFIG_ENV} to an existing OpenMinion config file"
    if not Path(config_ref).expanduser().is_file():
        return f"{_CONFIG_ENV} does not exist: {config_ref}"
    return ""


def _digest(payload: object) -> str:
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()


def _config_identity(env: dict[str, str]) -> dict[str, object]:
    config_ref = env.get(_CONFIG_ENV, "").strip()
    if not config_ref:
        return {"status": "unavailable"}
    try:
        config = load_cli_config(
            Path(config_ref).expanduser(),
            home_root=_ROOT.parent,
            data_root=_ROOT.parent / ".openminion",
        )
        agent_id = env.get("OPENMINION_CLI_FOCUS_E2E_AGENT", "minimax-m2-7")
        effective = build_runtime_config(config, agent_id=agent_id)
        profile = resolve_runtime_profile(effective, agent_id=agent_id)
        provider = getattr(effective.providers, profile.provider)
        settings = asdict(effective)
        relevant = {
            name: settings[name]
            for name in (
                "runtime",
                "security",
                "action_policy",
                "enabled_plugins",
            )
        }
        relevant["provider"] = asdict(provider)
        relevant["profile"] = asdict(profile)
        safe_config, _ = redact_mapping(relevant)
        provider_identity = provider.provider_identity or {}
        return {
            "status": "resolved",
            "provider": profile.provider,
            "adapter": provider_identity.get("transport_adapter"),
            "service_vendor": provider_identity.get("service_vendor"),
            "model": provider.model,
            "endpoint_authority": urlparse(provider.base_url).hostname,
            "credentials_available": bool(
                provider.api_key or env.get(provider.api_key_env, "").strip()
            ),
            "config_digest": _digest(safe_config),
            "output_token_limit": getattr(provider, "max_tokens", None),
        }
    except (ConfigError, OSError, ValueError, TypeError, AttributeError, KeyError):
        return {"status": "unavailable"}


def _run_identity(env: dict[str, str], suite: Suite) -> dict[str, object]:
    git = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    status = subprocess.run(
        ["git", "status", "--porcelain"],
        cwd=_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    corpus = hashlib.sha256()
    for node in suite.paths:
        source = _ROOT / node.split("::", 1)[0]
        corpus.update(node.encode())
        if source.is_file():
            corpus.update(source.read_bytes())
    # Assertions and prompts are also part of the two-case contract.
    if suite.paths == BASELINE_CASES:
        for name in ("assertions.py", "scenarios.py"):
            corpus.update((_ROOT / "tests/e2e/cli/focus/harness" / name).read_bytes())
    dependencies = {}
    for package in ("openminion", "pytest", "pexpect", "prompt-toolkit"):
        try:
            dependencies[package] = version(package)
        except PackageNotFoundError:
            dependencies[package] = None
    return {
        "candidate_commit": git.stdout.strip() if git.returncode == 0 else None,
        "dirty": bool(status.stdout.strip()) if status.returncode == 0 else None,
        "corpus_digest": corpus.hexdigest(),
        "config_identity": _config_identity(env),
        "environment": {
            "python": sys.version,
            "platform": sys.platform,
            "dependencies": dependencies,
        },
    }


def _case_accounting(path: Path | None, expected: tuple[str, ...]) -> dict[str, object]:
    result: dict[str, object] = {
        "expected": len(expected) if expected else None,
        "collected": 0,
        "executed": 0,
        "passed": 0,
        "failed": 0,
        "skipped": 0,
        "case_ids": [],
        "missing_cases": list(expected),
        "report_status": "absent",
        "complete": False,
    }
    if path is None or not path.is_file():
        return result
    try:
        root = ET.parse(path).getroot()
    except (ET.ParseError, OSError):
        result["report_status"] = "malformed"
        return result
    cases = root.findall(".//testcase")
    ids = [
        case.attrib.get("classname", "").replace(".", "/")
        + ".py::"
        + case.attrib.get("name", "")
        for case in cases
    ]
    skipped = sum(case.find("skipped") is not None for case in cases)
    errors = sum(case.find("error") is not None for case in cases)
    failures = sum(case.find("failure") is not None for case in cases)
    missing = sorted(set(expected) - set(ids))
    result.update(
        {
            "collected": len(cases),
            "executed": len(cases) - skipped - errors,
            "passed": len(cases) - skipped - errors - failures,
            "failed": errors + failures,
            "skipped": skipped,
            "case_ids": ids,
            "missing_cases": missing,
            "report_status": "present",
            "complete": bool(cases)
            and len(set(ids)) == len(ids)
            and not missing
            and (not expected or sorted(ids) == sorted(expected)),
        }
    )
    return result


def _safe_junit(path: Path | None, *, output_path: Path | None = None) -> Path | None:
    if path is None or not path.is_file():
        return None
    try:
        report = ET.parse(path)
    except (ET.ParseError, OSError):
        return None
    safe_root = ET.Element("testsuites")
    safe_suite = ET.SubElement(safe_root, "testsuite")
    for case in report.getroot().findall(".//testcase"):
        classname = case.attrib.get("classname", "")
        name = case.attrib.get("name", "")
        node = classname.replace(".", "/") + ".py::" + name
        if node not in BASELINE_CASES:
            classname, name = "unselected", "unselected"
        safe_case = ET.SubElement(
            safe_suite, "testcase", classname=classname, name=name
        )
        for outcome in ("failure", "error", "skipped"):
            if case.find(outcome) is not None:
                ET.SubElement(
                    safe_case,
                    outcome,
                    message="Private assertion details omitted from upload",
                )
    target = output_path or path.with_name(path.stem + ".safe.xml")
    ET.ElementTree(safe_root).write(target, encoding="utf-8", xml_declaration=True)
    return target


def _write_run_summary(
    *,
    path: Path | None,
    mode: str,
    suite: Suite,
    exit_code: int,
    elapsed_seconds: float,
    details: dict[str, object],
) -> None:
    if path is None:
        return
    payload = {
        "mode": mode,
        "paths": list(suite.paths),
        "extra_args": list(suite.extra_args),
        "live": suite.live,
        "complex": suite.complex,
        "exit_code": exit_code,
        "elapsed_seconds": round(elapsed_seconds, 3),
        **details,
    }
    safe_payload, _ = redact_mapping(payload)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(safe_payload, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    mode = args[0] if args else "local"
    if mode in {"--list", "list"}:
        for name in suite_names():
            print(name)
        return 0
    if mode not in SUITES:
        options = ", ".join(suite_names())
        print(f"usage: run_cli_focus_e2e.py [{options}]", file=sys.stderr)
        return 2
    env = os.environ.copy()
    env.setdefault("PYTHONDONTWRITEBYTECODE", "1")
    env["OPENMINION_PYTHON"] = sys.executable
    env.pop(_BASELINE_ENV, None)
    suite = SUITES[mode]
    summary_raw = env.get(_SUMMARY_ENV, "").strip()
    summary_path = Path(summary_raw).expanduser() if summary_raw else None
    baseline = mode == "baseline-live"
    if baseline and summary_path is None:
        summary_path = (
            _ROOT.parent
            / "workspace-tmp"
            / "openminion-cli-focus-e2e"
            / (datetime.now(timezone.utc).strftime("baseline-%Y%m%dT%H%M%S%fZ"))
            / "summary.json"
        )
    junit_path = summary_path.with_suffix(".junit.xml") if summary_path else None
    safe_junit_path = (
        summary_path.with_suffix(".junit.safe.xml")
        if summary_path and baseline
        else None
    )
    if baseline and summary_path:
        summary_path.parent.mkdir(parents=True, exist_ok=True)
        summary_path.with_suffix(".junit.xml").unlink(missing_ok=True)
        private_root = Path(
            tempfile.mkdtemp(
                prefix="focus-private-junit-", dir=summary_path.parent.parent
            )
        )
        junit_path = private_root / "pytest.xml"
        safe_junit_path.unlink(missing_ok=True)
    if junit_path and not baseline:
        junit_path.parent.mkdir(parents=True, exist_ok=True)
        # A new attempt must not inherit an earlier passing report.
        junit_path.unlink(missing_ok=True)
        junit_path.with_name(junit_path.stem + ".safe.xml").unlink(missing_ok=True)
    started = time.monotonic()
    timeout = _runner_timeout_seconds(env, suite)
    details = _run_identity(env, suite) if summary_path else {}
    details.update(
        {
            "started_at_utc": datetime.now(timezone.utc).isoformat(),
            "timeout_seconds": timeout,
            "usage": None,
        }
    )
    preflight_error = _live_preflight(env) if suite.live else ""
    if baseline and not preflight_error:
        config_identity = details["config_identity"]
        if config_identity.get("status") != "resolved":
            preflight_error = "baseline MiniMax configuration could not be resolved"
        elif str(
            config_identity.get("service_vendor", "")
        ).lower() != "minimax" or config_identity.get("endpoint_authority") not in {
            "api.minimax.io",
            "api.minimax.chat",
        }:
            preflight_error = (
                "baseline requires the official MiniMax provider identity and endpoint"
            )
        elif not config_identity.get("credentials_available"):
            preflight_error = (
                "baseline MiniMax credential is unavailable; cases were not run"
            )
    if preflight_error:
        print(
            f"OpenMinion Focus E2E preflight failed: {preflight_error}", file=sys.stderr
        )
        exit_code = 2
        disposition = "blocked_external"
    else:
        if suite.live:
            env["OPENMINION_LIVE_CLI_FOCUS_E2E"] = "1"
        if suite.complex:
            env["OPENMINION_LIVE_CLI_FOCUS_COMPLEX_E2E"] = "1"
        if baseline:
            env[_BASELINE_ENV] = "1"
        extra = suite.extra_args
        if junit_path:
            extra += (f"--junitxml={junit_path}",)
        try:
            exit_code = _run(
                suite.paths, env=env, extra_args=extra, timeout_seconds=timeout
            )
        except KeyboardInterrupt:
            exit_code = 130
        disposition = "pass" if exit_code == 0 else "failed"
    accounting = _case_accounting(junit_path, BASELINE_CASES if baseline else ())
    if baseline and not preflight_error:
        if accounting["report_status"] != "present" or exit_code in (124, 130):
            disposition = "inconclusive"
        elif not accounting["complete"] or accounting["passed"] != len(BASELINE_CASES):
            disposition = "failed"
        if disposition != "pass" and exit_code == 0:
            exit_code = 1
    safe_junit = (
        _safe_junit(junit_path, output_path=safe_junit_path) if baseline else None
    )
    if baseline:
        accounting["case_ids"] = [
            node if node in BASELINE_CASES else "<unexpected-case>"
            for node in accounting["case_ids"]
        ]
    details.update(
        {
            "case_accounting": accounting,
            "terminal_disposition": disposition,
            "preflight_error": preflight_error or None,
            "evidence_paths": {
                "summary": str(summary_path) if summary_path else None,
                "junit": str(safe_junit)
                if safe_junit
                else (
                    str(junit_path)
                    if not baseline and junit_path and junit_path.is_file()
                    else None
                ),
            },
        }
    )
    _write_run_summary(
        path=summary_path,
        mode=mode,
        suite=suite,
        exit_code=exit_code,
        elapsed_seconds=time.monotonic() - started,
        details=details,
    )
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
