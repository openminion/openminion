"""Pure evaluator for verification-signal closure facts."""

import re
from typing import Any, Iterable

from ...constants import (
    BRAIN_DISPOSITION_CLOSE,
    BRAIN_DISPOSITION_CONTINUE,
)
from ...schemas.closure import (
    ClosureJudgment,
    VerificationFact,
)
from openminion.modules.tool import (
    blast_radius_requires_verification,
    tool_result_blast_radius,
)
from openminion.modules.tool.errors import ToolRuntimeError
from ..budget.continuation import has_continuation_budget

_TEST_RUNNER_PATTERNS: tuple[str, ...] = (
    "pytest",
    "python -m pytest",
    "make test",
    "make check",
    "npm test",
    "npm run test",
    "yarn test",
    "yarn run test",
    "cargo test",
    "go test",
    "rspec",
)

_TYPE_CHECK_PATTERNS: tuple[str, ...] = (
    "mypy",
    "pyright",
    "ruff check",
    "tsc",
    "tsc --noemit",
    "npm run typecheck",
    "yarn typecheck",
)

_BUILD_PATTERNS: tuple[str, ...] = (
    "make build",
    "make all",
    "make install",
    "npm run build",
    "npm build",
    "yarn build",
    "yarn run build",
    "cargo build",
    "go build",
    "python setup.py build",
    "python -m build",
)

_BARE_MAKE_RE = re.compile(r"^\s*make(\s|$)")
VERIFICATION_FAILED_REASON = "verification_failed"


def evaluate_verification(
    *,
    tool_results: list[dict[str, Any]] | tuple[dict[str, Any], ...] | None,
    user_verification_exit_code: int | None = None,
) -> VerificationFact:
    fact = verification_fact_for_results(
        tool_results=tool_results,
        user_verification_exit_code=user_verification_exit_code,
    )
    return fact if fact is not None else VerificationFact()


def verification_fact_for_results(
    *,
    tool_results: list[dict[str, Any]] | tuple[dict[str, Any], ...] | None,
    user_verification_exit_code: int | None = None,
) -> VerificationFact | None:
    results = _normalize_tool_results(tool_results)
    if not results:
        return None
    side_effect_state = _side_effect_state(results)
    if isinstance(side_effect_state, VerificationFact):
        return side_effect_state
    if not side_effect_state:
        return None
    for signal, finder in (
        ("tests", _find_last_test_invocation),
        ("types", _find_last_type_check_invocation),
        ("build", _find_last_build_invocation),
    ):
        match = finder(results)
        if match is not None:
            return _fact_from_result(signal=signal, result=match)
    if user_verification_exit_code is not None:
        ok = int(user_verification_exit_code) == 0
        return VerificationFact(
            signal="user",
            exit_code=int(user_verification_exit_code),
            ok=ok,
            probed_tool="user",
        )
    return VerificationFact()


def is_verification_failed(fact: VerificationFact | None) -> bool:
    if fact is None:
        return False
    if fact.signal == "unavailable" and fact.ok:
        return False
    return not fact.ok


def apply_verification_to_judgment(
    judgment: ClosureJudgment,
    fact: VerificationFact,
    *,
    state: Any,
) -> ClosureJudgment:
    judgment.verification = fact
    if not is_verification_failed(fact):
        return judgment
    if not (judgment.satisfied and judgment.next_action == BRAIN_DISPOSITION_CLOSE):
        return judgment
    if has_continuation_budget(state):
        judgment.satisfied = False
        judgment.next_action = BRAIN_DISPOSITION_CONTINUE
        judgment.final_answer = None
    judgment.reason = (
        f"{judgment.reason}; {VERIFICATION_FAILED_REASON}"
        if judgment.reason
        else VERIFICATION_FAILED_REASON
    )
    return judgment


def _normalize_tool_results(
    raw: Iterable[dict[str, Any]] | None,
) -> list[dict[str, Any]]:
    if not raw:
        return []
    return [item for item in raw if isinstance(item, dict)]


def _side_effect_state(
    results: list[dict[str, Any]],
) -> bool | VerificationFact:
    for result in results:
        if not bool(result.get("ok")):
            continue
        try:
            radius = tool_result_blast_radius(result)
        except ToolRuntimeError:
            return VerificationFact(
                signal="unavailable",
                ok=False,
                probed_tool=str(result.get("tool_name") or "").strip(),
            )
        if radius is not None and blast_radius_requires_verification(radius):
            return True
    return False


def _extract_command_text(result: dict[str, Any]) -> str:
    data = result.get("data")
    if isinstance(data, dict):
        argv = data.get("argv")
        if isinstance(argv, (list, tuple)) and argv:
            return " ".join(str(item) for item in argv).strip().lower()
        for key in ("command", "cmd", "argv_string"):
            value = data.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip().lower()
    argv = result.get("argv")
    if isinstance(argv, (list, tuple)) and argv:
        return " ".join(str(item) for item in argv).strip().lower()
    for key in ("command", "cmd"):
        value = result.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip().lower()
    content = result.get("content")
    if isinstance(content, str) and content.strip():
        return content.strip().lower()
    return ""


def _is_exec_run(result: dict[str, Any]) -> bool:
    return str(result.get("tool_name") or "").strip().lower() == "exec.run"


def _command_matches(command: str, patterns: tuple[str, ...]) -> bool:
    if not command:
        return False
    return any(pattern in command for pattern in patterns)


def _find_last_test_invocation(
    results: list[dict[str, Any]],
) -> dict[str, Any] | None:
    return _find_last_matching(results, _TEST_RUNNER_PATTERNS)


def _find_last_type_check_invocation(
    results: list[dict[str, Any]],
) -> dict[str, Any] | None:
    return _find_last_matching(results, _TYPE_CHECK_PATTERNS)


def _find_last_build_invocation(
    results: list[dict[str, Any]],
) -> dict[str, Any] | None:
    explicit = _find_last_matching(results, _BUILD_PATTERNS)
    if explicit is not None:
        return explicit
    last: dict[str, Any] | None = None
    for result in results:
        if not _is_exec_run(result):
            continue
        command = _extract_command_text(result)
        if (
            _BARE_MAKE_RE.match(command)
            and "test" not in command
            and "check" not in command
        ):
            last = result
    return last


def _find_last_matching(
    results: list[dict[str, Any]],
    patterns: tuple[str, ...],
) -> dict[str, Any] | None:
    last: dict[str, Any] | None = None
    for result in results:
        if not _is_exec_run(result):
            continue
        command = _extract_command_text(result)
        if _command_matches(command, patterns):
            last = result
    return last


def _exit_code(result: dict[str, Any]) -> int | None:
    data = result.get("data")
    if isinstance(data, dict):
        value = data.get("exit_code")
        if isinstance(value, bool):
            return int(value)
        if isinstance(value, int):
            return value
        if isinstance(value, str) and value.strip().lstrip("-").isdigit():
            return int(value)
    top = result.get("exit_code")
    if isinstance(top, int) and not isinstance(top, bool):
        return top
    return None


def _fact_from_result(*, signal: str, result: dict[str, Any]) -> VerificationFact:
    exit_code = _exit_code(result)
    ok = bool(result.get("ok"))
    if exit_code is not None:
        ok = exit_code == 0
    return VerificationFact(
        signal=signal,  # type: ignore[arg-type]
        exit_code=exit_code,
        ok=ok,
        probed_tool=str(result.get("tool_name") or "").strip(),
    )


__all__ = [
    "VERIFICATION_FAILED_REASON",
    "apply_verification_to_judgment",
    "evaluate_verification",
    "is_verification_failed",
    "verification_fact_for_results",
]
