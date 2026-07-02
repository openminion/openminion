from __future__ import annotations

import io
import os
import subprocess
import sys
import types
import unittest
from argparse import Namespace
from contextlib import redirect_stdout
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from openminion.base.types import AgentResponse, Message
from openminion.cli.commands.agent import run_agent
from openminion.cli.parser.base import build_parser


class ParserTests(unittest.TestCase):
    def test_root_parser_rejects_conf_alias(self) -> None:
        parser = build_parser()
        with self.assertRaises(SystemExit) as ctx:
            parser.parse_args(["--" + "conf", "config.json", "config", "show"])
        self.assertEqual(ctx.exception.code, 2)

    def test_gateway_once_parse(self) -> None:
        parser = build_parser()
        args = parser.parse_args(
            [
                "gateway",
                "run",
                "--agent-id",
                "ops",
                "--once",
                "--message",
                "hello",
                "--session-id",
                "session-1",
                "--idempotency-key",
                "idem-123",
            ]
        )

        self.assertEqual(args.command, "gateway")
        self.assertEqual(args.gateway_command, "run")
        self.assertEqual(args.agent_id, "ops")
        self.assertTrue(args.once)
        self.assertEqual(args.message, "hello")
        self.assertEqual(args.session_id, "session-1")
        self.assertEqual(args.idempotency_key, "idem-123")
        self.assertTrue(callable(args.handler))

    def test_gateway_interactive_no_progress_parse(self) -> None:
        parser = build_parser()
        args = parser.parse_args(
            [
                "gateway",
                "run",
                "--agent-id",
                "ops",
                "--target",
                "chat",
                "--no-progress",
                "--quiet",
            ]
        )

        self.assertEqual(args.command, "gateway")
        self.assertEqual(args.gateway_command, "run")
        self.assertEqual(args.agent_id, "ops")
        self.assertEqual(args.target, "chat")
        self.assertFalse(args.once)
        self.assertTrue(args.no_progress)
        self.assertTrue(args.quiet)
        self.assertTrue(callable(args.handler))

    def test_api_run_parse(self) -> None:
        parser = build_parser()
        args = parser.parse_args(["api", "run", "--host", "0.0.0.0", "--port", "8080"])

        self.assertEqual(args.command, "api")
        self.assertEqual(args.api_command, "run")
        self.assertEqual(args.host, "0.0.0.0")
        self.assertEqual(args.port, 8080)
        self.assertTrue(callable(args.handler))

    def test_build_parser_does_not_import_api_server(self) -> None:
        script = (
            "import sys; "
            "from openminion.cli.parser.base import build_parser; "
            "build_parser(); "
            "print('openminion.api.server' in sys.modules)"
        )
        result = subprocess.run(
            [sys.executable, "-c", script],
            check=True,
            capture_output=True,
            text=True,
        )

        self.assertEqual(result.stdout.strip(), "False")

    def test_root_parser_accepts_allow_unsandboxed_exec_flag(self) -> None:
        parser = build_parser()
        args = parser.parse_args(["--allow-unsandboxed-exec", "chat"])

        self.assertTrue(args.allow_unsandboxed_exec)

    def test_daemon_start_parse(self) -> None:
        parser = build_parser()
        args = parser.parse_args(["daemon", "start"])

        self.assertEqual(args.command, "daemon")
        self.assertEqual(args.daemon_command, "start")
        self.assertTrue(callable(args.handler))

    def test_cron_create_parse(self) -> None:
        parser = build_parser()
        args = parser.parse_args(
            [
                "cron",
                "create",
                "--instruction",
                "check health",
                "--every-ms",
                "60000",
                "--name",
                "health-check",
                "--agent-id",
                "ops",
                "--session",
                "cron-ops",
                "--json",
            ]
        )

        self.assertEqual(args.command, "cron")
        self.assertEqual(args.cron_command, "create")
        self.assertEqual(args.instruction, "check health")
        self.assertEqual(args.every_ms, 60000)
        self.assertEqual(args.name, "health-check")
        self.assertEqual(args.agent_id, "ops")
        self.assertEqual(args.session, "cron-ops")
        self.assertTrue(args.json)
        self.assertTrue(callable(args.handler))

    def test_cron_pause_parse(self) -> None:
        parser = build_parser()
        args = parser.parse_args(
            [
                "cron",
                "pause",
                "job-123",
                "--agent-id",
                "ops",
                "--session",
                "cron-ops",
                "--json",
            ]
        )
        self.assertEqual(args.command, "cron")
        self.assertEqual(args.cron_command, "pause")
        self.assertEqual(args.task_id, "job-123")
        self.assertEqual(args.agent_id, "ops")
        self.assertEqual(args.session, "cron-ops")
        self.assertTrue(args.json)
        self.assertTrue(callable(args.handler))

    def test_cron_resume_parse(self) -> None:
        parser = build_parser()
        args = parser.parse_args(
            [
                "cron",
                "resume",
                "job-123",
                "--agent-id",
                "ops",
                "--session",
                "cron-ops",
                "--json",
            ]
        )
        self.assertEqual(args.command, "cron")
        self.assertEqual(args.cron_command, "resume")
        self.assertEqual(args.task_id, "job-123")
        self.assertEqual(args.agent_id, "ops")
        self.assertEqual(args.session, "cron-ops")
        self.assertTrue(args.json)
        self.assertTrue(callable(args.handler))

    def test_cron_show_parse(self) -> None:
        parser = build_parser()
        args = parser.parse_args(
            [
                "cron",
                "show",
                "job-123",
                "--runs-limit",
                "7",
                "--agent-id",
                "ops",
                "--session",
                "cron-ops",
                "--json",
            ]
        )
        self.assertEqual(args.command, "cron")
        self.assertEqual(args.cron_command, "show")
        self.assertEqual(args.task_id, "job-123")
        self.assertEqual(args.runs_limit, 7)
        self.assertEqual(args.agent_id, "ops")
        self.assertEqual(args.session, "cron-ops")
        self.assertTrue(args.json)
        self.assertTrue(callable(args.handler))

    def test_run_parse(self) -> None:
        parser = build_parser()
        args = parser.parse_args(
            [
                "run",
                "hello world",
                "--agent",
                "ops",
                "--session",
                "session-123",
                "--stream",
                "--json",
            ]
        )

        self.assertEqual(args.command, "run")
        self.assertEqual(args.prompt, "hello world")
        self.assertEqual(args.agent, "ops")
        self.assertEqual(args.session, "session-123")
        self.assertTrue(args.stream)
        self.assertTrue(args.json)
        self.assertTrue(callable(args.handler))

    def test_chat_parse(self) -> None:
        parser = build_parser()
        args = parser.parse_args(
            [
                "chat",
                "--agent",
                "research",
                "--session",
                "chat-1",
                "--conversation",
                "conv-1",
            ]
        )

        self.assertEqual(args.command, "chat")
        self.assertEqual(args.agent, "research")
        self.assertEqual(args.session, "chat-1")
        self.assertEqual(args.conversation, "conv-1")
        self.assertFalse(args.quiet)
        self.assertFalse(args.no_progress)
        self.assertFalse(args.no_activity_indicator)
        self.assertFalse(args.demo)
        self.assertTrue(callable(args.handler))

    def test_chat_no_progress_parse(self) -> None:
        parser = build_parser()
        args = parser.parse_args(
            ["chat", "--agent", "research", "--session", "chat-1", "--no-progress"]
        )

        self.assertEqual(args.command, "chat")
        self.assertEqual(args.agent, "research")
        self.assertEqual(args.session, "chat-1")
        self.assertFalse(args.quiet)
        self.assertTrue(args.no_progress)
        self.assertFalse(args.no_activity_indicator)
        self.assertTrue(callable(args.handler))

    def test_chat_no_activity_indicator_parse(self) -> None:
        parser = build_parser()
        args = parser.parse_args(
            [
                "chat",
                "--agent",
                "research",
                "--session",
                "chat-1",
                "--no-progress",
                "--no-activity-indicator",
            ]
        )

        self.assertEqual(args.command, "chat")
        self.assertTrue(args.no_progress)
        self.assertTrue(args.no_activity_indicator)
        self.assertTrue(callable(args.handler))

    def test_chat_quiet_parse(self) -> None:
        parser = build_parser()
        args = parser.parse_args(
            ["chat", "--agent", "research", "--session", "chat-1", "--quiet"]
        )

        self.assertEqual(args.command, "chat")
        self.assertEqual(args.agent, "research")
        self.assertEqual(args.session, "chat-1")
        self.assertTrue(args.quiet)
        self.assertFalse(args.no_progress)
        self.assertTrue(callable(args.handler))

    def test_chat_demo_parse(self) -> None:
        parser = build_parser()
        args = parser.parse_args(["chat", "--demo", "--session", "demo-chat"])

        self.assertEqual(args.command, "chat")
        self.assertTrue(args.demo)
        self.assertEqual(args.session, "demo-chat")
        self.assertTrue(callable(args.handler))

    def test_chat_no_interactive_parse(self) -> None:
        parser = build_parser()
        args = parser.parse_args(["chat", "--no-interactive"])
        self.assertTrue(args.no_interactive)

    def test_chat_resume_reset_parse(self) -> None:
        parser = build_parser()
        args = parser.parse_args(
            ["chat", "--agent", "research", "--session", "chat-1", "--resume"]
        )
        self.assertTrue(args.resume)
        self.assertFalse(args.reset_session)

        args = parser.parse_args(
            ["chat", "--agent", "research", "--session", "chat-1", "--reset-session"]
        )
        self.assertTrue(args.reset_session)

        args = parser.parse_args(
            ["chat", "--agent", "research", "--session", "chat-1", "--sync-identity"]
        )
        self.assertTrue(args.sync_identity)

    def test_tui_parse(self) -> None:
        parser = build_parser()
        args = parser.parse_args(["tui", "--demo", "--agent", "ops"])

        self.assertEqual(args.command, "tui")
        self.assertTrue(args.demo)
        self.assertEqual(args.agent, "ops")
        self.assertTrue(callable(args.handler))

        args = parser.parse_args(["tui", "--agent", "ops", "--sync-identity"])
        self.assertTrue(args.sync_identity)

        args = parser.parse_args(["tui", "--no-interactive"])
        self.assertTrue(args.no_interactive)

    def test_focus_parse(self) -> None:
        parser = build_parser()
        args = parser.parse_args(
            ["focus", "--agent", "alpha", "--session", "focus-1", "--dir", "/tmp/work"]
        )

        self.assertEqual(args.command, "focus")
        self.assertEqual(args.agent, "alpha")
        self.assertEqual(args.session, "focus-1")
        self.assertEqual(args.dir, "/tmp/work")
        self.assertFalse(args.no_interactive)
        self.assertTrue(callable(args.handler))

        args = parser.parse_args(["focus", "--no-interactive"])
        self.assertTrue(args.no_interactive)

    def test_setup_parse(self) -> None:
        from openminion.base.config import AgentProfileConfig, OpenMinionConfig

        parser = build_parser()
        args = parser.parse_args(["setup", "--agent", "ops", "--no-chat"])

        self.assertEqual(args.command, "setup")
        self.assertEqual(args.agent, "ops")
        self.assertTrue(args.no_chat)
        self.assertTrue(callable(args.handler))
        buf = io.StringIO()
        with (
            mock.patch(
                "openminion.cli.commands.setup._run_wizard",
                return_value=(
                    OpenMinionConfig(
                        agents={"ops": AgentProfileConfig(name="ops", provider="echo")}
                    ),
                    os.devnull,
                ),
            ),
            mock.patch(
                "openminion.cli.commands.setup._run_setup_doctor",
                return_value=0,
            ),
            redirect_stdout(buf),
        ):
            code = args.handler(args)
        self.assertEqual(code, 0)
        output = buf.getvalue()
        self.assertIn("Initialized onboarding config", output)
        self.assertIn("Chat launch skipped", output)

    def test_setup_parse_stale_handler_still_uses_canonical_patched_helpers(
        self,
    ) -> None:
        from openminion.base.config import AgentProfileConfig, OpenMinionConfig

        parser = build_parser()
        args = parser.parse_args(["setup", "--agent", "ops", "--no-chat"])

        def _fail_wizard(*_args, **_kwargs):
            raise AssertionError("stale _run_wizard seam used")

        def _fail_doctor(*_args, **_kwargs):
            raise AssertionError("stale _run_setup_doctor seam used")

        stale_globals = dict(args.handler.__globals__)
        stale_globals["_run_wizard"] = _fail_wizard
        stale_globals["_run_setup_doctor"] = _fail_doctor
        stale_handler = types.FunctionType(
            args.handler.__code__,
            stale_globals,
            name=args.handler.__name__,
            argdefs=args.handler.__defaults__,
            closure=args.handler.__closure__,
        )
        stale_handler.__kwdefaults__ = args.handler.__kwdefaults__

        with (
            mock.patch(
                "openminion.cli.commands.setup._run_wizard",
                return_value=(
                    OpenMinionConfig(
                        agents={"ops": AgentProfileConfig(name="ops", provider="echo")}
                    ),
                    os.devnull,
                ),
            ),
            mock.patch(
                "openminion.cli.commands.setup._run_setup_doctor",
                return_value=0,
            ),
        ):
            code = stale_handler(args)

        self.assertEqual(code, 0)

    def test_config_export_parse(self) -> None:
        parser = build_parser()
        args = parser.parse_args(["config", "export", "--out", "portable.yaml"])

        self.assertEqual(args.command, "config")
        self.assertEqual(args.config_command, "export")
        self.assertEqual(args.output, "portable.yaml")
        self.assertFalse(args.include_secrets)
        self.assertTrue(callable(args.handler))

    def test_config_import_parse(self) -> None:
        parser = build_parser()
        args = parser.parse_args(["config", "import", "portable.yaml", "--force"])

        self.assertEqual(args.command, "config")
        self.assertEqual(args.config_command, "import")
        self.assertEqual(args.input, "portable.yaml")
        self.assertTrue(args.force)
        self.assertTrue(callable(args.handler))

    def test_tools_run_parse(self) -> None:
        parser = build_parser()
        args = parser.parse_args(
            [
                "tools",
                "run",
                "weather.openmeteo.current",
                "--json",
                '{"city":"Tokyo"}',
                "--session",
                "tools-1",
            ]
        )

        self.assertEqual(args.command, "tools")
        self.assertEqual(args.tools_command, "run")
        self.assertEqual(args.tool, "weather.openmeteo.current")
        self.assertEqual(args.json_payload, '{"city":"Tokyo"}')
        self.assertEqual(args.session, "tools-1")
        self.assertTrue(callable(args.handler))

    def test_time_now_parse(self) -> None:
        parser = build_parser()
        args = parser.parse_args(
            ["time", "now", "--tz", "America/Los_Angeles", "--session", "time-1"]
        )

        self.assertEqual(args.command, "time")
        self.assertEqual(args.time_command, "now")
        self.assertEqual(args.timezone, "America/Los_Angeles")
        self.assertEqual(args.session, "time-1")
        self.assertTrue(callable(args.handler))

    def test_time_diff_parse(self) -> None:
        parser = build_parser()
        args = parser.parse_args(
            [
                "time",
                "diff",
                "--a",
                "2026-03-11T07:00:00Z",
                "--b",
                "2026-03-11T08:00:00Z",
                "--unit",
                "minutes",
                "--signed",
            ]
        )

        self.assertEqual(args.command, "time")
        self.assertEqual(args.time_command, "diff")
        self.assertEqual(args.unit, "minutes")
        self.assertTrue(args.signed)
        self.assertTrue(callable(args.handler))

    def test_time_next_cron_parse(self) -> None:
        parser = build_parser()
        args = parser.parse_args(
            [
                "time",
                "next-cron",
                "--cron",
                "0 9 * * 1-5",
                "--tz",
                "America/New_York",
                "--count",
                "5",
            ]
        )

        self.assertEqual(args.command, "time")
        self.assertEqual(args.time_command, "next-cron")
        self.assertEqual(args.cron, "0 9 * * 1-5")
        self.assertEqual(args.timezone, "America/New_York")
        self.assertEqual(args.count, 5)
        self.assertTrue(callable(args.handler))

    def test_agent_parse(self) -> None:
        parser = build_parser()
        args = parser.parse_args(
            [
                "agent",
                "--message",
                "status",
                "--agent-id",
                "research",
                "--session-id",
                "session-2",
            ]
        )

        self.assertEqual(args.command, "agent")
        self.assertEqual(args.message, "status")
        self.assertEqual(args.agent_id, "research")
        self.assertEqual(args.session_id, "session-2")
        self.assertTrue(callable(args.handler))

    def test_scaffold_parse(self) -> None:
        parser = build_parser()
        args = parser.parse_args(
            ["scaffold", "provider", "weather", "--root", "/tmp/work"]
        )

        self.assertEqual(args.command, "scaffold")
        self.assertEqual(args.component, "provider")
        self.assertEqual(args.name, "weather")
        self.assertEqual(args.root, "/tmp/work")
        self.assertFalse(args.force)
        self.assertTrue(callable(args.handler))

    def test_scaffold_pack_parse(self) -> None:
        parser = build_parser()
        args = parser.parse_args(
            ["scaffold", "pack-memory", "starter", "--root", "/tmp/work"]
        )

        self.assertEqual(args.command, "scaffold")
        self.assertEqual(args.component, "pack-memory")
        self.assertEqual(args.name, "starter")
        self.assertEqual(args.root, "/tmp/work")
        self.assertFalse(args.force)
        self.assertTrue(callable(args.handler))

    def test_scaffold_chat_pack_parse(self) -> None:
        parser = build_parser()
        args = parser.parse_args(
            ["scaffold", "pack-channels-chat", "social", "--root", "/tmp/work"]
        )

        self.assertEqual(args.command, "scaffold")
        self.assertEqual(args.component, "pack-channels-chat")
        self.assertEqual(args.name, "social")
        self.assertEqual(args.root, "/tmp/work")
        self.assertFalse(args.force)
        self.assertTrue(callable(args.handler))

    def test_agent_check_parse(self) -> None:
        parser = build_parser()
        args = parser.parse_args(
            [
                "agent-check",
                "--message",
                "ping",
                "--channel",
                "console",
                "--agent-id",
                "ops",
            ]
        )

        self.assertEqual(args.command, "agent-check")
        self.assertEqual(args.message, "ping")
        self.assertEqual(args.channel, "console")
        self.assertEqual(args.agent_id, "ops")
        self.assertFalse(args.deliver)
        self.assertTrue(callable(args.handler))

    def test_doctor_parse(self) -> None:
        parser = build_parser()
        args = parser.parse_args(
            ["doctor", "--check-turn", "--agent-id", "ops", "--json"]
        )

        self.assertEqual(args.command, "doctor")
        self.assertTrue(args.check_turn)
        self.assertEqual(args.agent_id, "ops")
        self.assertTrue(args.json)
        self.assertTrue(callable(args.handler))

    def test_status_runs_parse(self) -> None:
        parser = build_parser()
        args = parser.parse_args(
            ["status", "runs", "--session-id", "session-1", "--limit", "10", "--json"]
        )

        self.assertEqual(args.command, "status")
        self.assertEqual(args.status_command, "runs")
        self.assertEqual(args.session_id, "session-1")
        self.assertEqual(args.limit, 10)
        self.assertTrue(args.json)
        self.assertTrue(callable(args.handler))

    def test_status_run_events_parse(self) -> None:
        parser = build_parser()
        args = parser.parse_args(
            [
                "status",
                "run-events",
                "--session-id",
                "session-1",
                "--run-id",
                "run-1",
                "--limit",
                "25",
            ]
        )

        self.assertEqual(args.command, "status")
        self.assertEqual(args.status_command, "run-events")
        self.assertEqual(args.session_id, "session-1")
        self.assertEqual(args.run_id, "run-1")
        self.assertEqual(args.limit, 25)
        self.assertFalse(args.json)
        self.assertTrue(callable(args.handler))

    def test_status_notes_parse(self) -> None:
        parser = build_parser()
        args = parser.parse_args(
            ["status", "notes", "--agent-id", "ops-agent", "--json"]
        )

        self.assertEqual(args.command, "status")
        self.assertEqual(args.status_command, "notes")
        self.assertEqual(args.agent_id, "ops-agent")
        self.assertTrue(args.json)
        self.assertTrue(callable(args.handler))

    def test_status_note_activate_parse(self) -> None:
        parser = build_parser()
        args = parser.parse_args(
            [
                "status",
                "note-activate",
                "--agent-id",
                "ops-agent",
                "--signature",
                "tool.weather.openmeteo.current.missing-city-argument",
            ]
        )

        self.assertEqual(args.command, "status")
        self.assertEqual(args.status_command, "note-activate")
        self.assertEqual(args.agent_id, "ops-agent")
        self.assertEqual(
            args.signature, "tool.weather.openmeteo.current.missing-city-argument"
        )
        self.assertFalse(args.json)
        self.assertTrue(callable(args.handler))

    def test_status_owner_parse(self) -> None:
        parser = build_parser()
        args = parser.parse_args(
            [
                "status",
                "owner",
                "--session-limit",
                "12",
                "--run-limit",
                "8",
                "--hours",
                "48",
                "--json",
            ]
        )

        self.assertEqual(args.command, "status")
        self.assertEqual(args.status_command, "owner")
        self.assertEqual(args.session_limit, 12)
        self.assertEqual(args.run_limit, 8)
        self.assertEqual(args.hours, 48)
        self.assertTrue(args.json)
        self.assertTrue(callable(args.handler))

    def test_status_onboarding_parse(self) -> None:
        parser = build_parser()
        args = parser.parse_args(
            ["status", "onboarding", "--agent-id", "ops-agent", "--json"]
        )

        self.assertEqual(args.command, "status")
        self.assertEqual(args.status_command, "onboarding")
        self.assertEqual(args.agent_id, "ops-agent")
        self.assertTrue(args.json)
        self.assertTrue(callable(args.handler))

    def test_status_identity_parse(self) -> None:
        parser = build_parser()
        args = parser.parse_args(
            [
                "status",
                "identity",
                "--agent-id",
                "ops-agent",
                "--root",
                "/tmp/work",
                "--json",
            ]
        )

        self.assertEqual(args.command, "status")
        self.assertEqual(args.status_command, "identity")
        self.assertEqual(args.agent_id, "ops-agent")
        self.assertEqual(args.root, "/tmp/work")
        self.assertTrue(args.json)
        self.assertTrue(callable(args.handler))

    def test_status_identity_help_mentions_identityctl_and_deprecated_root(
        self,
    ) -> None:
        parser = build_parser()
        status_parser = None
        status_identity_parser = None
        for action in parser._actions:
            if getattr(action, "dest", None) != "command":
                continue
            command_map = getattr(action, "choices", {})
            status_parser = command_map.get("status")
            if status_parser is not None:
                break

        self.assertIsNotNone(status_parser)
        self.assertIn(
            "Inspect IdentityCtl profile/render state for an agent",
            status_parser.format_help(),
        )
        for action in status_parser._actions:
            if getattr(action, "dest", None) != "status_command":
                continue
            status_identity_parser = getattr(action, "choices", {}).get("identity")
            if status_identity_parser is not None:
                break

        self.assertIsNotNone(status_identity_parser)
        identity_help = status_identity_parser.format_help()
        self.assertIn("Deprecated compatibility flag.", identity_help)

    def test_identity_upsert_parse_accepts_directory_path(self) -> None:
        parser = build_parser()
        args = parser.parse_args(["identity", "upsert", "/tmp/identity-root"])

        self.assertEqual(args.command, "identity")
        self.assertEqual(args.identity_command, "upsert")
        self.assertEqual(args.yaml_path, "/tmp/identity-root")
        self.assertTrue(callable(args.handler))

    def test_identity_import_parse(self) -> None:
        parser = build_parser()
        args = parser.parse_args(
            [
                "identity",
                "import",
                "--from-bundle",
                "/tmp/bundles",
                "--agent-id",
                "ops-agent",
            ]
        )

        self.assertEqual(args.command, "identity")
        self.assertEqual(args.identity_command, "import")
        self.assertEqual(args.from_bundle, "/tmp/bundles")
        self.assertEqual(args.agent_id, "ops-agent")
        self.assertTrue(callable(args.handler))

    def test_identity_export_parse(self) -> None:
        parser = build_parser()
        args = parser.parse_args(
            [
                "identity",
                "export",
                "--output",
                "/tmp/identity.yaml",
                "--agent-id",
                "ops-agent",
            ]
        )

        self.assertEqual(args.command, "identity")
        self.assertEqual(args.identity_command, "export")
        self.assertEqual(args.output, "/tmp/identity.yaml")
        self.assertEqual(args.agent_id, "ops-agent")
        self.assertTrue(callable(args.handler))

    def test_identity_export_markdown_parse(self) -> None:
        parser = build_parser()
        args = parser.parse_args(
            [
                "identity",
                "export",
                "--output-dir",
                "/tmp/identity-bundles",
                "--agent-id",
                "ops-agent",
                "--force",
            ]
        )

        self.assertEqual(args.command, "identity")
        self.assertEqual(args.identity_command, "export")
        self.assertEqual(args.output_dir, "/tmp/identity-bundles")
        self.assertEqual(args.agent_id, "ops-agent")
        self.assertTrue(args.force)
        self.assertTrue(callable(args.handler))

    def test_identity_diff_parse(self) -> None:
        parser = build_parser()
        args = parser.parse_args(
            [
                "identity",
                "diff",
                "ops-agent",
                "--bundle-dir",
                "/tmp/identity-bundles",
            ]
        )

        self.assertEqual(args.command, "identity")
        self.assertEqual(args.identity_command, "diff")
        self.assertEqual(args.agent_id, "ops-agent")
        self.assertEqual(args.bundle_dir, "/tmp/identity-bundles")
        self.assertTrue(callable(args.handler))

    def test_identity_help_mentions_startup_precedence_and_directory_upsert(
        self,
    ) -> None:
        parser = build_parser()
        identity_parser = None
        identity_upsert_parser = None
        identity_import_parser = None
        identity_export_parser = None
        for action in parser._actions:
            if getattr(action, "dest", None) != "command":
                continue
            identity_parser = getattr(action, "choices", {}).get("identity")
            if identity_parser is not None:
                break

        self.assertIsNotNone(identity_parser)
        identity_help = identity_parser.format_help()
        self.assertIn("Startup precedence: YAML sync first", identity_help)
        self.assertIn("default fallback last.", identity_help)

        for action in identity_parser._actions:
            if getattr(action, "dest", None) != "identity_command":
                continue
            identity_upsert_parser = getattr(action, "choices", {}).get("upsert")
            if identity_upsert_parser is not None:
                break

        self.assertIsNotNone(identity_upsert_parser)
        upsert_help = identity_upsert_parser.format_help()
        self.assertIn("YAML file or directory", upsert_help)

        for action in identity_parser._actions:
            if getattr(action, "dest", None) != "identity_command":
                continue
            identity_import_parser = getattr(action, "choices", {}).get("import")
            if identity_import_parser is not None:
                break

        self.assertIsNotNone(identity_import_parser)
        import_help = identity_import_parser.format_help()
        self.assertIn("--from-bundle", import_help)
        self.assertIn("--agent-id", import_help)

        for action in identity_parser._actions:
            if getattr(action, "dest", None) != "identity_command":
                continue
            identity_export_parser = getattr(action, "choices", {}).get("export")
            if identity_export_parser is not None:
                break

        self.assertIsNotNone(identity_export_parser)
        export_help = identity_export_parser.format_help()
        self.assertIn("--output", export_help)
        self.assertIn("--output-dir", export_help)
        self.assertIn("--force", export_help)

        identity_diff_parser = None
        for action in identity_parser._actions:
            if getattr(action, "dest", None) != "identity_command":
                continue
            identity_diff_parser = getattr(action, "choices", {}).get("diff")
            if identity_diff_parser is not None:
                break

        self.assertIsNotNone(identity_diff_parser)
        diff_help = identity_diff_parser.format_help()
        self.assertIn("--bundle-dir", diff_help)

    def test_status_action_policy_parse(self) -> None:
        parser = build_parser()
        args = parser.parse_args(
            [
                "status",
                "action-policy",
                "--session-id",
                "policy-session-1",
                "--json",
            ]
        )

        self.assertEqual(args.command, "status")
        self.assertEqual(args.status_command, "action-policy")
        self.assertEqual(args.session_id, "policy-session-1")
        self.assertTrue(args.json)
        self.assertTrue(callable(args.handler))

    def test_storage_status_parse(self) -> None:
        parser = build_parser()
        args = parser.parse_args(
            ["storage", "status", "--sqlite", "/tmp/openminion.db", "--json"]
        )

        self.assertEqual(args.command, "storage")
        self.assertEqual(args.storage_command, "status")
        self.assertEqual(args.sqlite, "/tmp/openminion.db")
        self.assertTrue(args.json)
        self.assertTrue(callable(args.handler))

    def test_storage_reindex_parse(self) -> None:
        parser = build_parser()
        args = parser.parse_args(
            [
                "storage",
                "reindex",
                "--fallback",
                "/tmp/fallback",
                "--since-ts",
                "2026-01-01T00:00:00+00:00",
            ]
        )

        self.assertEqual(args.command, "storage")
        self.assertEqual(args.storage_command, "reindex")
        self.assertEqual(args.fallback, "/tmp/fallback")
        self.assertEqual(args.since_ts, "2026-01-01T00:00:00+00:00")
        self.assertTrue(callable(args.handler))

    def test_verify_parse(self) -> None:
        parser = build_parser()
        args = parser.parse_args(
            ["verify", "unit", "--pattern", "test_cli_*.py", "--verbose"]
        )

        self.assertEqual(args.command, "verify")
        self.assertEqual(args.suite, "unit")
        self.assertEqual(args.pattern, "test_cli_*.py")
        self.assertTrue(args.verbose)
        self.assertTrue(callable(args.handler))

    def test_verify_skills_parse(self) -> None:
        parser = build_parser()
        args = parser.parse_args(["verify", "skills", "--json"])

        self.assertEqual(args.command, "verify")
        self.assertEqual(args.suite, "skills")
        self.assertTrue(args.json)
        self.assertTrue(callable(args.handler))

    def test_config_init_provider_parse(self) -> None:
        parser = build_parser()
        args = parser.parse_args(["config", "init", "--provider", "openrouter"])

        self.assertEqual(args.command, "config")
        self.assertEqual(args.config_command, "init")
        self.assertEqual(args.provider, "openrouter")
        self.assertEqual(args.storage_location, "config")
        self.assertIsNone(args.storage_path)
        self.assertTrue(callable(args.handler))

    def test_config_init_provider_parse_cortensor(self) -> None:
        parser = build_parser()
        args = parser.parse_args(["config", "init", "--provider", "cortensor"])

        self.assertEqual(args.command, "config")
        self.assertEqual(args.config_command, "init")
        self.assertEqual(args.provider, "cortensor")
        self.assertEqual(args.storage_location, "config")
        self.assertIsNone(args.storage_path)
        self.assertTrue(callable(args.handler))

    def test_config_init_provider_parse_cerebras(self) -> None:
        parser = build_parser()
        args = parser.parse_args(["config", "init", "--provider", "cerebras"])

        self.assertEqual(args.command, "config")
        self.assertEqual(args.config_command, "init")
        self.assertEqual(args.provider, "cerebras")
        self.assertEqual(args.storage_location, "config")
        self.assertIsNone(args.storage_path)
        self.assertTrue(callable(args.handler))

    def test_config_init_provider_parse_groq(self) -> None:
        parser = build_parser()
        args = parser.parse_args(["config", "init", "--provider", "groq"])

        self.assertEqual(args.command, "config")
        self.assertEqual(args.config_command, "init")
        self.assertEqual(args.provider, "groq")
        self.assertEqual(args.storage_location, "config")
        self.assertIsNone(args.storage_path)
        self.assertTrue(callable(args.handler))

    def test_config_init_storage_flags_parse(self) -> None:
        parser = build_parser()
        args = parser.parse_args(
            [
                "config",
                "init",
                "--provider",
                "echo",
                "--storage-location",
                "home",
                "--storage-path",
                "/tmp/openminion/custom.db",
            ]
        )

        self.assertEqual(args.command, "config")
        self.assertEqual(args.config_command, "init")
        self.assertEqual(args.provider, "echo")
        self.assertEqual(args.storage_location, "home")
        self.assertEqual(args.storage_path, "/tmp/openminion/custom.db")
        self.assertTrue(callable(args.handler))

    def test_top_level_command_order_is_stable(self) -> None:
        parser = build_parser()
        command_action = next(
            action
            for action in parser._actions
            if getattr(action, "dest", None) == "command"
        )

        self.assertEqual(
            list(getattr(command_action, "choices", {}).keys()),
            [
                "config",
                "api",
                "autonomy",
                "data",
                "daemon",
                "run",
                "room",
                "channel",
                "chat",
                "dashboard",
                "tui",
                "sessions",
                "sidecar",
                "tools",
                "toolctl",
                "time",
                "gateway",
                "agent",
                "agent-check",
                "agent-ctl",
                "message",
                "plugins",
                "doctor",
                "status",
                "export",
                "focus",
                "setup",
                "storage",
                "verify",
                "version",
                "scaffold",
                "cron",
                "debug",
                "skill",
                "identity",
                "memory",
                "mcp",
            ],
        )

    def test_top_level_help_hides_agent_ctl_and_preserves_visible_order(self) -> None:
        parser = build_parser()
        help_text = parser.format_help()
        command_action = next(
            action
            for action in parser._actions
            if getattr(action, "dest", None) == "command"
        )

        self.assertNotIn("agent-ctl", help_text)
        self.assertEqual(
            [choice.dest for choice in getattr(command_action, "_choices_actions", [])],
            [
                "config",
                "api",
                "autonomy",
                "data",
                "daemon",
                "run",
                "room",
                "channel",
                "chat",
                "dashboard",
                "tui",
                "sessions",
                "sidecar",
                "tools",
                "toolctl",
                "time",
                "gateway",
                "agent",
                "agent-check",
                "message",
                "plugins",
                "doctor",
                "status",
                "export",
                "focus",
                "setup",
                "storage",
                "verify",
                "version",
                "scaffold",
                "cron",
                "debug",
                "skill",
                "identity",
                "memory",
                "mcp",
            ],
        )

    def test_agent_ctl_parse_is_available_but_hidden(self) -> None:
        parser = build_parser()
        args = parser.parse_args(["agent-ctl", "ls", "--json"])

        self.assertEqual(args.command, "agent-ctl")
        self.assertEqual(args.agent_command, "ls")
        self.assertTrue(args.json)
        self.assertTrue(callable(args.handler))

    def test_sessions_list_json_parse_preserves_output_json_dest(self) -> None:
        parser = build_parser()
        args = parser.parse_args(["sessions", "list", "--limit", "5", "--json"])

        self.assertEqual(args.command, "sessions")
        self.assertEqual(args.sessions_command, "list")
        self.assertEqual(args.limit, 5)
        self.assertTrue(args.output_json)
        self.assertTrue(callable(args.handler))

    def test_sessions_list_help_mentions_json_output(self) -> None:
        parser = build_parser()
        sessions_parser = None
        sessions_list_parser = None
        for action in parser._actions:
            if getattr(action, "dest", None) != "command":
                continue
            sessions_parser = getattr(action, "choices", {}).get("sessions")
            if sessions_parser is not None:
                break

        self.assertIsNotNone(sessions_parser)
        for action in sessions_parser._actions:
            if getattr(action, "dest", None) != "sessions_command":
                continue
            sessions_list_parser = getattr(action, "choices", {}).get("list")
            if sessions_list_parser is not None:
                break

        self.assertIsNotNone(sessions_list_parser)
        sessions_list_help = sessions_list_parser.format_help()
        self.assertIn("--json", sessions_list_help)

    def test_tools_list_help_mentions_catalog_operation(self) -> None:
        parser = build_parser()
        tools_parser = None
        tools_list_parser = None
        for action in parser._actions:
            if getattr(action, "dest", None) != "command":
                continue
            tools_parser = getattr(action, "choices", {}).get("tools")
            if tools_parser is not None:
                break

        self.assertIsNotNone(tools_parser)
        for action in tools_parser._actions:
            if getattr(action, "dest", None) != "tools_command":
                continue
            tools_list_parser = getattr(action, "choices", {}).get("list")
            if tools_list_parser is not None:
                break

        self.assertIsNotNone(tools_list_parser)
        tools_list_help = tools_list_parser.format_help()
        self.assertIn("--available", tools_list_help)
        self.assertIn("--blocked", tools_list_help)


class SkillBootstrapTests(unittest.TestCase):
    def setUp(self) -> None:
        self._env_patcher = mock.patch.dict(
            os.environ,
            {"OPENMINION_DISABLE_SKILL": "1"},
            clear=False,
        )
        self._env_patcher.start()

    def tearDown(self) -> None:
        self._env_patcher.stop()

    def test_parser_import_without_skill_module(self) -> None:
        import sys

        original_path = sys.path.copy()
        filtered_path = [p for p in sys.path if "openminion-skill" not in p]
        sys.path = filtered_path
        try:
            from openminion.cli.parser.base import build_parser

            parser = build_parser()
            self.assertIsNotNone(parser)
        finally:
            sys.path = original_path

    def test_chat_command_parse_without_skill_module(self) -> None:
        import sys

        original_path = sys.path.copy()
        filtered_path = [p for p in sys.path if "openminion-skill" not in p]
        sys.path = filtered_path
        try:
            from openminion.cli.parser.base import build_parser

            parser = build_parser()
            args = parser.parse_args(
                ["chat", "--agent", "test-agent", "--session", "test-session"]
            )
            self.assertEqual(args.command, "chat")
            self.assertEqual(args.agent, "test-agent")
            self.assertEqual(args.session, "test-session")
        finally:
            sys.path = original_path

    def test_skill_command_returns_error_when_unavailable(self) -> None:
        import sys

        original_path = sys.path.copy()
        filtered_path = [p for p in sys.path if "openminion-skill" not in p]
        sys.path = filtered_path
        try:
            from openminion.cli.commands.skill import (
                _check_skill_available,
                _get_skill_error,
            )

            self.assertFalse(_check_skill_available())
            error_msg = _get_skill_error()
            self.assertIn("openminion.modules.skill", error_msg)
            self.assertTrue(
                "pip install" in error_msg or "OPENMINION_DISABLE_SKILL" in error_msg
            )
        finally:
            sys.path = original_path

    def test_skill_ingest_returns_error_json(self) -> None:
        import sys
        import json

        original_path = sys.path.copy()
        filtered_path = [p for p in sys.path if "openminion-skill" not in p]
        sys.path = filtered_path
        try:
            from openminion.cli.commands.skill import _run_skill_ingest
            import io
            import contextlib
            from argparse import Namespace

            args = Namespace(
                config="skill.yaml",
                file="test.md",
                name=None,
                scope="global",
                agent_id=None,
            )
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                result = _run_skill_ingest(args)
            self.assertEqual(result, 1)
            error_output = json.loads(output.getvalue())
            self.assertFalse(error_output.get("ok", True))
            self.assertEqual(
                error_output.get("error", {}).get("code"), "SKILL_NOT_AVAILABLE"
            )
        finally:
            sys.path = original_path

    def test_skill_list_returns_error_json(self) -> None:
        import sys
        import json

        original_path = sys.path.copy()
        filtered_path = [p for p in sys.path if "openminion-skill" not in p]
        sys.path = filtered_path
        try:
            from openminion.cli.commands.skill import _run_skill_list
            import io
            import contextlib
            from argparse import Namespace

            args = Namespace(
                config="skill.yaml",
                status=None,
                scope=None,
                agent_id=None,
                tag=None,
                tool=None,
                json=False,
            )
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                result = _run_skill_list(args)
            self.assertEqual(result, 1)
            error_output = json.loads(output.getvalue())
            self.assertFalse(error_output.get("ok", True))
            self.assertEqual(
                error_output.get("error", {}).get("code"), "SKILL_NOT_AVAILABLE"
            )
        finally:
            sys.path = original_path

    def test_skill_refresh_returns_error_json(self) -> None:
        import sys
        import json

        original_path = sys.path.copy()
        filtered_path = [p for p in sys.path if "openminion-skill" not in p]
        sys.path = filtered_path
        try:
            from openminion.cli.commands.skill import _run_skill_refresh
            import io
            import contextlib
            from argparse import Namespace

            args = Namespace(config="skill.yaml", skill_id="test-skill", version=None)
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                result = _run_skill_refresh(args)
            self.assertEqual(result, 1)
            error_output = json.loads(output.getvalue())
            self.assertFalse(error_output.get("ok", True))
            self.assertEqual(
                error_output.get("error", {}).get("code"), "SKILL_NOT_AVAILABLE"
            )
        finally:
            sys.path = original_path


@dataclass
class _AgentCfg:
    name: str = "test-agent"
    default_channel: str = "console"


class _FakeAgentService:
    async def run_turn(self, message: Message, history: list[Message]) -> AgentResponse:
        del history
        return AgentResponse(
            text=f"ok:{message.body}",
            channel=message.channel,
            target=message.target,
            metadata={},
        )


class _FakeSessions:
    def __init__(self) -> None:
        self._resolved = SimpleNamespace(id="session-1")

    def resolve_session(self, **kwargs):  # noqa: ANN003
        del kwargs
        return self._resolved

    def append_message(self, **kwargs):  # noqa: ANN003
        del kwargs
        return


class _FakeApp:
    def __init__(self, tmp_path: Path) -> None:
        self._agent = _FakeAgentService()
        self.sessions = _FakeSessions()
        self.config = SimpleNamespace(
            agent=_AgentCfg(),
            runtime=SimpleNamespace(
                session_keep_recent_messages=20,
                session_max_compact_per_turn=100,
                session_summary_max_chars=8000,
                session_archive_enabled=True,
                session_archive_ref_limit=3,
                session_context_token_budget=321,
                session_context_chars_per_token=3.25,
                session_summary_enrichment_enabled=False,
                memory_enabled=False,
            ),
        )
        self.config_path = tmp_path / "config.json"
        self.storage_path = tmp_path / "state" / "openminion.db"

    def resolve_agent_profile(self, agent_id):  # noqa: ANN001
        del agent_id
        return SimpleNamespace(name="test-agent", default_channel="console")

    def resolve_agent_service(self, agent_name: str) -> _FakeAgentService:
        del agent_name
        return self._agent


def test_run_agent_forwards_session_context_budget_settings(tmp_path: Path) -> None:
    app = _FakeApp(tmp_path)
    args = Namespace(
        message="hello",
        target="chat",
        channel="console",
        agent_id=None,
        session_id="session-1",
        deliver=False,
        json=False,
    )
    session_context = mock.Mock()
    session_context.build_history.return_value = []
    memory_adapter = mock.Mock()
    memory_adapter.build_context.return_value = None
    mocked_context_service = mock.Mock(return_value=session_context)
    mocked_memory_root = mock.Mock(return_value=tmp_path / "memory")
    mocked_archive_root = mock.Mock(return_value=tmp_path / "archive")
    mocked_disabled_memory = mock.Mock(return_value=memory_adapter)

    with (
        mock.patch.dict(
            run_agent.__globals__,
            {
                "resolve_memory_root": mocked_memory_root,
                "resolve_session_archive_root": mocked_archive_root,
                "SessionContextService": mocked_context_service,
                "DisabledMemoryGatewayAdapter": mocked_disabled_memory,
            },
        ),
        redirect_stdout(io.StringIO()),
    ):
        code = run_agent(args, app)

    assert code == 0
    assert mocked_context_service.call_args is not None
    assert mocked_context_service.call_args.kwargs["token_budget"] == 321
    assert mocked_context_service.call_args.kwargs["chars_per_token"] == 3.25
    assert (
        mocked_context_service.call_args.kwargs["summary_enrichment_enabled"] is False
    )


def test_run_agent_skips_session_context_for_minimal_session_store(
    tmp_path: Path,
) -> None:
    app = _FakeApp(tmp_path)
    args = Namespace(
        message="hello",
        target="chat",
        channel="console",
        agent_id=None,
        session_id="session-1",
        deliver=False,
        json=False,
    )
    memory_adapter = mock.Mock()
    memory_adapter.build_context.return_value = None
    mocked_memory_root = mock.Mock(return_value=tmp_path / "memory")
    mocked_archive_root = mock.Mock(return_value=tmp_path / "archive")
    mocked_disabled_memory = mock.Mock(return_value=memory_adapter)

    with (
        mock.patch.dict(
            run_agent.__globals__,
            {
                "resolve_memory_root": mocked_memory_root,
                "resolve_session_archive_root": mocked_archive_root,
                "DisabledMemoryGatewayAdapter": mocked_disabled_memory,
            },
        ),
        redirect_stdout(io.StringIO()),
    ):
        code = run_agent(args, app)

    assert code == 0
