from __future__ import annotations

import json
import re
import time
from collections.abc import Mapping

import pytest

from tests.helpers.live_cli_chat_alibaba import (
    artifact_dir,
    extract_assistant_messages,
    extract_all_debug_payloads,
    extract_last_debug_payload,
    framework_root,
    parse_tool_results,
    require_live_flag,
    run_cli_session,
    session_outbound_debug_payloads,
)
from tests.helpers.live_e2e_profiles import resolve_live_config_path

pytestmark = pytest.mark.e2e


_OFFICIAL_CONFIG = resolve_live_config_path(
    "per-agent-minimax-official.json",
    framework_root(),
)

_SEARCH_PROMPT = 'tool web.search {"query":"pipx official documentation pypa"}'
_UV_FETCH_PROMPT = (
    'tool web.fetch {"url":"https://docs.astral.sh/uv/getting-started/installation/"}'
)
_PIPX_FETCH_PROMPT = 'tool web.fetch {"url":"https://pipx.pypa.io/"}'
_SYNTHESIS_PROMPT = (
    "Using the tool results already produced in this session, compare uv versus "
    "pipx using official sources. Return exactly three sections titled PLAN, "
    "TABLE, and UNCERTAINTIES. In TABLE, compare install model, environment "
    "behavior, and app/script execution. End the answer with this exact line: "
    '<finalization_status>{"status":"final_answer","blocking_reason":"",'
    '"remaining_work":"","reasoning":""}</finalization_status>'
)
_PROMPT = (
    f"{_SEARCH_PROMPT}\n"
    "/debug\n"
    f"{_UV_FETCH_PROMPT}\n"
    "/debug\n"
    f"{_PIPX_FETCH_PROMPT}\n"
    "/debug\n"
    f"{_SYNTHESIS_PROMPT}"
)


def _coerce_finalization_status(value: object) -> dict[str, object] | None:
    if isinstance(value, Mapping):
        return dict(value)
    if isinstance(value, str):
        raw = value.strip()
        if not raw:
            return None
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            return None
        if isinstance(parsed, dict):
            return dict(parsed)
    return None


def _extract_finalization_status_from_body(body: str) -> dict[str, object] | None:
    match = re.search(
        r"<finalization_status>\s*(\{.*?\})\s*</finalization_status>",
        str(body or ""),
        flags=re.DOTALL,
    )
    if not match:
        return None
    return _coerce_finalization_status(match.group(1))


def _contains_section_heading(body: str, title: str) -> bool:
    normalized = str(title or "").strip().upper()
    if not normalized:
        return False
    patterns = (
        rf"(?mi)^\s*#{{1,6}}\s+{re.escape(normalized)}\s*$",
        rf"(?mi)^\s*#{{1,6}}\s+{re.escape(normalized)}(?:\s+[-—:]\s+.*)?$",
        rf"(?mi)^\s*#{{1,6}}\s+{re.escape(normalized)}(?::)(?:\s+\S.*)?$",
        rf"(?mi)^\s*##\s+{re.escape(normalized)}\s*$",
        rf"(?mi)^\s*\*\*{re.escape(normalized)}\*\*\s*$",
        rf"(?mi)^\s*\*\*{re.escape(normalized)}(?::)?\*\*:?(?:\s+\S.*)?$",
        rf"(?mi)^\s*{re.escape(normalized)}\s*$",
    )
    return any(re.search(pattern, body) for pattern in patterns)


def _tool_evidence_failure(
    *,
    tool_execution_count: int,
    tool_results: list[dict[str, object]],
    metadata: dict[str, object],
    transcript_path: object,
) -> str | None:
    if tool_execution_count < 3:
        return (
            "expected at least three tool-backed evidence steps for the OMCTI probe\n"
            f"metadata={json.dumps(metadata, indent=2, sort_keys=True)}\n"
            f"transcript={transcript_path}"
        )
    if len(tool_results) < 3:
        return (
            "expected structured tool_results for the OMCTI probe\n"
            f"metadata={json.dumps(metadata, indent=2, sort_keys=True)}\n"
            f"transcript={transcript_path}"
        )
    return None


@pytest.mark.parametrize(
    ("body", "title"),
    [
        ("# PLAN\nbody", "PLAN"),
        ("## TABLE\nbody", "TABLE"),
        ("**UNCERTAINTIES**\nbody", "UNCERTAINTIES"),
        ("**PLAN** ✓ (search + 2 fetches all succeeded)\nbody", "PLAN"),
        ("**PLAN:**\nbody", "PLAN"),
        ("### TABLE — uv vs. pipx Comparison\nbody", "TABLE"),
        ("### TABLE - uv vs. pipx Comparison\nbody", "TABLE"),
        ("## TABLE: uv vs. pipx Comparison\nbody", "TABLE"),
        ("PLAN\nbody", "PLAN"),
    ],
)
def test_contains_section_heading_accepts_common_heading_forms(
    body: str, title: str
) -> None:
    assert _contains_section_heading(body, title)


def test_extract_finalization_status_accepts_prompt_level_contract() -> None:
    body = (
        "Answer body.\n"
        '<finalization_status>{"status": "final_answer"}</finalization_status>'
    )

    assert _extract_finalization_status_from_body(body) == {"status": "final_answer"}


def test_complex_task_prompt_uses_structured_tool_invocations() -> None:
    assert _SEARCH_PROMPT in _PROMPT
    assert _UV_FETCH_PROMPT in _PROMPT
    assert _PIPX_FETCH_PROMPT in _PROMPT
    assert "<finalization_status>" in _PROMPT
    assert _PROMPT.count("/debug") == 3


@pytest.mark.e2e
@pytest.mark.timeout(420)
def test_live_minimax_m2_7_complex_task_integrity() -> None:
    require_live_flag()
    if not _OFFICIAL_CONFIG.exists():
        pytest.skip(f"missing config file: {_OFFICIAL_CONFIG}")

    run_id = f"omcti-complex-{int(time.time())}"
    last_failure_diag = ""
    for attempt in (1, 2):
        result = run_cli_session(
            session_id_prefix=run_id,
            user_input=f"{_PROMPT}\n/debug\n/exit\n",
            agent_id="minimax-m2-7",
            config_path=_OFFICIAL_CONFIG,
            data_root_override=artifact_dir() / "data-roots" / run_id,
            matrix_type="skill_dense",
            attempt_suffix=f"attempt-{attempt}",
        )

        transcript = result.transcript
        debug_payloads = session_outbound_debug_payloads(
            data_root=result.data_root,
            agent_id="minimax-m2-7",
            session_id=result.session_id,
        ) or extract_all_debug_payloads(transcript)
        debug_payload = (
            debug_payloads[-1]
            if debug_payloads
            else extract_last_debug_payload(transcript)
        )
        last_turn = debug_payload.get("last_turn")
        assert isinstance(last_turn, dict), (
            f"missing last_turn debug payload\ntranscript={result.transcript_path}"
        )
        metadata = last_turn.get("metadata")
        assert isinstance(metadata, dict), (
            "missing metadata in last_turn debug payload\n"
            f"transcript={result.transcript_path}"
        )

        assistant_messages = extract_assistant_messages(
            transcript=transcript,
            session_id=result.session_id,
            agent_id="minimax-m2-7",
            include_policy_confirmation_prompt=False,
        )
        assert assistant_messages, (
            f"missing assistant message content\ntranscript={result.transcript_path}"
        )

        tool_results: list[dict[str, object]] = []
        tool_execution_count = 0
        for payload in debug_payloads:
            payload_turn = payload.get("last_turn")
            if not isinstance(payload_turn, dict):
                continue
            payload_metadata = payload_turn.get("metadata")
            if not isinstance(payload_metadata, dict):
                continue
            tool_results.extend(
                parse_tool_results(payload_metadata.get("tool_results"))
            )
            tool_execution_count += int(
                str(payload_metadata.get("tool_execution_count", "0")).strip() or "0"
            )
        finalization_status = _coerce_finalization_status(
            metadata.get("adaptive.finalization_status")
        ) or _coerce_finalization_status(metadata.get("finalization_status"))
        body_preview = str(last_turn.get("body_preview", "") or "").strip()
        assert body_preview, (
            "expected a user-visible answer body for the final OMCTI turn\n"
            f"metadata={json.dumps(metadata, indent=2, sort_keys=True)}\n"
            f"transcript={result.transcript_path}"
        )
        assistant_body = str(last_turn.get("body", "") or body_preview).strip()
        body_finalization_status = _extract_finalization_status_from_body(
            assistant_body
        )
        if finalization_status is None:
            finalization_status = body_finalization_status

        tool_failure = _tool_evidence_failure(
            tool_execution_count=tool_execution_count,
            tool_results=tool_results,
            metadata=metadata,
            transcript_path=result.transcript_path,
        )
        if tool_failure is not None:
            last_failure_diag = tool_failure
            if attempt == 1:
                continue
            raise AssertionError(tool_failure)

        if isinstance(finalization_status, dict):
            assert str(finalization_status.get("status", "")).strip() in {
                "final_answer",
                "incomplete",
                "blocked",
            }, (
                "unexpected finalization status\n"
                f"metadata={json.dumps(metadata, indent=2, sort_keys=True)}\n"
                f"transcript={result.transcript_path}"
            )
        missing_headings = [
            heading
            for heading in ("PLAN", "TABLE", "UNCERTAINTIES")
            if not _contains_section_heading(assistant_body, heading)
        ]
        if missing_headings:
            last_failure_diag = (
                "expected final OMCTI answer section heading(s): "
                f"{', '.join(missing_headings)}\n"
                f"transcript={result.transcript_path}\n"
                f"assistant_body={assistant_body}"
            )
            if attempt == 1:
                continue
            raise AssertionError(last_failure_diag)
        for heading in ("PLAN", "TABLE", "UNCERTAINTIES"):
            assert _contains_section_heading(assistant_body, heading), (
                f"expected final OMCTI answer to include a {heading} section heading\n"
                f"transcript={result.transcript_path}"
            )
        return

        last_failure_diag = (
            "expected tool-backed evidence and PLAN/TABLE/UNCERTAINTIES sections\n"
            f"attempt={attempt}\n"
            f"metadata={json.dumps(metadata, indent=2, sort_keys=True)}\n"
            f"transcript={result.transcript_path}\n"
            f"assistant_body={assistant_body}"
        )
        if attempt == 1 and (tool_execution_count == 0 or finalization_status is None):
            continue
        break

    raise AssertionError(last_failure_diag)
