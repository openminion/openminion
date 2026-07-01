from __future__ import annotations

import ast
import importlib
from pathlib import Path

import pytest

from openminion.cli.status import (
    PhaseStatusController,
    PhaseStatusViewModel,
    build_signature,
    format_elapsed_time,
    format_primary_status_text,
    status_from_payload,
)
from openminion.modules.brain.diagnostics.status import PhaseStatus


# ── Signature dedup parity ────────────────────────────────────────────────────


def test_signature_fields_match_historical_chat_cli_signature() -> None:
    status = PhaseStatus(
        trace_id="trace-sig",
        status_key="planning",
        label="Planning...",
        mode_state="active",
        mode_label="reviewing step 1",
        step_index=1,
        step_total=3,
        mode_step_index=1,
        mode_step_total=2,
        llm_call_count=2,
        llm_call_limit=12,
        total_input_tokens_used=500,
        total_output_tokens_used=200,
        total_tokens_used=700,
        token_usage_estimated=False,
        tool_name="location.get",
        progress_phase="composing",
        detail_text="thinking...",
        terminal=False,
    )
    sig = build_signature(status)
    # Every repaint-worthy field must be in the signature; any drop is a
    # regression.
    expected_values = (
        status.status_key,
        status.label,
        status.mode,
        status.mode_state,
        status.mode_label,
        status.step_index,
        status.step_total,
        status.mode_step_index,
        status.mode_step_total,
        status.llm_call_count,
        status.llm_call_limit,
        status.total_input_tokens_used,
        status.total_output_tokens_used,
        status.total_tokens_used,
        status.token_usage_estimated,
        status.tool_name,
        status.progress_phase,
        status.detail_text,
        status.terminal,
    )
    assert sig == expected_values


def test_signature_flips_when_mode_label_changes() -> None:
    status_a = PhaseStatus(
        trace_id="x",
        status_key="planning",
        label="Planning...",
        mode_label="reviewing step 1",
    )
    status_b = PhaseStatus(
        trace_id="x",
        status_key="planning",
        label="Planning...",
        mode_label="reviewing step 2",
    )
    assert build_signature(status_a) != build_signature(status_b)


# ── Controller dedup + elapsed ───────────────────────────────────────────────


def test_controller_update_dedupes_identical_payloads() -> None:
    controller = PhaseStatusController()
    controller.start_turn()
    status = PhaseStatus(trace_id="t", status_key="working", label="Working...")
    first = controller.update(status)
    second = controller.update(status)
    assert first is not None
    assert second is None


def test_controller_update_repaints_on_mode_label_change() -> None:
    controller = PhaseStatusController()
    controller.start_turn()
    first = controller.update(
        PhaseStatus(
            trace_id="t",
            status_key="planning",
            label="Planning...",
            mode_label="step 1",
        )
    )
    second = controller.update(
        PhaseStatus(
            trace_id="t",
            status_key="planning",
            label="Planning...",
            mode_label="step 2",
        )
    )
    assert first is not None
    assert second is not None
    assert first.signature != second.signature


def test_controller_drops_hidden_progress_payloads_without_consuming_dedup() -> None:
    controller = PhaseStatusController()
    controller.start_turn()

    hidden = controller.update(
        {
            "trace_id": "hidden",
            "status_key": "working",
            "label": "raw provider thought",
            "visibility": "hidden",
        }
    )
    visible = controller.update(
        {"trace_id": "shown", "status_key": "working", "label": "Working..."}
    )

    assert hidden is None
    assert visible is not None
    assert visible.primary_text == "Working..."


def test_controller_elapsed_seconds_tracks_clock() -> None:
    values = iter([10.0, 14.5, 20.0])
    controller = PhaseStatusController(clock=lambda: next(values))
    assert controller.elapsed_seconds() is None
    controller.start_turn()  # consumes 10.0
    assert controller.elapsed_seconds() == pytest.approx(4.5)  # consumes 14.5
    assert format_elapsed_time(controller.elapsed_seconds()) == "10s"  # consumes 20.0
    controller.end_turn()
    assert controller.elapsed_seconds() is None


def test_controller_view_model_terminal_detection() -> None:
    controller = PhaseStatusController()
    controller.start_turn()
    view = controller.update(
        PhaseStatus(
            trace_id="t",
            status_key="completed",
            label="Done",
        )
    )
    assert view is not None
    assert view.terminal is True
    assert view.show_spinner is False


def test_controller_view_model_waiting_for_user_still_shows_spinner() -> None:
    controller = PhaseStatusController()
    controller.start_turn()
    view = controller.update(
        PhaseStatus(
            trace_id="t",
            status_key="waiting_for_user",
            label="Awaiting input",
        )
    )
    assert view is not None
    assert view.show_spinner is True


def test_controller_view_model_contains_primary_text_from_shared_formatter() -> None:
    controller = PhaseStatusController(fallback_label="thinking…")
    controller.start_turn()
    status = PhaseStatus(
        trace_id="t",
        status_key="planning",
        label="Planning...",
        mode_label="step 1",
    )
    view = controller.update(status)
    expected = format_primary_status_text(status, fallback_label="thinking…")
    assert view is not None
    assert view.primary_text == expected


# ── status_from_payload round-trip ───────────────────────────────────────────


def test_status_from_payload_accepts_dict_mapping() -> None:
    status = status_from_payload(
        {"trace_id": "trace", "status_key": "working", "label": "Working..."}
    )
    assert isinstance(status, PhaseStatus)
    assert status.trace_id == "trace"
    assert status.status_key == "working"


def test_status_from_payload_passes_through_phase_status() -> None:
    ps = PhaseStatus(trace_id="t", status_key="working", label="Working...")
    assert status_from_payload(ps) is ps


# ── Cross-shell adoption proof ────────────────────────────────────────────────


def test_chat_cli_phase_status_display_uses_shared_controller() -> None:
    import inspect

    from openminion.cli.chat.ui import PhaseStatusDisplay

    src = inspect.getsource(PhaseStatusDisplay)
    assert "PhaseStatusController" in src, (
        "chat CLI PhaseStatusDisplay no longer uses the shared status "
        "controller; signature/elapsed dedup is back to shell-local."
    )
    assert "_last_signature" not in src, (
        "chat CLI should not maintain a shell-local signature cache; "
        "dedup must go through the shared controller."
    )


def test_dashboard_chat_uses_shared_status_controller() -> None:
    import inspect

    from openminion.cli.tui.tabs.chat import ChatTab

    src = inspect.getsource(ChatTab)
    assert "PhaseStatusController" in src, (
        "Dashboard ChatTab must consume `PhaseStatusController`; "
        "a thin label-only path has regressed the shared-owner contract."
    )


def test_focus_screen_uses_shared_status_controller() -> None:
    import inspect

    from openminion.cli.tui.focus.screen import FocusScreen

    src = inspect.getsource(FocusScreen)
    assert "PhaseStatusController" in src, (
        "Focus `FocusScreen` must consume `PhaseStatusController`; the "
        "shared status owner is no longer being invoked."
    )
    assert "_status_controller" in src, (
        "Focus shell must hold a per-turn `_status_controller` so dedup "
        "and elapsed tracking match chat CLI."
    )


# ── Ownership direction: shared owner does not import shell modules ──────────


_SHARED_STATUS_MODULES = [
    "openminion/src/openminion/cli/status/__init__.py",
    "openminion/src/openminion/cli/status/models.py",
    "openminion/src/openminion/cli/status/controller.py",
    "openminion/src/openminion/cli/status/formatting.py",
]

_FORBIDDEN_PREFIXES = (
    "openminion.cli.chat.",
    "openminion.cli.tui.",
)


def _collect_import_names(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(), filename=str(path))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module)
        elif isinstance(node, ast.Import):
            for alias in node.names:
                names.add(alias.name)
    return names


@pytest.fixture(scope="module")
def _repo_root() -> Path:
    return Path(__file__).resolve().parents[3]


@pytest.mark.parametrize("rel_path", _SHARED_STATUS_MODULES)
def test_shared_status_owner_does_not_import_shell_modules(
    _repo_root: Path, rel_path: str
) -> None:
    path = _repo_root / rel_path
    imports = _collect_import_names(path)
    bad = [name for name in imports if name.startswith(_FORBIDDEN_PREFIXES)]
    assert not bad, (
        f"{rel_path} imports shell-local module(s): {bad}. "
        "Shared CLI status owners must not reach into cli.chat or cli.tui."
    )


def test_cli_status_package_reexports() -> None:
    mod = importlib.import_module("openminion.cli.status")
    expected = {
        "DEFAULT_FALLBACK_LABEL",
        "PhaseStatusController",
        "PhaseStatusSignature",
        "PhaseStatusViewModel",
        "build_signature",
        "format_elapsed_time",
        "format_primary_status_text",
        "status_from_payload",
    }
    missing = expected - set(dir(mod))
    assert not missing, f"cli.status package missing exports: {missing}"


# ── Cross-shell parity fixture ────────────────────────────────────────────────


_PARITY_FIXTURES = [
    PhaseStatus(
        trace_id="mode-change",
        status_key="planning",
        label="Planning steps...",
        mode_label="reviewing step 1/3",
    ),
    PhaseStatus(
        trace_id="tokens",
        status_key="working",
        label="Working...",
        llm_call_count=2,
        llm_call_limit=12,
        total_tokens_used=1500,
    ),
    PhaseStatus(
        trace_id="tool-in-progress",
        status_key="executing",
        label="Executing tool...",
        tool_name="exec.run",
        progress_phase="running",
    ),
    PhaseStatus(
        trace_id="terminal",
        status_key="completed",
        label="Turn complete",
        terminal=True,
    ),
]


@pytest.mark.parametrize("status", _PARITY_FIXTURES, ids=lambda s: s.trace_id)
def test_view_model_matches_shared_formatter_across_all_shells(
    status: PhaseStatus,
) -> None:
    controller = PhaseStatusController()
    controller.start_turn()
    view = controller.update(status)
    assert isinstance(view, PhaseStatusViewModel)
    expected_primary = format_primary_status_text(status)
    assert view.primary_text == expected_primary
    # Terminal status keys must set terminal=True
    if status.status_key in {"completed", "error"} or status.terminal:
        assert view.terminal is True
    else:
        assert view.terminal is False
