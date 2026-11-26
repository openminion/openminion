"""Native requests retain their built context identity across calls and retries."""

from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from openminion.modules.brain.execution.loop_contracts import ExecutionContext
from openminion.modules.brain.execution.task_context import execution_messages
from openminion.modules.brain.loop.adaptive.context import _adaptive_loop_metadata
from openminion.modules.brain.loop.tools.runtime import (
    DefaultAdaptiveToolLoopLLMRuntime,
)
from openminion.modules.brain.schemas import ActDecision
from openminion.modules.llm.schemas import Message
from tests.brain.test_context import _Logger
from tests.brain.test_unified_turn_entry import (
    _build_runner,
    _RecordingEntryLLM,
    _SequencedEntryLLM,
    _state,
    _text_response,
)


def test_entry_requests_use_manifest_identity_and_distinct_retry_id(tmp_path):
    llm = _SequencedEntryLLM([_text_response(""), _text_response("Finished.")])
    runner = _build_runner(tmp_path, llm_api=llm)
    context_builds = []

    def build(**kwargs):
        call_id = kwargs["hints"]["_llm_call_id"]
        context_builds.append(call_id)
        return {
            "context_manifest": {"llm_call_id": call_id},
            "messages": [{"role": "user", "content": "Finish this."}],
        }

    runner.context_api = SimpleNamespace(build=build)
    logger = _Logger()
    decision = runner._decide(state=_state(), user_input="Finish this.", logger=logger)

    assert decision.answer == "Finished."
    (manifest_id,) = context_builds
    assert [request.metadata["llm_call_id"] for request in llm.requests] == [
        manifest_id,
        f"{manifest_id}:1",
    ]
    assert all(
        request.metadata["context_llm_call_id"] == manifest_id
        for request in llm.requests
    )
    assert [
        payload["llm_call_id"]
        for name, payload in logger.events
        if name == "context.manifest.created"
    ] == [manifest_id]
    assert llm.requests[0].tools == llm.requests[1].tools
    assert llm.requests[0].messages[-1] == llm.requests[1].messages[-1]


def test_entry_without_manifest_does_not_invent_context_link(tmp_path):
    llm = _RecordingEntryLLM(_text_response("Finished."))
    runner = _build_runner(tmp_path, llm_api=llm)
    runner._decide(state=_state(), user_input="Finish this.", logger=_Logger())

    assert llm.requests[0].metadata["llm_call_id"]
    assert llm.requests[0].metadata.get("context_llm_call_id") is None


def _execution_context(context):
    return ExecutionContext(
        state=_state(),
        decision=ActDecision(),
        user_input="Continue.",
        logger=_Logger(),
        options=SimpleNamespace(profile=None),
        llm_adapter=None,
        command_executor=Mock(),
        _services=SimpleNamespace(
            runner=SimpleNamespace(
                context_api=object(),
                _build_context=Mock(return_value=context),
            )
        ),
    )


def test_shortlisting_does_not_claim_the_full_context():
    ctx = _execution_context({})
    ctx.context_llm_call_id = "built-act-context"
    metadata = _adaptive_loop_metadata(ctx, purpose="tool_shortlist")

    assert "context_llm_call_id" not in metadata
    assert "llm_call_id" not in metadata


@pytest.mark.parametrize("call_path", [True, False])
def test_adaptive_continuations_and_failed_attempts_keep_context_link(call_path):
    context = {
        "context_manifest": {"llm_call_id": "built-act-context"},
        "messages": [
            {"role": "system", "content": "Existing memory."},
            {"role": "user", "content": "Continue."},
        ],
    }
    ctx = _execution_context(context)
    messages = execution_messages(ctx)
    assert [message.model_dump(exclude_defaults=True) for message in messages] == [
        {"role": "system", "content": "Existing memory."},
        {"role": "user", "content": "Continue."},
    ]
    metadata = _adaptive_loop_metadata(ctx, purpose="act")
    recorded = []

    def dispatch(*args, **kwargs):
        recorded.append(args[0].metadata if call_path else kwargs["metadata"])
        if len(recorded) == 2:
            raise TimeoutError("fixture timeout")
        return _text_response("Finished.")

    client = SimpleNamespace(**{"call" if call_path else "complete": dispatch})
    runtime = DefaultAdaptiveToolLoopLLMRuntime(client)
    for attempt in range(3):
        if attempt == 1:
            messages.append(Message(role="assistant", content="Continue working."))
            with pytest.raises(TimeoutError):
                runtime.complete(
                    messages=messages, tools=[], model="test", metadata=metadata
                )
        else:
            runtime.complete(
                messages=messages, tools=[], model="test", metadata=metadata
            )

    assert recorded[0]["llm_call_id"] == "built-act-context"
    assert len({item["llm_call_id"] for item in recorded}) == 3
    assert all(item["context_llm_call_id"] == "built-act-context" for item in recorded)
    assert "llm_call_id" not in metadata
    assert context["context_manifest"] == {"llm_call_id": "built-act-context"}


@pytest.mark.parametrize("autonomous", [False, True])
def test_execution_without_manifest_does_not_reuse_previous_context(autonomous):
    ctx = _execution_context({"context_manifest": {}})
    ctx.context_llm_call_id = "previous-context"
    messages = execution_messages(ctx, autonomous=autonomous)
    metadata = _adaptive_loop_metadata(ctx, purpose="act")
    client = _RecordingEntryLLM(_text_response("Finished."))

    DefaultAdaptiveToolLoopLLMRuntime(client).complete(
        messages=messages, tools=[], model="test", metadata=metadata
    )

    assert ctx.context_llm_call_id is None
    assert "context_llm_call_id" not in client.requests[0].metadata
    assert "llm_call_id" not in client.requests[0].metadata
    if autonomous:
        ctx._services.runner._build_context.assert_not_called()
