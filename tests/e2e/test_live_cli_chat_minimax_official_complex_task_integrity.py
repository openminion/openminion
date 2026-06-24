from __future__ import annotations

import json
import re
import time
from collections.abc import Mapping

import pytest

from tests.helpers.live_cli_chat_alibaba import (
    artifact_dir,
    extract_assistant_messages,
    extract_last_debug_payload,
    framework_root,
    parse_tool_results,
    require_live_flag,
    run_cli_session,
)
from tests.helpers.live_e2e_profiles import resolve_live_config_path

pytestmark = pytest.mark.e2e


_OFFICIAL_CONFIG = resolve_live_config_path(
    "per-agent-minimax-official.json",
    framework_root(),
)

_PROMPT = (
    "Break this into a short three-step plan and then carry it out in the same "
    "turn: compare uv versus pipx using official sources. Use exactly one "
    "web.search for the natural-language query `pipx official documentation "
    "pypa`, then web.fetch `https://docs.astral.sh/uv/getting-started/installation/`, "
    "then web.fetch either `https://pipx.pypa.io/` or `https://github.com/pypa/pipx`. "
    "If the first pipx fetch fails, recover with the other official pipx URL. "
    "Return exactly three sections titled PLAN, TABLE, and UNCERTAINTIES. In "
    "TABLE, compare install model, environment behavior, and app/script "
    "execution. Append `<finalization_status>{...}</finalization_status>` after "
    "the final user-facing answer with status=`final_answer`, `incomplete`, or "
    "`blocked`."
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
        debug_payload = extract_last_debug_payload(transcript)
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

        tool_results = parse_tool_results(metadata.get("tool_results"))
        tool_execution_count = int(
            str(metadata.get("tool_execution_count", "0")).strip() or "0"
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
        assistant_body = assistant_messages[-1]
        body_finalization_status = _extract_finalization_status_from_body(
            assistant_body
        )
        if finalization_status is None:
            finalization_status = body_finalization_status

        if isinstance(finalization_status, dict):
            assert tool_execution_count >= 3, (
                "expected at least three tool-backed evidence steps for the OMCTI probe\n"
                f"metadata={json.dumps(metadata, indent=2, sort_keys=True)}\n"
                f"transcript={result.transcript_path}"
            )
            assert len(tool_results) >= 3, (
                "expected structured tool_results for the OMCTI probe\n"
                f"metadata={json.dumps(metadata, indent=2, sort_keys=True)}\n"
                f"transcript={result.transcript_path}"
            )
            assert str(finalization_status.get("status", "")).strip() in {
                "final_answer",
                "incomplete",
                "blocked",
            }, (
                "unexpected finalization status\n"
                f"metadata={json.dumps(metadata, indent=2, sort_keys=True)}\n"
                f"transcript={result.transcript_path}"
            )
            for heading in ("PLAN", "TABLE", "UNCERTAINTIES"):
                assert _contains_section_heading(assistant_body, heading), (
                    f"expected final OMCTI answer to include a {heading} section heading\n"
                    f"transcript={result.transcript_path}"
                )
            return

        if "required typed finalization_status contract" in assistant_body:
            return

        last_failure_diag = (
            "expected either typed finalization_status metadata or the truthful "
            "contract-missing fail-closed outcome\n"
            f"attempt={attempt}\n"
            f"metadata={json.dumps(metadata, indent=2, sort_keys=True)}\n"
            f"transcript={result.transcript_path}\n"
            f"assistant_body={assistant_body}"
        )
        if attempt == 1 and tool_execution_count == 0:
            continue
        break

    raise AssertionError(last_failure_diag)
