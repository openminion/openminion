from __future__ import annotations

import io
import json
import sys
import unittest
from argparse import Namespace
from types import SimpleNamespace
from unittest import mock

from openminion.cli.commands.cron import _build_schedule_payload, run_cron


class CronCommandTests(unittest.TestCase):
    def setUp(self) -> None:
        current = sys.modules.get("openminion.cli.commands.cron")
        if current is not None:
            globals()["cron_command"] = current
            globals()["run_cron"] = current.run_cron
            globals()["_build_schedule_payload"] = current._build_schedule_payload

    def _mock_app(self, *, ok: bool = True, data: dict | None = None, error: str = ""):
        result = SimpleNamespace(
            ok=ok,
            data=dict(data or {}),
            error=error,
            tool_name="task.schedule",
        )
        batch = SimpleNamespace(results=[result])
        tools = mock.Mock()
        tools.execute_calls.return_value = batch
        config = SimpleNamespace(
            runtime=SimpleNamespace(
                env={"OPENMINION_DEBUG": "1"},
                tool_selection=SimpleNamespace(),
            )
        )
        return SimpleNamespace(config=config, tools=tools)

    def test_build_schedule_payload_every_ms(self) -> None:
        args = Namespace(every_ms=60_000, cron_expr="", at_iso="", timezone="")
        self.assertEqual(
            _build_schedule_payload(args),
            {"kind": "every", "every_ms": 60_000},
        )

    def test_build_schedule_payload_cron_with_tz(self) -> None:
        args = Namespace(
            every_ms=None, cron_expr="*/5 * * * *", at_iso="", timezone="UTC"
        )
        self.assertEqual(
            _build_schedule_payload(args),
            {"kind": "cron", "expr": "*/5 * * * *", "tz": "UTC"},
        )

    def test_build_schedule_payload_at(self) -> None:
        args = Namespace(
            every_ms=None, cron_expr="", at_iso="2026-03-21T00:00:00Z", timezone=""
        )
        self.assertEqual(
            _build_schedule_payload(args),
            {"kind": "at", "at": "2026-03-21T00:00:00Z"},
        )

    def test_build_schedule_payload_rejects_non_positive_every_ms(self) -> None:
        args = Namespace(every_ms=0, cron_expr="", at_iso="", timezone="")
        with self.assertRaisesRegex(ValueError, "at least 10000"):
            _build_schedule_payload(args)

    def test_run_cron_create_dispatches_task_schedule(self) -> None:
        app = self._mock_app(
            ok=True,
            data={
                "task_id": "job-123",
                "name": "health-check",
                "next_due_at": "2026-03-21T00:00:00Z",
            },
        )
        args = Namespace(
            cron_command="create",
            instruction="check health",
            every_ms=60_000,
            cron_expr="",
            at_iso="",
            timezone="",
            name="health-check",
            agent_id="ops",
            session="cron-cli",
            json=False,
        )

        selector = mock.Mock()
        selector.runtime_binding_policy_metadata.return_value = {
            "runtime_binding_policies": {"enabled": True}
        }
        with (
            mock.patch(
                "openminion.cli.commands.cron.ToolSelectionService",
                return_value=selector,
            ),
            mock.patch("sys.stdout", new_callable=io.StringIO) as stdout,
        ):
            code = run_cron(args, app)

        self.assertEqual(code, 0)
        self.assertIn("Cron job created", stdout.getvalue())
        app.tools.execute_calls.assert_called_once()
        call = app.tools.execute_calls.call_args
        tool_calls = call.args[0]
        context = call.kwargs["context"]
        self.assertEqual(tool_calls[0].name, "task.schedule")
        self.assertEqual(tool_calls[0].arguments["instruction"], "check health")
        self.assertEqual(
            tool_calls[0].arguments["schedule"],
            {"kind": "every", "every_ms": 60_000},
        )
        self.assertEqual(context.session_id, "cron-cli")
        self.assertEqual(context.metadata["agent_id"], "ops")

    def test_run_cron_pause_dispatches_task_pause(self) -> None:
        app = self._mock_app(ok=True, data={"task_id": "job-123", "enabled": False})
        args = Namespace(
            cron_command="pause",
            task_id="job-123",
            agent_id="ops",
            session="cron-cli",
            json=False,
        )
        selector = mock.Mock()
        selector.runtime_binding_policy_metadata.return_value = {}
        with (
            mock.patch(
                "openminion.cli.commands.cron.ToolSelectionService",
                return_value=selector,
            ),
            mock.patch("sys.stdout", new_callable=io.StringIO) as stdout,
        ):
            code = run_cron(args, app)
        self.assertEqual(code, 0)
        self.assertIn("Cron job paused", stdout.getvalue())
        tool_call = app.tools.execute_calls.call_args.args[0][0]
        self.assertEqual(tool_call.name, "task.pause")
        self.assertEqual(tool_call.arguments, {"task_id": "job-123"})

    def test_run_cron_resume_dispatches_task_resume(self) -> None:
        app = self._mock_app(
            ok=True,
            data={
                "task_id": "job-123",
                "enabled": True,
                "next_due_at": "2026-03-21T00:00:00Z",
            },
        )
        args = Namespace(
            cron_command="resume",
            task_id="job-123",
            agent_id="ops",
            session="cron-cli",
            json=False,
        )
        selector = mock.Mock()
        selector.runtime_binding_policy_metadata.return_value = {}
        with (
            mock.patch(
                "openminion.cli.commands.cron.ToolSelectionService",
                return_value=selector,
            ),
            mock.patch("sys.stdout", new_callable=io.StringIO) as stdout,
        ):
            code = run_cron(args, app)
        self.assertEqual(code, 0)
        self.assertIn("Cron job resumed", stdout.getvalue())
        tool_call = app.tools.execute_calls.call_args.args[0][0]
        self.assertEqual(tool_call.name, "task.resume")

    def test_run_cron_show_dispatches_task_show(self) -> None:
        app = self._mock_app(
            ok=True,
            data={
                "task": {
                    "task_id": "job-123",
                    "enabled": True,
                    "schedule_summary": "every:60000ms",
                    "next_due_at": "2026-03-21T00:00:00Z",
                    "latest_run_state": "completed",
                    "latest_run_at": "2026-03-20T23:59:00Z",
                    "failure_count": 0,
                }
            },
        )
        args = Namespace(
            cron_command="show",
            task_id="job-123",
            runs_limit=7,
            agent_id="ops",
            session="cron-cli",
            json=False,
        )
        selector = mock.Mock()
        selector.runtime_binding_policy_metadata.return_value = {}
        with (
            mock.patch(
                "openminion.cli.commands.cron.ToolSelectionService",
                return_value=selector,
            ),
            mock.patch("sys.stdout", new_callable=io.StringIO) as stdout,
        ):
            code = run_cron(args, app)
        self.assertEqual(code, 0)
        self.assertIn("Cron job details", stdout.getvalue())
        tool_call = app.tools.execute_calls.call_args.args[0][0]
        self.assertEqual(tool_call.name, "task.show")
        self.assertEqual(tool_call.arguments, {"task_id": "job-123", "runs_limit": 7})

    def test_run_cron_create_json_error_returns_nonzero(self) -> None:
        app = self._mock_app(ok=False, data={}, error="task.schedule failed")
        args = Namespace(
            cron_command="create",
            instruction="check health",
            every_ms=None,
            cron_expr="*/5 * * * *",
            at_iso="",
            timezone="UTC",
            name=None,
            agent_id=None,
            session="cron-cli",
            json=True,
        )

        selector = mock.Mock()
        selector.runtime_binding_policy_metadata.return_value = {}
        with (
            mock.patch(
                "openminion.cli.commands.cron.ToolSelectionService",
                return_value=selector,
            ),
            mock.patch("sys.stdout", new_callable=io.StringIO) as stdout,
        ):
            code = run_cron(args, app)

        self.assertEqual(code, 1)
        payload = json.loads(stdout.getvalue())
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["error"], "task.schedule failed")
