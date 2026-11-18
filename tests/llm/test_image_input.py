from io import BytesIO
from types import SimpleNamespace

import pytest
from PIL import Image

from openminion.base.config import (
    AgentProfileConfig,
    OpenMinionConfig,
    build_runtime_config,
)
from openminion.modules.artifact.config import load_config
from openminion.modules.artifact.control import ArtifactCtl
from openminion.modules.artifact.errors import ArtifactCtlError
from openminion.modules.brain.adapters.context.bridges.session import (
    BridgeSessionClient,
)
from openminion.modules.brain.adapters.llm.request import _messages_from_context
from openminion.modules.context.schemas import ContextSegment
from openminion.modules.context.segment.messages import segments_to_messages
from openminion.modules.llm.client_call import (
    latest_prompt_and_history,
    normalized_messages,
    split_system_and_conversation,
)
from openminion.modules.llm.image_input import (
    admit_images,
    finish_image_reading,
    materialize_image_input,
    read_images,
)
from openminion.modules.llm.providers.contracts import ProviderError
from openminion.modules.llm.schemas import (
    ImageContentPart,
    LLMRequest,
    LLMResponse,
    Message,
)
from tests.artifact.utils import make_config


@pytest.fixture
def image_turn(tmp_path):
    config = build_runtime_config(
        OpenMinionConfig(
            default_agent="agent",
            agents={
                "agent": AgentProfileConfig(
                    name="agent",
                    provider="openai",
                    provider_config_overrides={
                        "model": "vision-fixture",
                        "enable_vision_input": True,
                    },
                    image_input={"mode": "native"},
                    model_connections={
                        "vision": {
                            "provider": "openai",
                            "models": ["vision-fixture"],
                            "provider_config_overrides": {"enable_vision_input": True},
                            "image_input": {"vision-fixture": "supported"},
                        }
                    },
                )
            },
        )
    )
    config.providers.openai.api_key = "fixture-only"
    artifacts = load_config(make_config(tmp_path))
    session = SimpleNamespace(
        id="chat", image_generation="generation", image_scope="chat::generation"
    )
    stream = BytesIO()
    Image.new("RGB", (12, 8), "blue").save(stream, format="PNG")
    with ArtifactCtl(artifacts) as ctl:
        image = ctl.ingest_image(
            stream.getvalue(), mime="image/png", session_id=session.image_scope
        )
    record = admit_images(config, artifacts, session=session, image_refs=(image.ref,))
    return config, artifacts, session, record


def test_native_selected_context_preserves_image_only_turn_through_provider_bridge(
    image_turn,
):
    config, artifacts, session, record = image_turn
    context_slice = BridgeSessionClient(None)._to_session_slice(
        session_id=session.id,
        raw={
            "recent_turns": [
                {
                    "turn_id": "image-turn",
                    "role": "user",
                    "text": "",
                    "channel_meta": {"image_input": record.model_dump()},
                },
            ]
        },
    )
    turn = context_slice.recent_turns[0]
    messages = segments_to_messages(
        [
            ContextSegment(
                id=turn.turn_id,
                bucket="recent_window",
                role=turn.role,
                content=turn.content,
                image_input=turn.image_input,
                token_estimate=0,
            )
        ]
    )
    request = LLMRequest(
        messages=_messages_from_context(
            {"messages": [item.model_dump() for item in messages]}
        )
    )
    _, conversation = split_system_and_conversation(normalized_messages(request))
    question, history, _, restored = latest_prompt_and_history(
        conversational=conversation, metadata={}
    )
    assert question == "" and history == [] and restored == record
    parts = materialize_image_input(config, artifacts, restored, question=question)
    assert len(parts) == 1 and isinstance(parts[0], ImageContentPart)
    assert parts[0].refs == record.image_refs


def test_native_history_requires_original_destination_and_live_reference(image_turn):
    config, artifacts, _, record = image_turn
    config.providers.openai.base_url = "https://other.example/v1"
    with pytest.raises(ProviderError, match="different model connection"):
        materialize_image_input(config, artifacts, record, question="What is here?")
    with ArtifactCtl(artifacts) as ctl:
        ctl.revoke_image(record.image_refs[0], session_id="chat::generation")
    with pytest.raises(ArtifactCtlError, match="IMAGE_UNAVAILABLE"):
        materialize_image_input(config, artifacts, record, question="What is here?")


def test_unknown_capability_never_enables_native_input(image_turn):
    config, artifacts, session, record = image_turn
    config.agents["agent"].model_connections["vision"]["image_input"] = {}
    with pytest.raises(ProviderError, match="Declare image support"):
        admit_images(
            config, artifacts, session=session, image_refs=tuple(record.image_refs)
        )


def test_reader_uses_one_tool_free_request_with_original_question_and_selected_images(
    image_turn, monkeypatch
):
    config, artifacts, session, record = image_turn
    record = record.model_copy(update={"mode": "separate"})
    requests = []
    response = LLMResponse(
        ok=True,
        provider="openai",
        model=record.model,
        output_text='[{"image_ref":"'
        + record.image_refs[0]
        + '","visible_text":"","observations":["blue rectangle"],"uncertainties":[]}]',
    )

    def call(request):
        requests.append(request)
        return response

    closed = []
    monkeypatch.setattr(
        "openminion.modules.llm.providers.factory.build_runtime_llm_handle",
        lambda *args: SimpleNamespace(
            name="openai",
            model=record.model,
            client=SimpleNamespace(call_sync=call),
            close=lambda: closed.append(True),
        ),
    )
    result = read_images(
        config,
        artifacts,
        record,
        question="What color?",
        max_output_tokens=200,
        logger=None,
        sessions=SimpleNamespace(get_session=lambda _: session),
        on_response=lambda response: None,
        before_request=lambda request: request,
    )
    finished = finish_image_reading(record, result)
    assert len(requests) == 1 and closed == [True]
    assert requests[0].tools == [] and requests[0].tool_choice == "none"
    assert requests[0].max_output_tokens == 200
    assert requests[0].messages[-1].content == "What color?"
    assert requests[0].messages[-1].image_input == record
    parts = materialize_image_input(config, artifacts, finished, question="What color?")
    assert all(part.type == "text" for part in parts)
    assert "blue rectangle" in parts[-1].text
    assert "Untrusted" in parts[-1].text


@pytest.mark.parametrize(
    "result",
    [
        "not json",
        "[]",
        '[{"image_ref":"other","visible_text":"","observations":[],"uncertainties":[]}]',
    ],
)
def test_reader_rejects_invalid_or_incomplete_result_without_repair(image_turn, result):
    _, _, _, record = image_turn
    with pytest.raises(ProviderError, match="Image reader"):
        finish_image_reading(
            record,
            LLMResponse(
                ok=True, provider="openai", model=record.model, output_text=result
            ),
        )


def test_identical_text_with_different_images_is_not_deduplicated(image_turn):
    _, _, _, record = image_turn
    request = LLMRequest(
        messages=[
            Message(role="user", content="inspect", image_input=record),
            Message(role="user", content="inspect"),
        ]
    )
    _, conversation = split_system_and_conversation(normalized_messages(request))
    _, history, _, current = latest_prompt_and_history(
        conversational=conversation, metadata={}
    )
    assert current is None
    assert len(history) == 1 and history[0].image_input == record


@pytest.mark.parametrize("stop", ["revocation", "budget", "cancel"])
def test_reader_rechecks_each_paid_attempt_and_keeps_usage(
    image_turn, monkeypatch, stop
):
    import asyncio
    import logging
    from threading import Event
    from openminion.modules.llm.providers import factory
    from openminion.modules.llm.errors import LLMCtlError
    from openminion.modules.llm.schemas import ResponseError, UsageInfo
    from openminion.modules.runtime import sync

    config, artifacts, session, record = image_turn
    record = record.model_copy(update={"mode": "separate"})
    original_build = factory.build_runtime_llm_handle
    calls, spent = [], []
    cancelled = Event()
    token = sync._turn_cancellations.set((cancelled,))

    def build(*args, **kwargs):
        runtime = original_build(*args, **kwargs)
        provider = runtime.client.llmctl.registry.get("openai")

        def complete(request, cfg):
            calls.append(request)
            assert any(
                part.type == "image" for part in request.messages[-1].content_parts
            )
            if stop == "revocation":
                with ArtifactCtl(artifacts) as ctl:
                    ctl.revoke_image(
                        record.image_refs[0], session_id=session.image_scope
                    )
            elif stop == "cancel":
                cancelled.set()
            return LLMResponse(
                ok=False,
                provider="openai",
                model=record.model,
                error=ResponseError(
                    code="RATE_LIMITED", message="Fixture paid failure"
                ),
                usage=UsageInfo(input_tokens=5, output_tokens=5, total_tokens=10),
            )

        monkeypatch.setattr(provider, "complete", complete)
        return runtime

    def before_request(request):
        if stop == "budget" and sum(spent) >= 10:
            raise LLMCtlError("POLICY_DENIED", "Turn budget exhausted")
        return request

    monkeypatch.setattr(factory, "build_runtime_llm_handle", build)
    monkeypatch.setattr(
        "openminion.modules.llm.runtime.flow.time.sleep", lambda _: None
    )
    try:
        try:
            response = read_images(
                config,
                artifacts,
                record,
                question="What color?",
                max_output_tokens=10,
                logger=logging.getLogger("image-test"),
                sessions=SimpleNamespace(get_session=lambda _: session),
                on_response=lambda response: spent.append(
                    response.usage.total_tokens or 0
                ),
                before_request=before_request,
            )
        except asyncio.CancelledError:
            assert stop == "cancel"
        except ArtifactCtlError:
            assert stop == "revocation"
        else:
            assert stop == "budget"
            assert not response.ok and response.error.code == "POLICY_DENIED"
        assert len(calls) == 1
        assert sum(spent) == 10
    finally:
        sync._turn_cancellations.reset(token)


def test_local_context_preserves_selected_images_after_restart(tmp_path, image_turn):
    from openminion.modules.brain.adapters.context.local import LocalContextAdapter
    from openminion.modules.brain.adapters.session.local_store import LocalSessionStore

    _, _, session, record = image_turn
    store = LocalSessionStore(tmp_path / "local")
    for text in ("Compare this image", ""):
        store.append_turn(
            session.id, "user", text, meta={"image_input": record.model_dump()}
        )
    context = LocalContextAdapter(
        session_store=LocalSessionStore(tmp_path / "local")
    ).build(session_id=session.id, agent_id="agent", purpose="test", budget={})
    messages = _messages_from_context(context)
    assert [message.content for message in messages] == ["Compare this image", ""]
    assert all(message.image_input == record for message in messages)
