from __future__ import annotations

import io
import json
import sys
import unittest
from contextlib import redirect_stdout
from argparse import Namespace
from types import SimpleNamespace
from unittest import mock

from openminion.cli.commands.cron import run_cron
from openminion.cli.commands.task_tools import build_schedule_payload
from openminion.cli.parser.base import build_parser


class CronCommandTests(unittest.TestCase):
    def setUp(self) -> None:
        current = sys.modules.get("openminion.cli.commands.cron")
        if current is not None:
            globals()["cron_command"] = current
            globals()["run_cron"] = current.run_cron

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
            agents={"ops": object()},
            default_agent="ops",
            runtime=SimpleNamespace(
                env={"OPENMINION_DEBUG": "1"},
                tool_selection=SimpleNamespace(),
            ),
        )
        task_manager = mock.Mock(spec=["get_task", "list_scheduled_jobs"])
        return SimpleNamespace(
            config=config,
            tools=tools,
            task_manager=task_manager,
            scheduler_readiness=mock.Mock(return_value={"state": "ready"}),
        )

    def test_build_schedule_payload_every_ms(self) -> None:
        args = Namespace(every_ms=60_000, cron_expr="", at_iso="", timezone="")
        self.assertEqual(
            build_schedule_payload(args),
            {"kind": "every", "every_ms": 60_000},
        )

    def test_cron_help_describes_task_run_and_scheduler_status(self) -> None:
        output = io.StringIO()
        with redirect_stdout(output), self.assertRaises(SystemExit):
            build_parser().parse_args(["cron", "--help"])

        help_text = output.getvalue()
        self.assertIn("run                 Run one exact owned task now", help_text)
        self.assertIn("status              Show cron scheduler health", help_text)

    def test_build_schedule_payload_cron_with_tz(self) -> None:
        args = Namespace(
            every_ms=None, cron_expr="*/5 * * * *", at_iso="", timezone="UTC"
        )
        self.assertEqual(
            build_schedule_payload(args),
            {"kind": "cron", "expr": "*/5 * * * *", "tz": "UTC"},
        )

    def test_build_schedule_payload_at(self) -> None:
        args = Namespace(
            every_ms=None, cron_expr="", at_iso="2026-03-21T00:00:00Z", timezone=""
        )
        self.assertEqual(
            build_schedule_payload(args),
            {"kind": "at", "at": "2026-03-21T00:00:00Z"},
        )

    def test_build_schedule_payload_rejects_non_positive_every_ms(self) -> None:
        args = Namespace(every_ms=0, cron_expr="", at_iso="", timezone="")
        with self.assertRaisesRegex(ValueError, "at least 10000"):
            build_schedule_payload(args)

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
                "openminion.cli.commands.task_tools.ToolSelectionService",
                return_value=selector,
            ),
            mock.patch("sys.stdout", new_callable=io.StringIO) as stdout,
        ):
            code = run_cron(args, app)

        self.assertEqual(code, 0)
        self.assertIn("Scheduled task created", stdout.getvalue())
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
        self.assertIs(context.task_manager, app.task_manager)
        self.assertIs(context.scheduler_readiness, app.scheduler_readiness)

    def test_run_cron_uses_configured_default_agent(self) -> None:
        app = self._mock_app(
            ok=True,
            data={"task_id": "job-123", "name": "health-check"},
        )
        args = Namespace(
            cron_command="create",
            instruction="check health",
            every_ms=60_000,
            cron_expr="",
            at_iso="",
            timezone="",
            name="health-check",
            agent_id="",
            session="cron-cli",
            json=False,
        )
        selector = mock.Mock()
        selector.runtime_binding_policy_metadata.return_value = {}
        with mock.patch(
            "openminion.cli.commands.task_tools.ToolSelectionService",
            return_value=selector,
        ):
            self.assertEqual(run_cron(args, app), 0)
        context = app.tools.execute_calls.call_args.kwargs["context"]
        self.assertEqual(context.metadata["agent_id"], "ops")

    def test_run_cron_status_reports_scheduler_health_only(self) -> None:
        app = self._mock_app()
        app.scheduler_readiness.return_value = {
            "state": "ready",
            "last_heartbeat_at": "2026-10-18T12:00:00Z",
        }
        args = Namespace(cron_command="status")

        with mock.patch("sys.stdout", new_callable=io.StringIO) as stdout:
            code = run_cron(args, app)

        self.assertEqual(code, 0)
        self.assertIn("Scheduler Status", stdout.getvalue())
        self.assertNotIn("Registered scheduled tasks", stdout.getvalue())

    def test_run_cron_status_is_nonzero_when_scheduler_is_unhealthy(self) -> None:
        app = self._mock_app()
        app.scheduler_readiness.return_value = {
            "state": "unavailable",
            "reason": "daemon_unreachable",
            "check_command": "openminion service status cron",
        }

        with mock.patch("sys.stdout", new_callable=io.StringIO):
            code = run_cron(Namespace(cron_command="status"), app)

        self.assertEqual(code, 1)

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
                "openminion.cli.commands.task_tools.ToolSelectionService",
                return_value=selector,
            ),
            mock.patch("sys.stdout", new_callable=io.StringIO) as stdout,
        ):
            code = run_cron(args, app)
        self.assertEqual(code, 0)
        self.assertIn("Scheduled task paused", stdout.getvalue())
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
                "openminion.cli.commands.task_tools.ToolSelectionService",
                return_value=selector,
            ),
            mock.patch("sys.stdout", new_callable=io.StringIO) as stdout,
        ):
            code = run_cron(args, app)
        self.assertEqual(code, 0)
        self.assertIn("Scheduled task resumed", stdout.getvalue())
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
                "openminion.cli.commands.task_tools.ToolSelectionService",
                return_value=selector,
            ),
            mock.patch("sys.stdout", new_callable=io.StringIO) as stdout,
        ):
            code = run_cron(args, app)
        self.assertEqual(code, 0)
        self.assertIn("Scheduled task details", stdout.getvalue())
        tool_call = app.tools.execute_calls.call_args.args[0][0]
        self.assertEqual(tool_call.name, "task.show")
        self.assertEqual(tool_call.arguments, {"task_id": "job-123", "runs_limit": 7})

    def test_run_cron_run_dispatches_task_run_now(self) -> None:
        app = self._mock_app(ok=True, data={"run_id": "run-123"})
        args = Namespace(
            cron_command="run",
            job_id="job-123",
            agent_id="ops",
            session="cron-cli",
            json=False,
        )
        selector = mock.Mock()
        selector.runtime_binding_policy_metadata.return_value = {}
        with (
            mock.patch(
                "openminion.cli.commands.task_tools.ToolSelectionService",
                return_value=selector,
            ),
            mock.patch("sys.stdout", new_callable=io.StringIO) as stdout,
        ):
            code = run_cron(args, app)
        self.assertEqual(code, 0)
        self.assertIn("run-123", stdout.getvalue())
        tool_call = app.tools.execute_calls.call_args.args[0][0]
        self.assertEqual(tool_call.name, "task.run_now")
        self.assertEqual(tool_call.arguments, {"task_id": "job-123"})

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
                "openminion.cli.commands.task_tools.ToolSelectionService",
                return_value=selector,
            ),
            mock.patch("sys.stdout", new_callable=io.StringIO) as stdout,
        ):
            code = run_cron(args, app)

        self.assertEqual(code, 1)
        payload = json.loads(stdout.getvalue())
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["error"], "task.schedule failed")
