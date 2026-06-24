import logging
import threading
import time
import unittest
from dataclasses import FrozenInstanceError, replace

from openminion.base.config import OpenMinionConfig
from openminion.base.types import AgentResponse, Message
from openminion.services.runtime.plugins import Plugin, PluginContext
from openminion.services.runtime.plugins import PluginRegistry


class _AppendInboundPlugin(Plugin):
    inbound_hook_mode = "mutating"

    def __init__(self, name: str, suffix: str, events: list[str]) -> None:
        self.name = name
        self._suffix = suffix
        self._events = events

    def on_message(self, message: Message, context: PluginContext) -> Message:
        del context
        self._events.append(self.name)
        return replace(message, body=message.body + self._suffix)


class _SideEffectInboundMutator(Plugin):
    name = "side-effect-mutator"
    inbound_hook_mode = "side_effect"

    def __init__(self, seen_bodies: list[str]) -> None:
        self._seen_bodies = seen_bodies

    def on_message(self, message: Message, context: PluginContext) -> Message:
        del context
        self._seen_bodies.append(message.body)
        message.body = "tampered-in-place"
        return replace(message, body="tampered-return")


class _FailingMutatingInbound(Plugin):
    name = "fail-mutating"
    inbound_hook_mode = "mutating"

    def on_message(self, message: Message, context: PluginContext) -> Message:
        del message, context
        raise RuntimeError("boom")


class _FailingSideEffectInbound(Plugin):
    name = "fail-side-effect"
    inbound_hook_mode = "side_effect"

    def on_message(self, message: Message, context: PluginContext) -> Message:
        del message, context
        raise RuntimeError("boom")


class _SleepSideEffectInbound(Plugin):
    name = "sleep-side-effect"
    inbound_hook_mode = "side_effect"

    def __init__(
        self, delay_seconds: float, thread_ids: set[int], lock: threading.Lock
    ) -> None:
        self._delay_seconds = delay_seconds
        self._thread_ids = thread_ids
        self._lock = lock

    def on_message(self, message: Message, context: PluginContext) -> Message:
        del message, context
        with self._lock:
            self._thread_ids.add(threading.get_ident())
        time.sleep(self._delay_seconds)
        return Message(channel="noop", target="noop", body="noop")


class _AppendOutboundPlugin(Plugin):
    outbound_hook_mode = "mutating"

    def __init__(self, name: str, suffix: str, events: list[str]) -> None:
        self.name = name
        self._suffix = suffix
        self._events = events

    def on_response(
        self,
        response: AgentResponse,
        message: Message,
        context: PluginContext,
    ) -> AgentResponse:
        del message, context
        self._events.append(self.name)
        return replace(response, text=response.text + self._suffix)


class _SideEffectOutboundMutator(Plugin):
    name = "side-effect-outbound-mutator"
    outbound_hook_mode = "side_effect"

    def __init__(self, seen_texts: list[str]) -> None:
        self._seen_texts = seen_texts

    def on_response(
        self,
        response: AgentResponse,
        message: Message,
        context: PluginContext,
    ) -> AgentResponse:
        del message, context
        self._seen_texts.append(response.text)
        try:
            response.text = "tampered-outbound-in-place"
        except FrozenInstanceError:
            pass
        else:  # pragma: no cover - the frozen contract is the assertion.
            raise AssertionError("AgentResponse must reject in-place mutation")
        return replace(response, text="tampered-outbound-return")


class PluginHookRunnerTests(unittest.TestCase):
    def test_agent_response_rejects_post_hoc_attribute_mutation(self) -> None:
        response = AgentResponse(text="reply", channel="console", target="me")

        with self.assertRaises(FrozenInstanceError):
            response.text = "mutated"

        self.assertEqual(response.text, "reply")

    def test_mutating_inbound_hooks_apply_sequentially(self) -> None:
        events: list[str] = []
        seen_bodies: list[str] = []
        registry = PluginRegistry(
            [
                _AppendInboundPlugin("append-a", "-a", events),
                _AppendInboundPlugin("append-b", "-b", events),
                _SideEffectInboundMutator(seen_bodies),
            ]
        )
        context = _plugin_context()
        output = registry.apply_inbound(_message("start"), context)

        self.assertEqual(output.body, "start-a-b")
        self.assertEqual(events, ["append-a", "append-b"])
        self.assertEqual(seen_bodies, ["start-a-b"])

    def test_side_effect_inbound_hooks_execute_in_parallel(self) -> None:
        thread_ids: set[int] = set()
        lock = threading.Lock()
        registry = PluginRegistry(
            [
                _SleepSideEffectInbound(
                    delay_seconds=0.25, thread_ids=thread_ids, lock=lock
                ),
                _SleepSideEffectInbound(
                    delay_seconds=0.25, thread_ids=thread_ids, lock=lock
                ),
            ]
        )
        context = _plugin_context()

        started = time.perf_counter()
        output = registry.apply_inbound(_message("ping"), context)
        elapsed = time.perf_counter() - started

        self.assertEqual(output.body, "ping")
        self.assertLess(elapsed, 0.45)
        self.assertGreaterEqual(len(thread_ids), 2)

    def test_inbound_hook_failures_do_not_abort_pipeline(self) -> None:
        events: list[str] = []
        registry = PluginRegistry(
            [
                _FailingMutatingInbound(),
                _FailingSideEffectInbound(),
                _AppendInboundPlugin("append-ok", "-ok", events),
            ]
        )
        context = _plugin_context()
        output = registry.apply_inbound(_message("start"), context)

        self.assertEqual(output.body, "start-ok")
        self.assertEqual(events, ["append-ok"])

    def test_outbound_mutating_and_side_effect_modes(self) -> None:
        events: list[str] = []
        seen_texts: list[str] = []
        registry = PluginRegistry(
            [
                _AppendOutboundPlugin("out-a", "-a", events),
                _AppendOutboundPlugin("out-b", "-b", events),
                _SideEffectOutboundMutator(seen_texts),
            ]
        )
        context = _plugin_context()
        output = registry.apply_outbound(
            AgentResponse(text="reply", channel="console", target="me"),
            _message("source"),
            context,
        )

        self.assertEqual(output.text, "reply-a-b")
        self.assertEqual(events, ["out-a", "out-b"])
        self.assertEqual(seen_texts, ["reply-a-b"])


def _plugin_context() -> PluginContext:
    logger = logging.getLogger("openminion.tests.plugins")
    logger.handlers = [logging.NullHandler()]
    logger.propagate = False
    return PluginContext(config=OpenMinionConfig(), logger=logger)


def _message(body: str) -> Message:
    return Message(channel="console", target="me", body=body)
