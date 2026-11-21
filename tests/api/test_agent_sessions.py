"""Same-session SDK follow-ups through a real runtime and the echo provider."""

import pytest

from openminion import Agent, AgentRunResult, APIRuntime
from openminion.api.queries.runs import list_runs
from openminion.base.config import OpenMinionConfig, save_config
from tests._csc_fixtures import _csc_install_default_agent


@pytest.fixture
def echo_runtime(tmp_path):
    config = OpenMinionConfig()
    _csc_install_default_agent(config, provider="echo")
    config.runtime.log_level = "ERROR"
    config.storage.path = str(tmp_path / "state.db")
    config_path = tmp_path / "agents.json"
    save_config(config, str(config_path))
    runtime = APIRuntime.from_config_path(
        str(config_path), home_root=tmp_path, data_root=tmp_path / "data"
    )
    try:
        yield runtime
    finally:
        runtime.close()


def _session_run_ids(runtime: APIRuntime, session_id: str) -> set[str]:
    runs = list_runs(None, session_id=session_id, runtime=runtime)["runs"]
    return {run["run_id"] for run in runs}


def _assert_new_turns(
    results: list[AgentRunResult[str]], messages: tuple[str, ...]
) -> None:
    for result, message in zip(results, messages, strict=True):
        assert message in result.text
        assert result.run_state == "completed"
        assert "replayed_response" not in result.metadata
        assert result.metadata["thread_decision_action"] != "replay"
    assert len({result.run_id for result in results}) == len(results)
    assert len({result.metadata["request_id"] for result in results}) == len(results)
    assert len({result.id for result in results}) == len(results)


def test_agent_follow_up_runs_a_new_turn_in_the_same_session(echo_runtime):
    messages = ("first message", "second message", "third message")
    with Agent(runtime=echo_runtime, session_id="sdk-follow-up") as agent:
        results = [agent.run(message) for message in messages]

    _assert_new_turns(results, messages)
    assert {result.session_id for result in results} == {"sdk-follow-up"}
    assert _session_run_ids(echo_runtime, "sdk-follow-up") == {
        result.run_id for result in results
    }


def test_agents_sharing_a_session_each_get_their_own_reply(echo_runtime):
    owner = Agent(runtime=echo_runtime, session_id="sdk-shared")
    guest = Agent(runtime=echo_runtime)

    results = [
        owner.run("owner opens"),
        guest.run("guest joins", session_id="sdk-shared"),
        owner.run("owner follows up"),
    ]

    _assert_new_turns(results, ("owner opens", "guest joins", "owner follows up"))
    assert {result.session_id for result in results} == {"sdk-shared"}
    assert _session_run_ids(echo_runtime, "sdk-shared") == {
        result.run_id for result in results
    }


def test_agent_receives_a_pending_undelivered_reply_once(echo_runtime):
    session_id = "sdk-pending-delivery"
    unacknowledged = {"session_id": session_id, "deliver": False}
    pending = echo_runtime.run_turn(
        payload={"message": "never delivered", **unacknowledged}
    )
    retry = echo_runtime.run_turn(payload={"message": "raw retry", **unacknowledged})

    assert retry["body"] == pending["body"]
    assert retry["metadata"]["replayed_response"] == "true"
    assert retry["metadata"]["thread_decision_reason"] == (
        "undelivered_response_pending"
    )

    with Agent(runtime=echo_runtime, session_id=session_id) as agent:
        replay = agent.run("sdk retry")
        follow_up = agent.run("sdk follow-up")

    assert replay.text == pending["body"]
    assert replay.run_id == pending["run_id"]
    assert replay.metadata["replayed_response"] == "true"
    _assert_new_turns([follow_up], ("sdk follow-up",))
    assert _session_run_ids(echo_runtime, session_id) == {
        pending["run_id"],
        follow_up.run_id,
    }


def test_runtime_idempotency_replays_exact_request_and_rejects_conflict(
    echo_runtime,
):
    payload = {
        "message": "idempotent request",
        "session_id": "sdk-idempotency",
        "idempotency_key": "app-request-1",
        "deliver": False,
    }
    first = echo_runtime.run_turn(payload=payload)
    duplicate = echo_runtime.run_turn(payload=payload)

    assert duplicate["id"] == first["id"]
    assert duplicate["body"] == first["body"]
    with pytest.raises(ValueError, match="idempotency key conflicts"):
        echo_runtime.run_turn(payload={**payload, "message": "different request"})
