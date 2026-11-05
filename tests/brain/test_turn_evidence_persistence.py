"""Turn evidence on the working state survives `save_state` and `load_or_init_state`.

Each test runs on the runtime's SQLite session store and on the local session
store.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Iterator

import pytest
from pydantic import ValidationError

from openminion.modules.brain.adapters.session import (
    LocalSessionStore,
    SessctlAdapter,
)
from openminion.modules.brain.config import RunnerOptions
from openminion.modules.brain.execution.dispatch import _same_turn_evidence
from openminion.modules.brain.runner import BrainRunner
from openminion.modules.brain.schemas import ActionResult
from tests.brain.runner_test_support import _profile

SESSION_ID = "s-turn-evidence"
NEW_FIELDS = ("turn_evidence", "turn_evidence_trace_id")
LOOKUP = ActionResult(
    command_id="cmd-lookup-order",
    status="success",
    summary="Order A-104 is ready, code ZEBRA-42.",
    outputs={"order_id": "A-104", "status": "ready", "pickup_code": "ZEBRA-42"},
)


@pytest.fixture(params=["sqlite", "local"])
def session_api(request: pytest.FixtureRequest, tmp_path: Path) -> Iterator[Any]:
    if request.param == "sqlite":
        adapter = SessctlAdapter(tmp_path / "sessions.db")
        yield adapter
        adapter.close()
    else:
        yield LocalSessionStore(tmp_path / "sessions")


def _runner(session_api: Any) -> BrainRunner:
    return BrainRunner(
        profile=_profile(),
        session_api=session_api,
        options=RunnerOptions(metactl_enabled=False),
    )


def _saved(session_api: Any) -> dict[str, Any]:
    return session_api.get_latest_working_state(SESSION_ID)["state_inline"]


def test_a_state_saved_before_turn_evidence_loads_without_evidence(
    session_api: Any,
) -> None:
    runner = _runner(session_api)
    state = runner._load_or_init_state(SESSION_ID)
    state.trace_id = "trace-earlier"
    state.last_result = LOOKUP
    # What a runtime from before the two fields saves: the same dump without them.
    legacy = state.model_dump(mode="json")
    for field in NEW_FIELDS:
        del legacy[field]
    session_api.put_working_state(SESSION_ID, state_inline=legacy)

    loaded = runner._load_or_init_state(SESSION_ID)

    assert loaded.turn_evidence is None
    assert loaded.turn_evidence_trace_id is None
    assert loaded.trace_id == "trace-earlier"
    assert loaded.last_result == LOOKUP
    assert _same_turn_evidence(loaded) is None


def test_saved_turn_evidence_keeps_its_trace_id(session_api: Any) -> None:
    runner = _runner(session_api)
    state = runner._load_or_init_state(SESSION_ID)
    state.trace_id = "trace-1"
    state.turn_evidence_trace_id = "trace-1"
    state.turn_evidence = LOOKUP
    runner._save_state(state)

    saved = _saved(session_api)
    assert saved["turn_evidence_trace_id"] == "trace-1"
    assert saved["turn_evidence"] == LOOKUP.model_dump(mode="json")

    loaded = runner._load_or_init_state(SESSION_ID)

    assert loaded.turn_evidence == LOOKUP
    assert loaded.turn_evidence_trace_id == "trace-1"
    assert _same_turn_evidence(loaded) == LOOKUP
    loaded.trace_id = "trace-2"
    assert _same_turn_evidence(loaded) is None


def test_every_saved_state_writes_both_fields(session_api: Any) -> None:
    runner = _runner(session_api)
    runner._save_state(runner._load_or_init_state(SESSION_ID))

    saved = _saved(session_api)

    assert {field: saved[field] for field in NEW_FIELDS} == {
        "turn_evidence": None,
        "turn_evidence_trace_id": None,
    }


def test_a_field_the_reader_does_not_define_is_still_rejected(
    session_api: Any,
) -> None:
    # A runtime from before the two fields reads this version's state this way.
    runner = _runner(session_api)
    payload = runner._load_or_init_state(SESSION_ID).model_dump(mode="json")
    payload["field_from_a_newer_version"] = None
    session_api.put_working_state(SESSION_ID, state_inline=payload)

    with pytest.raises(ValidationError) as excinfo:
        runner._load_or_init_state(SESSION_ID)

    assert [error["type"] for error in excinfo.value.errors()] == ["extra_forbidden"]
