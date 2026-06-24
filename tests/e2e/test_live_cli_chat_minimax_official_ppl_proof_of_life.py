from __future__ import annotations

import time
from pathlib import Path

import pytest

from openminion.api.runtime import APIRuntime
from openminion.modules.brain.diagnostics.status import (
    PhaseStatus,
    format_phase_status_text,
)
from tests.helpers.live_cli_chat_alibaba import (
    artifact_dir,
    framework_root,
    require_live_flag,
)
from tests.helpers.live_e2e_profiles import resolve_live_config_path

pytestmark = pytest.mark.e2e


_AGENT_ID = "minimax-m2-7"
_OFFICIAL_CONFIG = resolve_live_config_path(
    "per-agent-minimax-official.json",
    framework_root(),
)


@pytest.mark.e2e
def test_live_minimax_m2_7_ppl_proof_of_life_emission_chain() -> None:
    require_live_flag()
    if not _OFFICIAL_CONFIG.exists():
        pytest.skip(f"missing config file: {_OFFICIAL_CONFIG}")

    run_id = f"ppl-proof-{int(time.time())}"
    data_root = artifact_dir() / "data-roots" / run_id
    data_root.mkdir(parents=True, exist_ok=True)

    captured: list[PhaseStatus] = []

    def _capture(status: object) -> None:
        if isinstance(status, PhaseStatus):
            captured.append(status)

    runtime = APIRuntime.from_config_path(
        str(_OFFICIAL_CONFIG),
        home_root=Path(framework_root()),
        data_root=data_root,
    )
    try:
        runtime.run_turn(
            payload={
                "message": "What is 2 + 2? Answer with just the number.",
                "agent": _AGENT_ID,
                "session_id": run_id,
            },
            progress_callback=_capture,
        )
    finally:
        runtime.close()

    # --- Sanity: the callback fired at all --------------------------------
    assert captured, (
        "progress_callback received no PhaseStatus emissions during a "
        "live turn. The display would sit on its initial Working... label "
        "for the entire turn. Likely causes: "
        "(a) run_turn path dropped the callback, "
        "(b) brain runner didn't bind _progress_callback, "
        "(c) no emission sites fired."
    )

    # --- Gap D (PPL-07) — bridge prep emits before the LLM call ----------
    # The first emission should carry the bridge-prep detail_text.
    # status_key must be "analyzing" (not "preparing" — schema guardrail).
    prep_emissions = [
        status
        for status in captured
        if status.status_key == "analyzing"
        and str(status.detail_text or "").strip().startswith("Preparing turn")
    ]
    assert prep_emissions, (
        "PPL-07 invariant: bridge prep must emit an 'analyzing' status "
        "with detail_text='Preparing turn...' so the display transitions "
        "off the initial Working... label within the first few hundred "
        "ms of the turn. This emission was missing.\n"
        f"captured (first 5): {[(s.status_key, s.detail_text, s.label) for s in captured[:5]]}"
    )

    # --- Gap A (PPL-02) — DECIDE pre-call emits even without token estimate
    decide_emissions = [
        status
        for status in captured
        if status.source_phase == "DECIDE"
        and status.llm_call_count is not None
        and status.llm_call_count >= 1
    ]
    assert decide_emissions, (
        "PPL-02 invariant: the DECIDE pre-call emit must fire with "
        "turn.llm_call_count >= 1 whenever the hook is callable, "
        "independent of the outbound token estimate. This emission was "
        "missing.\n"
        f"captured source_phase values: {sorted({s.source_phase for s in captured if s.source_phase})}"
    )

    # --- Gap C (PPL-06) — rendered text never bare 'Working...' ----------
    # Any captured status that has progress segments AND a specific
    # phase must compose the label alongside those segments.
    for status in captured:
        if status.status_key in {"completed", "error", "waiting_for_user"}:
            # Terminal/waiting carveout — label-only is correct.
            continue
        if status.llm_call_count is None and not status.total_tokens_used:
            continue  # No progress segments; nothing to compose.
        rendered = format_phase_status_text(status)
        if status.status_key == "working":
            # Generic working — progress-only render is correct.
            continue
        # Specific phase with progress segments: the phase label must
        # appear alongside the progress text, not be replaced by it.
        label = str(status.label or "").strip()
        if not label:
            continue
        assert label in rendered, (
            f"PPL-06 invariant: when a specific phase has progress "
            f"segments, the phase label must appear in the rendered "
            f"text. Got: {rendered!r}, expected label={label!r} to be "
            f"present.\n"
            f"status: status_key={status.status_key}, "
            f"llm_call_count={status.llm_call_count}, "
            f"total_tokens_used={status.total_tokens_used}"
        )
