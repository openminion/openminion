from __future__ import annotations

import json
import os
import subprocess
import tempfile
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Sequence

from openminion.base.constants import (
    OPENMINION_TRACE_REQUESTS_DIR_ENV,
    OPENMINION_TRACE_REQUESTS_ENV,
)
from .phase_trace_grade import PhaseTraceGrade, TraceExpectation, grade_phase_trace


@dataclass(frozen=True)
class PhaseContractScenario:
    scenario_id: str
    mode: str
    prompts: tuple[str, ...] = ()
    expectation: TraceExpectation = field(default_factory=TraceExpectation)
    expected_issue_codes: tuple[str, ...] = ()
    required_stdout_substrings: tuple[str, ...] = ()
    fixture_trace_dir: str = ""
    description: str = ""


@dataclass(frozen=True)
class PhaseContractScenarioResult:
    scenario_id: str
    passed: bool
    mode: str
    trace_dirs: tuple[str, ...] = ()
    selected_trace_dir: str = ""
    grade: dict[str, Any] = field(default_factory=dict)
    stdout: str = ""
    stderr: str = ""
    expected_issue_codes: tuple[str, ...] = ()
    required_stdout_substrings: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["trace_dirs"] = list(self.trace_dirs)
        payload["expected_issue_codes"] = list(self.expected_issue_codes)
        payload["required_stdout_substrings"] = list(self.required_stdout_substrings)
        return payload


def default_phase_contract_scenarios(
    repo_root: str | Path,
) -> list[PhaseContractScenario]:
    root = Path(repo_root).expanduser().resolve()
    fixture_root = (
        root / "openminion" / "tests" / "fixtures" / "rsp_phase_contract_traces"
    )
    return [
        PhaseContractScenario(
            scenario_id="valid_respond",
            mode="live_chat",
            prompts=("hello",),
            expectation=TraceExpectation(
                expected_first_decide_mode="respond",
                required_purposes=("decide",),
                max_llm_calls=2,
            ),
            description="Simple direct response stays in decide/respond lane.",
        ),
        PhaseContractScenario(
            scenario_id="valid_act_loop",
            mode="live_chat",
            prompts=("what's the weather in Tokyo?",),
            expectation=TraceExpectation(
                expected_first_decide_mode="act",
                required_purposes=("decide", "validate", "judge"),
                max_llm_calls=5,
            ),
            description="Single-tool request stays on the act loop + judge path.",
        ),
        PhaseContractScenario(
            scenario_id="valid_orchestrate",
            mode="live_chat",
            prompts=("open browser and go to google.com",),
            expectation=TraceExpectation(
                expected_first_decide_mode="act",
                required_purposes=("decide", "plan"),
            ),
            description=(
                "Compound request enters act/orchestrate instead of collapsing to "
                "a single act turn."
            ),
        ),
        PhaseContractScenario(
            scenario_id="replay_continuity",
            mode="live_chat",
            prompts=("hi", "what's the weather in Tokyo?"),
            expectation=TraceExpectation(
                expected_first_decide_mode="act",
                required_purposes=("decide", "validate", "judge"),
                max_llm_calls=5,
            ),
            description="Second turn in same session keeps continuity without phase drift.",
        ),
        PhaseContractScenario(
            scenario_id="partial_coverage",
            mode="live_chat",
            prompts=("check the weather in Tokyo and book me a flight there",),
            expectation=TraceExpectation(
                expected_first_decide_mode="act",
                required_purposes=("decide", "validate"),
                max_llm_calls=7,
            ),
            required_stdout_substrings=("Reply 'continue'",),
            description=(
                "Feasibility gate surfaces partial coverage after act/orchestrate "
                "classification."
            ),
        ),
        PhaseContractScenario(
            scenario_id="invalid_decide_fixture",
            mode="fixture_trace",
            fixture_trace_dir=str(fixture_root / "invalid_decide"),
            expected_issue_codes=("decide_emitted_execution_tool_call",),
            description="Fixture trace where decide illegally emits an execution tool.",
        ),
        PhaseContractScenario(
            scenario_id="invalid_judge_fixture",
            mode="fixture_trace",
            fixture_trace_dir=str(fixture_root / "invalid_judge"),
            expected_issue_codes=("judge_emitted_prose", "closure_after_invalid_judge"),
            description="Fixture trace where judge emits prose and the turn closes.",
        ),
        PhaseContractScenario(
            scenario_id="malformed_wrapper_fixture",
            mode="fixture_trace",
            fixture_trace_dir=str(fixture_root / "malformed_wrapper"),
            expected_issue_codes=("malformed_trace_response",),
            description="Fixture trace where wrapper/normalization emitted malformed response shape.",
        ),
    ]


def run_phase_contract_matrix(
    *,
    repo_root: str | Path,
    openminion_root: str | Path,
    config_path: str | Path,
    agent: str,
    python_bin: str,
    scenarios: Sequence[PhaseContractScenario] | None = None,
    scenario_ids: Sequence[str] | None = None,
) -> list[PhaseContractScenarioResult]:
    available = list(scenarios or default_phase_contract_scenarios(repo_root))
    selected_ids = {
        str(item).strip() for item in (scenario_ids or []) if str(item).strip()
    }
    if selected_ids:
        available = [item for item in available if item.scenario_id in selected_ids]

    results: list[PhaseContractScenarioResult] = []
    for item in available:
        if item.mode == "fixture_trace":
            results.append(run_fixture_trace_scenario(item))
        elif item.mode == "live_chat":
            results.append(
                run_live_chat_scenario(
                    scenario=item,
                    repo_root=repo_root,
                    openminion_root=openminion_root,
                    config_path=config_path,
                    agent=agent,
                    python_bin=python_bin,
                )
            )
        else:
            raise ValueError(f"unsupported scenario mode: {item.mode}")
    return results


def run_fixture_trace_scenario(
    scenario: PhaseContractScenario,
) -> PhaseContractScenarioResult:
    trace_dir = Path(scenario.fixture_trace_dir).expanduser().resolve()
    grade = grade_phase_trace(trace_dir, expectation=scenario.expectation)
    passed = _scenario_passed(
        grade=grade,
        expected_issue_codes=scenario.expected_issue_codes,
        stdout="",
        required_stdout_substrings=scenario.required_stdout_substrings,
    )
    return PhaseContractScenarioResult(
        scenario_id=scenario.scenario_id,
        passed=passed,
        mode=scenario.mode,
        trace_dirs=(str(trace_dir),),
        selected_trace_dir=str(trace_dir),
        grade=grade.to_dict(),
        expected_issue_codes=scenario.expected_issue_codes,
        required_stdout_substrings=scenario.required_stdout_substrings,
    )


def run_live_chat_scenario(
    *,
    scenario: PhaseContractScenario,
    repo_root: str | Path,
    openminion_root: str | Path,
    config_path: str | Path,
    agent: str,
    python_bin: str,
) -> PhaseContractScenarioResult:
    repo_path = Path(repo_root).expanduser().resolve()
    app_root = Path(openminion_root).expanduser().resolve()
    config = Path(config_path).expanduser().resolve()
    session_id = f"{agent}::rsp-eval:{scenario.scenario_id}-{int(time.time())}"

    with tempfile.TemporaryDirectory(
        prefix=f"{scenario.scenario_id}-trace-"
    ) as trace_tmp:
        trace_root = Path(trace_tmp).resolve()
        env = dict(os.environ)
        env.update(
            {
                "OPENMINION_HOME": str(repo_path),
                OPENMINION_TRACE_REQUESTS_ENV: "1",
                OPENMINION_TRACE_REQUESTS_DIR_ENV: str(trace_root),
                "PYTHONPATH": "src",
            }
        )
        command = [
            str(python_bin),
            "-m",
            "openminion",
            "--config",
            str(config),
            "chat",
            "--agent",
            agent,
            "--session",
            session_id,
            "--quiet",
            "--no-progress",
        ]
        proc = subprocess.run(
            command,
            cwd=str(app_root),
            input="\n".join([*scenario.prompts, "/exit"]) + "\n",
            capture_output=True,
            text=True,
            env=env,
            check=False,
        )

        trace_dirs = tuple(
            str(path)
            for path in sorted(
                (trace_root / "llm").glob("*/*"),
                key=lambda item: item.name,
            )
            if path.is_dir()
        )
        selected_trace = trace_dirs[-1] if trace_dirs else ""
        grade = (
            grade_phase_trace(selected_trace, expectation=scenario.expectation)
            if selected_trace
            else PhaseTraceGrade(
                trace_dir="",
                call_count=0,
                purposes=(),
                first_decide_mode=None,
                issues=(),
            )
        )
        stdout = proc.stdout or ""
        stderr = proc.stderr or ""
        if not selected_trace:
            grade = PhaseTraceGrade(
                trace_dir="",
                call_count=0,
                purposes=(),
                first_decide_mode=None,
                issues=(),
            )
            grade_dict = grade.to_dict()
            grade_dict["issues"].append(
                {
                    "code": "missing_trace_dir",
                    "message": "live scenario did not emit any trace directory",
                    "inference_step": None,
                    "purpose": "",
                }
            )
        else:
            grade_dict = grade.to_dict()
        if proc.returncode != 0:
            grade_dict["issues"].append(
                {
                    "code": "chat_command_failed",
                    "message": f"chat command exited with code {proc.returncode}",
                    "inference_step": None,
                    "purpose": "",
                }
            )

        passed = _scenario_passed(
            grade=grade,
            expected_issue_codes=scenario.expected_issue_codes,
            stdout=stdout,
            required_stdout_substrings=scenario.required_stdout_substrings,
            extra_issue_codes={
                str(item["code"])
                for item in grade_dict.get("issues", [])
                if isinstance(item, dict) and str(item.get("code", "")).strip()
            }
            - {item.code for item in grade.issues},
        )
        return PhaseContractScenarioResult(
            scenario_id=scenario.scenario_id,
            passed=passed,
            mode=scenario.mode,
            trace_dirs=trace_dirs,
            selected_trace_dir=selected_trace,
            grade=grade_dict,
            stdout=stdout,
            stderr=stderr,
            expected_issue_codes=scenario.expected_issue_codes,
            required_stdout_substrings=scenario.required_stdout_substrings,
        )


def results_to_json(results: Sequence[PhaseContractScenarioResult]) -> dict[str, Any]:
    payload = [item.to_dict() for item in results]
    return {
        "results": payload,
        "passed": sum(1 for item in results if item.passed),
        "failed": sum(1 for item in results if not item.passed),
    }


def dump_results_json(results: Sequence[PhaseContractScenarioResult]) -> str:
    return json.dumps(results_to_json(results), indent=2, sort_keys=True)


def _scenario_passed(
    *,
    grade: PhaseTraceGrade,
    expected_issue_codes: Sequence[str],
    stdout: str,
    required_stdout_substrings: Sequence[str],
    extra_issue_codes: set[str] | None = None,
) -> bool:
    actual_codes = {item.code for item in grade.issues}
    actual_codes.update(extra_issue_codes or set())
    required_stdout_ok = all(token in stdout for token in required_stdout_substrings)
    if expected_issue_codes:
        return set(expected_issue_codes) == actual_codes and required_stdout_ok
    return not actual_codes and required_stdout_ok
