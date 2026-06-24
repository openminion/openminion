import io
import json
import unittest
from argparse import Namespace
from contextlib import redirect_stdout
from datetime import timezone
from dataclasses import dataclass

from openminion.cli.commands.agent_check import run_agent_check
from openminion.base.types import AgentResponse, Message


@dataclass
class _AgentConfig:
    name: str = "test-agent"
    default_channel: str = "console"


@dataclass
class _Config:
    agents: dict  # type: ignore[type-arg]
    default_agent: str = ""


class _FakeProvider:
    name = "fake"


class _FakeAgent:
    async def run_turn(self, message: Message) -> AgentResponse:
        return AgentResponse(
            text=f"ok:{message.body}",
            channel=message.channel,
            target=message.target,
            metadata={"provider": "fake", "model": "fake-model"},
        )


class _FakeChannel:
    def __init__(self) -> None:
        self.sent = []

    def send(self, message: Message) -> None:
        stamp = message.timestamp.astimezone(timezone.utc).isoformat()
        self.sent.append((stamp, message.channel, message.target, message.body))


class _FakeChannels:
    def __init__(self, channel: _FakeChannel) -> None:
        self._channel = channel

    def get(self, name: str) -> _FakeChannel:
        if name != "console":
            raise KeyError(f"Unknown channel: {name}")
        return self._channel


class _FakeApp:
    def __init__(self) -> None:
        self.config = _Config(
            agents={"test-agent": _AgentConfig()}, default_agent="test-agent"
        )
        self.provider = _FakeProvider()
        self.agent = _FakeAgent()
        self._channel = _FakeChannel()
        self.channels = _FakeChannels(self._channel)

    def resolve_agent_profile(self, agent_id):  # noqa: ANN001
        selected = (
            str(agent_id or "").strip()
            or self.config.agents[next(iter(self.config.agents.keys()))].name
        )
        provider = "fake"
        if selected == "research":
            provider = "cortensor"
        return type(
            "_FakeProfile",
            (),
            {"name": selected, "default_channel": "console", "provider": provider},
        )()

    def resolve_agent_service(self, agent_id):  # noqa: ANN001
        del agent_id
        return self.agent


class AgentCheckCommandTests(unittest.TestCase):
    def test_agent_check_success_json(self) -> None:
        app = _FakeApp()
        args = Namespace(
            message="ping", target="user", channel="console", deliver=False, json=True
        )

        buf = io.StringIO()
        with redirect_stdout(buf):
            code = run_agent_check(args, app)
        self.assertEqual(code, 0)

        payload = json.loads(buf.getvalue())
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["status"], "healthy")
        self.assertEqual(payload["provider"], "fake")
        self.assertEqual(payload["channel"], "console")

    def test_agent_check_deliver_sends_message(self) -> None:
        app = _FakeApp()
        args = Namespace(
            message="ping", target="user", channel="console", deliver=True, json=False
        )

        with redirect_stdout(io.StringIO()):
            code = run_agent_check(args, app)
        self.assertEqual(code, 0)
        self.assertEqual(len(app._channel.sent), 1)

    def test_agent_check_unknown_channel_fails(self) -> None:
        app = _FakeApp()
        args = Namespace(
            message="ping", target="user", channel="missing", deliver=False, json=True
        )

        buf = io.StringIO()
        with redirect_stdout(buf):
            code = run_agent_check(args, app)
        self.assertEqual(code, 1)

        payload = json.loads(buf.getvalue())
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["status"], "unhealthy")

    def test_agent_check_uses_selected_agent_id(self) -> None:
        app = _FakeApp()
        args = Namespace(
            message="ping",
            target="user",
            channel=None,
            agent_id="research",
            deliver=False,
            json=True,
        )

        buf = io.StringIO()
        with redirect_stdout(buf):
            code = run_agent_check(args, app)
        self.assertEqual(code, 0)

        payload = json.loads(buf.getvalue())
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["agent"], "research")
