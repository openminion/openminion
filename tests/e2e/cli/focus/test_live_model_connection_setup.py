from __future__ import annotations

import shutil
import time
import re

import pytest

from openminion.base.config import load_config, save_config
from openminion.modules.llm.model_connections import legacy_model_connection
from openminion.modules.telemetry.storage import SQLiteTelemetryStore
from tests.e2e.cli.focus.conftest import require_live_focus
from tests.e2e.cli.focus.harness import FocusProbe, FocusScenario

pytestmark = [pytest.mark.e2e, pytest.mark.timeout(480)]


def _reply(session, text: str, marker: str, *, timeout: int = 60) -> str:
    offset = len(session.transcript)
    session.type_line(text)
    return session.wait_for_after(re.escape(marker), offset=offset, timeout=timeout)


def _latest_llm_call(data_root, session_id: str) -> dict[str, str]:
    store = SQLiteTelemetryStore(data_root / "telemetry" / "telemetry.db")
    try:
        events = store.fetch_events()
        completed = [
            event
            for event in events
            if event.event_type == "llm.call.completed"
            and event.session_id.startswith(session_id)
        ]
        assert completed
        return dict(max(completed, key=lambda event: event.timestamp).data)
    finally:
        store.close()


def test_focus_adds_uses_and_resumes_minimax_connection_live(
    focus_probe: FocusProbe,
    tmp_path,
) -> None:
    require_live_focus()
    config_path = tmp_path / "focus-model-setup.json"
    shutil.copy2(focus_probe.config_path, config_path)
    config = load_config(str(config_path))
    profile = config.agents[focus_probe.agent_id]
    legacy = legacy_model_connection(config, profile)
    assert legacy is not None
    legacy_id, legacy_route = legacy
    profile.model_connections[f"{legacy_id}-primary"] = legacy_route
    save_config(config, str(config_path))
    probe = FocusProbe(
        python_bin=focus_probe.python_bin,
        openminion_root=focus_probe.openminion_root,
        framework_root=focus_probe.framework_root,
        data_root=focus_probe.data_root,
        config_path=config_path,
        agent_id=focus_probe.agent_id,
        workdir=focus_probe.workdir,
        session_id=focus_probe.session_id,
        include_project_context=focus_probe.include_project_context,
    )

    with probe.session() as session:
        probe.wait_ready(session)
        _reply(session, "/model setup", "Connection number or id:")
        _reply(session, "minimax", "Model [MiniMax-M2.7]:")
        _reply(
            session,
            "MiniMax-M2.7-highspeed",
            "Save and use this connection now?",
        )
        configured = _reply(session, "y", "not tested yet")
        assert "MiniMax-M2.7-highspeed" in configured

        probe.run_turn(
            session,
            FocusScenario(
                scenario_id="model-setup-live",
                prompt="Reply with exactly: MODEL_SETUP_LIVE_OK",
                expected_markers=("MODEL_SETUP_LIVE_OK",),
                timeout=300,
            ),
        )
        first_call = _latest_llm_call(
            probe.data_root,
            probe.session_id,
        )
        assert first_call["service_vendor"] == "minimax"
        assert first_call["model"] == "MiniMax-M2.7-highspeed"

    saved = load_config(str(config_path))
    assert saved.agents[probe.agent_id].model_connections["minimax"]["models"] == [
        "MiniMax-M2.7-highspeed"
    ]

    time.sleep(0.2)
    with probe.session() as resumed:
        probe.wait_ready(resumed)
        status = probe.run_slash(resumed, "/model", marker="API format")
        assert "current model: MiniMax-M2.7-highspeed" in status
        probe.run_turn(
            resumed,
            FocusScenario(
                scenario_id="model-setup-resume-live",
                prompt="Reply with exactly: MODEL_SETUP_RESUME_OK",
                expected_markers=("MODEL_SETUP_RESUME_OK",),
                timeout=300,
            ),
        )
        resumed_call = _latest_llm_call(
            probe.data_root,
            probe.session_id,
        )
        assert resumed_call["service_vendor"] == "minimax"
        assert resumed_call["model"] == "MiniMax-M2.7-highspeed"
