import io
import inspect
import json
import os
import re
import sqlite3
import sys
import tempfile
import time
import unittest
from argparse import Namespace
from contextlib import redirect_stdout
from pathlib import Path
from types import SimpleNamespace
from unittest import mock
from tests._csc_fixtures import _csc_install_default_agent


from openminion.cli.chat.approval import ChatApprovalState
from openminion.cli.chat.runner import ChatRunnerDeps, run_chat as run_chat_runner
from openminion.cli.commands import chat as chat_command
from openminion.base.config import ConfigManagerError, OpenMinionConfig, save_config
from openminion.modules.identity.models import (
    AgentProfile,
    PersonalitySpec,
    RiskSpec,
    RoleSpec,
    ToolPostureSpec,
)
from openminion.modules.identity.runtime.service import IdentityCtl
from openminion.modules.identity.storage.store import SQLiteIdentityStore
from openminion.modules.storage.runtime.migrations import migrate_database
from openminion.modules.storage.runtime.session_store import SessionStore
from openminion.modules.storage.runtime.sqlite import connect_database


_ANSI_ESCAPE_RE = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")


def _config(
    *,
    mode: str = "single-process",
    agent_name: str = "openminion",
    provider: str = "echo",
    demo_mode: bool = False,
):
    return SimpleNamespace(
        runtime=SimpleNamespace(
            process_mode=mode,
            daemon_auto_start=False,
            debug_enabled=True,
            debug_chat_enabled=True,
            demo_mode=demo_mode,
        ),
        agent=SimpleNamespace(name=agent_name, provider=provider),
        agents={agent_name: SimpleNamespace(name=agent_name, provider=provider)},
        default_agent=agent_name,
    )


def _chat_args(**overrides):
    payload = {
        "config": "test-configs.json",
        "agent": "ops",
        "session": "ops-chat",
        "quiet": False,
        "no_progress": True,
        "no_activity_indicator": False,
        "conversation": None,
        "resume": False,
        "reset_session": False,
        "session_name": None,
        "demo": False,
        "sync_identity": False,
        "stdin_one_shot": False,
        "verbose": False,
        "tools_verbose": False,
    }
    payload.update(overrides)
    return Namespace(**payload)


def _normalize_cli_output(text: str) -> str:
    return _ANSI_ESCAPE_RE.sub("", text).replace("\r\n", "\n").replace("\r", "\n")


class _TTYStringIO(io.StringIO):
    def isatty(self) -> bool:  # pragma: no cover - trivial
        return True


class _PipeStringIO(io.StringIO):
    def isatty(self) -> bool:  # pragma: no cover - trivial
        return False


class ChatCommandTests(unittest.TestCase):
    def setUp(self) -> None:
        current = sys.modules.get("openminion.cli.commands.chat")
        if current is not None:
            globals()["chat_command"] = current
        self._deprecation_env = mock.patch.dict(
            os.environ,
            {"OPENMINION_CHAT_NO_DEPRECATION": "1"},
            clear=False,
        )
        self._deprecation_env.start()

    def tearDown(self) -> None:
        self._deprecation_env.stop()

    def test_resolve_conversation_selection_prefers_explicit_and_env(self) -> None:
        args = _chat_args(conversation="fixed-conv")

        explicit = chat_command._resolve_conversation_selection(
            args,
            session_id="ops-chat",
            config_path="test-configs.json",
        )
        self.assertEqual(
            explicit, {"conversation_id": "fixed-conv", "source": "explicit"}
        )

        args = _chat_args(conversation=None)
        with mock.patch(
            "openminion.cli.commands.chat.resolve_environment_config",
            return_value={chat_command.OPENMINION_CONVERSATION_ID_ENV: "env-conv"},
        ):
            env_selected = chat_command._resolve_conversation_selection(
                args,
                session_id="ops-chat",
                config_path="test-configs.json",
            )
        self.assertEqual(env_selected, {"conversation_id": "env-conv", "source": "env"})

    def test_build_inbound_metadata_includes_cwd_and_bounded_recent_artifacts(
        self,
    ) -> None:
        payload = chat_command._build_inbound_metadata(
            conversation_id="conv-1",
            thread_id="thread-1",
            attach_id="attach-1",
            resume_requested=False,
            reset_requested=False,
            cwd="/tmp/openminion",
            recent_artifacts=[
                {
                    "ref": "older",
                    "path": "/tmp/older.txt",
                    "kind": "text",
                    "content": "ignored",
                },
                {
                    "ref": "latest",
                    "path": "/tmp/target.cpp",
                    "type": "code",
                    "body": "ignored",
                },
            ],
        )

        self.assertEqual(payload["cwd"], "/tmp/openminion")
        recent_artifacts = json.loads(payload["recent_artifacts"])
        self.assertEqual(
            recent_artifacts,
            [
                {"ref": "older", "path": "/tmp/older.txt", "kind": "text"},
                {"ref": "latest", "path": "/tmp/target.cpp", "kind": "code"},
            ],
        )

    def test_chat_runner_performs_identity_sync_when_requested(self) -> None:
        calls: list[dict[str, object]] = []
        roots = SimpleNamespace(
            home_root=Path("/tmp/home"), data_root=Path("/tmp/data")
        )
        config = _config()
        runtime_state = SimpleNamespace(quiet=False)
        deps = ChatRunnerDeps(
            resolve_chat_roots=lambda args: (Path("/tmp/config.json"), roots),
            load_config=lambda *args, **kwargs: config,
            inspect_chat_onboarding=lambda args: (
                SimpleNamespace(
                    action=SimpleNamespace(value="ok"),
                    state=SimpleNamespace(value="ok"),
                ),
                None,
                None,
            ),
            print_onboarding_fail_fast=lambda status: 2,
            run_inline_setup_for_chat=lambda args: 1,
            materialize_demo_config_for_chat=lambda args, *, roots, config_path: Path(
                "/tmp/demo.json"
            ),
            normalize_chat_args=lambda args, config: SimpleNamespace(
                session_id="ops-chat",
                session_name="",
                quiet=False,
                show_progress=False,
            ),
            perform_identity_sync=lambda **kwargs: calls.append(dict(kwargs)),
            should_suppress_console_info_logs=lambda **kwargs: False,
            set_quiet_log_level=lambda: None,
            init_runtime_state=lambda args, config: (runtime_state, None),
            mark_stale_cli_sessions=lambda **kwargs: 0,
            resolve_initial_chat_agent_id=lambda args, *, config, session_id: (
                "ops",
                {},
            ),
            resolve_lifecycle_state=lambda *args, **kwargs: (
                {"source": "fresh"},
                "conv-1",
                "thread-1",
                "attach-1",
                {},
            ),
            session_profile_mismatch_message=lambda **kwargs: "",
            print_chat_ready_banner=lambda **kwargs: None,
            print_agent_resolution_notice=lambda **kwargs: None,
            print_stale_session_notice=lambda **kwargs: None,
            print_first_session_tip_if_requested=lambda args: None,
            get_session_record=lambda **kwargs: None,
            emit_session_open_events=lambda **kwargs: None,
            set_session_name_if_missing=lambda **kwargs: False,
            handle_chat_command=lambda **kwargs: SimpleNamespace(handled=False),
            handle_repl_command=lambda **kwargs: {},
            local_human_post_block_reason=lambda **kwargs: "",
            build_lifecycle_payload=lambda **kwargs: {},
            build_inbound_metadata=lambda **kwargs: {},
            build_turn_idempotency_key=lambda **kwargs: "turn-key",
            build_run_profile_override_payload=lambda args: {},
            execute_turn=lambda **kwargs: {"stop": True},
            maybe_auto_name_session=lambda **kwargs: False,
            emit_session_event_safe=lambda **kwargs: None,
            close_runtime=lambda state: None,
            chat_input_prompt=lambda **kwargs: "[ops-chat|ops] you> ",
            conversation_env_name=chat_command.OPENMINION_CONVERSATION_ID_ENV,
            resolve_environment_config=lambda: {},
            stale_timeout_default=10,
            turn_timeout_default=90.0,
            turn_max_attempts_default=2,
        )

        args = _chat_args(sync_identity=True)
        with mock.patch("builtins.input", side_effect=EOFError):
            code = run_chat_runner(args, deps=deps)

        self.assertEqual(code, 0)
        self.assertEqual(len(calls), 1)
        self.assertTrue(calls[0]["enabled"])
        self.assertIs(calls[0]["config"], config)
        self.assertIs(calls[0]["roots"], roots)

    def test_chat_runner_passes_real_cwd_and_recent_artifacts_into_inbound_metadata(
        self,
    ) -> None:
        captured_metadata_calls: list[dict[str, object]] = []
        roots = SimpleNamespace(
            home_root=Path("/tmp/home"), data_root=Path("/tmp/data")
        )
        config = _config()
        runtime_state = SimpleNamespace(quiet=False, transport="in-process", mode="hot")
        deps = ChatRunnerDeps(
            resolve_chat_roots=lambda args: (Path("/tmp/config.json"), roots),
            load_config=lambda *args, **kwargs: config,
            inspect_chat_onboarding=lambda args: (
                SimpleNamespace(
                    action=SimpleNamespace(value="ok"),
                    state=SimpleNamespace(value="ok"),
                ),
                None,
                None,
            ),
            print_onboarding_fail_fast=lambda status: 2,
            run_inline_setup_for_chat=lambda args: 1,
            materialize_demo_config_for_chat=lambda args, *, roots, config_path: Path(
                "/tmp/demo.json"
            ),
            normalize_chat_args=lambda args, config: SimpleNamespace(
                session_id="ops-chat",
                session_name="",
                quiet=False,
                show_progress=False,
            ),
            perform_identity_sync=lambda **kwargs: None,
            should_suppress_console_info_logs=lambda **kwargs: False,
            set_quiet_log_level=lambda: None,
            init_runtime_state=lambda args, config: (runtime_state, None),
            mark_stale_cli_sessions=lambda **kwargs: 0,
            resolve_initial_chat_agent_id=lambda args, *, config, session_id: (
                "ops",
                {},
            ),
            resolve_lifecycle_state=lambda *args, **kwargs: (
                {"source": "fresh"},
                "conv-1",
                "thread-1",
                "attach-1",
                {},
            ),
            session_profile_mismatch_message=lambda **kwargs: "",
            print_chat_ready_banner=lambda **kwargs: None,
            print_agent_resolution_notice=lambda **kwargs: None,
            print_stale_session_notice=lambda **kwargs: None,
            print_first_session_tip_if_requested=lambda args: None,
            get_session_record=lambda **kwargs: None,
            emit_session_open_events=lambda **kwargs: None,
            set_session_name_if_missing=lambda **kwargs: False,
            handle_chat_command=lambda **kwargs: SimpleNamespace(handled=False),
            handle_repl_command=lambda **kwargs: {},
            local_human_post_block_reason=lambda **kwargs: "",
            build_lifecycle_payload=lambda **kwargs: {},
            build_inbound_metadata=lambda **kwargs: (
                captured_metadata_calls.append(dict(kwargs)) or {}
            ),
            build_turn_idempotency_key=lambda **kwargs: "turn-key",
            build_run_profile_override_payload=lambda args: {},
            execute_turn=mock.Mock(
                side_effect=[
                    {
                        "retry": False,
                        "last_artifacts": [
                            {
                                "ref": "artifact:previous",
                                "path": "/tmp/target.cpp",
                                "type": "code",
                            }
                        ],
                        "last_turn_debug": {},
                    },
                    {"stop": True},
                ]
            ),
            maybe_auto_name_session=lambda **kwargs: False,
            emit_session_event_safe=lambda **kwargs: None,
            close_runtime=lambda state: None,
            chat_input_prompt=lambda **kwargs: "[ops-chat|ops] you> ",
            conversation_env_name=chat_command.OPENMINION_CONVERSATION_ID_ENV,
            resolve_environment_config=lambda: {},
            stale_timeout_default=10,
            turn_timeout_default=90.0,
            turn_max_attempts_default=2,
        )

        args = _chat_args()
        with tempfile.TemporaryDirectory() as tmpdir:
            old_cwd = os.getcwd()
            resolved_tmpdir = str(Path(tmpdir).resolve(strict=False))
            try:
                os.chdir(tmpdir)
                with mock.patch(
                    "builtins.input",
                    side_effect=["write code", "provide target path", EOFError],
                ):
                    code = run_chat_runner(args, deps=deps)
            finally:
                os.chdir(old_cwd)

        self.assertEqual(code, 0)
        self.assertEqual(len(captured_metadata_calls), 2)
        self.assertEqual(captured_metadata_calls[0]["cwd"], resolved_tmpdir)
        self.assertEqual(captured_metadata_calls[0]["recent_artifacts"], [])
        self.assertEqual(captured_metadata_calls[1]["cwd"], resolved_tmpdir)
        self.assertEqual(
            captured_metadata_calls[1]["recent_artifacts"],
            [
                {
                    "ref": "artifact:previous",
                    "path": "/tmp/target.cpp",
                    "type": "code",
                }
            ],
        )

    def test_resolve_conversation_selection_reuses_latest_and_force_fresh_bypasses(
        self,
    ) -> None:
        args = _chat_args()

        with mock.patch(
            "openminion.cli.commands.chat._latest_session_conversation_id",
            return_value="conv-prev",
        ):
            reused = chat_command._resolve_conversation_selection(
                args,
                session_id="ops-chat",
                config_path="test-configs.json",
            )
            fresh = chat_command._resolve_conversation_selection(
                args,
                session_id="ops-chat",
                config_path="test-configs.json",
                force_fresh=True,
            )

        self.assertEqual(
            reused, {"conversation_id": "conv-prev", "source": "session_reuse"}
        )
        self.assertEqual(fresh["source"], "force_fresh")
        self.assertTrue(fresh["conversation_id"].startswith("conv-"))
        self.assertNotEqual(fresh["conversation_id"], "conv-prev")

    def test_chat_input_prompt_plain_includes_session_and_agent(self) -> None:
        with mock.patch.object(sys.stdout, "isatty", return_value=False):
            prompt = chat_command.chat_input_prompt(
                session_id="ops-chat", agent_id="ops"
            )
        self.assertEqual(prompt, "[ops-chat|ops] you> ")

    def test_chat_input_prompt_is_colored_by_default_on_tty(self) -> None:
        with mock.patch.object(sys.stdout, "isatty", return_value=True):
            with mock.patch.dict(os.environ, {"NO_COLOR": ""}, clear=False):
                prompt = chat_command.chat_input_prompt(
                    session_id="ops-chat", agent_id="ops"
                )
        self.assertIn("\033[", prompt)
        self.assertIn("ops-chat", prompt)
        self.assertIn("ops", prompt)

    def test_chat_input_prompt_explicit_off_disables_tty_color(self) -> None:
        with mock.patch.object(sys.stdout, "isatty", return_value=True):
            with mock.patch.dict(os.environ, {"OPENMINION_COLOR_PROMPT": "0"}):
                prompt = chat_command.chat_input_prompt(
                    session_id="ops-chat", agent_id="ops"
                )
        self.assertEqual(prompt, "[ops-chat|ops] you> ")

    def test_readline_prompt_wraps_ansi_sequences_as_nonprinting(self) -> None:
        with mock.patch("openminion.cli.chat.runner._READLINE_AVAILABLE", True):
            with mock.patch("openminion.cli.chat.runner._READLINE_USES_LIBEDIT", False):
                display_prompt, input_prompt = (
                    chat_command.chat_runner._split_input_prompt_for_readline(
                        "\033[1;36m[ops-chat|ops]\033[0m \033[1;34myou\033[0m\033[2m>\033[0m "
                    )
                )
        self.assertEqual(display_prompt, "")
        self.assertIn("\001\033[1;36m\002", input_prompt)
        self.assertIn("\001\033[0m\002", input_prompt)
        self.assertIn("[ops-chat|ops]", input_prompt)

    def test_readline_prompt_strips_ansi_on_libedit(self) -> None:
        with mock.patch("openminion.cli.chat.runner._READLINE_AVAILABLE", True):
            with mock.patch("openminion.cli.chat.runner._READLINE_USES_LIBEDIT", True):
                display_prompt, input_prompt = (
                    chat_command.chat_runner._split_input_prompt_for_readline(
                        "\033[1;36m[ops-chat|ops]\033[0m \033[1;34myou\033[0m\033[2m>\033[0m "
                    )
                )
        self.assertEqual(display_prompt, "")
        self.assertEqual(input_prompt, "[ops-chat|ops] you> ")

    def test_readline_prompt_leaves_plain_prompt_unchanged(self) -> None:
        prompt = "[ops-chat|ops] you> "
        display_prompt, input_prompt = (
            chat_command.chat_runner._split_input_prompt_for_readline(prompt)
        )
        self.assertEqual(display_prompt, "")
        self.assertEqual(input_prompt, prompt)

    def test_print_assistant_text_formats_with_context(self) -> None:
        buf = io.StringIO()
        with redirect_stdout(buf):
            chat_command.print_assistant_text(
                text="ops: hello\nstill here",
                session_id="ops-chat",
                agent_id="ops",
            )
        lines = buf.getvalue().splitlines()
        self.assertEqual(lines[0], "[ops-chat|ops] ops: hello")
        self.assertTrue(lines[1].startswith(" " * (len("[ops-chat|ops] ops:") + 1)))
        self.assertTrue(lines[1].endswith("still here"))

    def test_print_assistant_text_uses_agent_when_sender_missing(self) -> None:
        buf = io.StringIO()
        with redirect_stdout(buf):
            chat_command.print_assistant_text(
                text="hello there",
                session_id="ops-chat",
                agent_id="ops",
            )
        self.assertEqual(buf.getvalue().strip(), "[ops-chat|ops] ops: hello there")

    def test_print_assistant_text_colors_body_when_terminal_color_enabled(self) -> None:
        buf = io.StringIO()
        with (
            redirect_stdout(buf),
            mock.patch(
                "openminion.cli.chat.ui._terminal_supports_color", return_value=True
            ),
        ):
            chat_command.print_assistant_text(
                text="ops: hello\nstill here",
                session_id="ops-chat",
                agent_id="ops",
            )
        rendered = buf.getvalue()
        self.assertIn("\033[", rendered)
        self.assertIn("hello", rendered)
        self.assertIn("still here", rendered)

    def test_chat_help_lines_include_expected_commands(self) -> None:
        from openminion.cli.chat.ui import chat_help_lines

        lines = chat_help_lines()
        rendered = "\n".join(lines)

        self.assertIn("Chat commands:", rendered)
        self.assertIn("/ or /help or /?", rendered)
        self.assertIn("/status", rendered)
        self.assertIn("/clear", rendered)
        self.assertIn("/agent <id>", rendered)
        self.assertIn("/session <id>", rendered)
        self.assertIn("/new", rendered)
        self.assertIn("/tools", rendered)
        self.assertIn("/artifacts", rendered)
        self.assertIn("/debug", rendered)
        self.assertIn("/trust <category>", rendered)
        self.assertIn("/untrust <category>", rendered)
        self.assertIn("/grants", rendered)
        self.assertIn("/exit", rendered)

    def test_debug_command_prints_context_snapshot(self) -> None:
        args = Namespace(
            config="test-configs.json",
            agent="ops",
            session="ops-chat",
            quiet=False,
            no_progress=True,
        )

        with (
            mock.patch(
                "openminion.cli.commands.chat.load_config",
                return_value=_config(mode="single-process"),
            ),
            mock.patch(
                "openminion.cli.chat.commands._print_debug_context"
            ) as print_debug,
            mock.patch("builtins.input", side_effect=["/debug", "/exit"]),
        ):
            buf = io.StringIO()
            with redirect_stdout(buf):
                code = chat_command.run_chat(args)

        self.assertEqual(code, 0)
        self.assertIn(
            "chat ready agent=ops session=ops-chat transport=in-process", buf.getvalue()
        )
        print_debug.assert_called_once()

    def test_debug_command_reports_disabled_when_chat_debug_off(self) -> None:
        args = Namespace(
            config="test-configs.json",
            agent="ops",
            session="ops-chat",
            quiet=False,
            no_progress=True,
        )
        disabled_config = SimpleNamespace(
            runtime=SimpleNamespace(
                process_mode="single-process",
                daemon_auto_start=False,
                debug_enabled=False,
                debug_chat_enabled=True,
            ),
            agent=SimpleNamespace(name="ops"),
        )

        with (
            mock.patch(
                "openminion.cli.commands.chat.load_config", return_value=disabled_config
            ),
            mock.patch(
                "openminion.cli.chat.commands._print_debug_context"
            ) as print_debug,
            mock.patch("builtins.input", side_effect=["/debug", "/exit"]),
        ):
            buf = io.StringIO()
            with redirect_stdout(buf):
                code = chat_command.run_chat(args)

        self.assertEqual(code, 0)
        output = buf.getvalue()
        self.assertIn("[chat] /debug is disabled by config", output)
        print_debug.assert_not_called()

    def test_debug_context_identity_snapshot_for_identityctl_profile(self) -> None:
        from openminion.cli.chat.commands.context import _get_identity_debug_info

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            identity_db = root / "identity" / "identity.db"
            _write_identity_profile(
                identity_db,
                agent_id="ops",
                mission="Debug context identity mission.",
                tone="direct",
                meta={
                    "bundle_imported": True,
                    "bundle_fingerprint": "chat-fingerprint-001",
                },
            )

            info = _get_identity_debug_info(
                config=SimpleNamespace(identity={"db_path": str(identity_db)}),
                agent_id="ops",
            )

            expected = {
                "profile_present": True,
                "identity_db_path": str(identity_db.resolve()),
                "profile_version": info["profile_version"],
                "render_version": info["render_version"],
                "profile_revision": 1,
                "bundle_imported": True,
                "bundle_fingerprint": "chat-fingerprint-001",
                "meta_source": "",
                "source_classification": "legacy-bundle",
                "source_refreshable_by_bundle": True,
                "errors": [],
                "warnings": [],
            }
            self.assertEqual(info, expected)

    def test_debug_context_identity_surfaces_source_provenance(self) -> None:
        from openminion.cli.chat.commands.context import _get_identity_debug_info

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            identity_db = root / "identity" / "identity.db"
            _write_identity_profile(
                identity_db,
                agent_id="ops",
                mission="Debug diagnostics baseline mission.",
                tone="clear",
                meta={
                    "bundle_imported": True,
                    "bundle_fingerprint": "chat-fingerprint-baseline-001",
                    "source": "yaml",
                },
            )

            info = _get_identity_debug_info(
                config=SimpleNamespace(identity={"db_path": str(identity_db)}),
                agent_id="ops",
            )

            self.assertTrue(info["profile_present"])
            self.assertTrue(info["bundle_imported"])
            self.assertEqual(
                info["bundle_fingerprint"], "chat-fingerprint-baseline-001"
            )
            self.assertEqual(info["meta_source"], "yaml")
            self.assertEqual(info["source_classification"], "yaml")
            self.assertFalse(info["source_refreshable_by_bundle"])

    def test_debug_context_identity_prefers_db_path_over_legacy_alias(self) -> None:
        from openminion.cli.chat.commands.context import _get_identity_debug_info

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            primary_db = root / "primary" / "identity.db"
            legacy_db = root / "legacy" / "identity.db"
            _write_identity_profile(
                primary_db,
                agent_id="ops",
                mission="Primary identity db mission.",
                tone="clear",
            )

            info = _get_identity_debug_info(
                config=SimpleNamespace(
                    identity={
                        "db_path": str(primary_db),
                        "root": str(legacy_db),
                    }
                ),
                agent_id="ops",
            )

            self.assertTrue(info["profile_present"])
            self.assertEqual(info["identity_db_path"], str(primary_db.resolve()))

    def test_chat_session_identity_db_path_prefers_split_field(self) -> None:
        from openminion.cli.chat.commands.session import _resolve_identity_db_path

        config = SimpleNamespace(
            identity=SimpleNamespace(
                db_path="/tmp/identity-new.db",
                root="/tmp/identity-legacy.db",
            )
        )
        self.assertEqual(_resolve_identity_db_path(config), "/tmp/identity-new.db")

    def test_inproc_turn_failure_does_not_exit_chat(self) -> None:
        args = Namespace(
            config="test-configs.json",
            agent="ops",
            session="ops-chat",
            quiet=False,
            no_progress=True,
        )
        runtime = mock.Mock()

        with (
            mock.patch(
                "openminion.cli.commands.chat.load_config",
                return_value=_config(mode="single-process"),
            ),
            mock.patch(
                "openminion.cli.commands.chat.APIRuntime.from_config_path",
                return_value=runtime,
            ),
            mock.patch(
                "openminion.cli.commands.chat.run_turn",
                side_effect=RuntimeError("provider failed"),
            ),
            mock.patch("builtins.input", side_effect=["hello", "/exit"]),
        ):
            buf = io.StringIO()
            with redirect_stdout(buf):
                code = chat_command.run_chat(args)

        self.assertEqual(code, 0)
        self.assertIn(
            "chat ready agent=ops session=ops-chat transport=in-process", buf.getvalue()
        )
        self.assertIn("[chat] turn failed: provider failed", buf.getvalue())
        runtime.close.assert_called_once()

    def test_inproc_empty_body_finalization_failure_surfaces_friendly_message(
        self,
    ) -> None:
        args = Namespace(
            config="test-configs.json",
            agent="ops",
            session="ops-chat",
            quiet=False,
            no_progress=True,
        )
        runtime = mock.Mock()

        with (
            mock.patch(
                "openminion.cli.commands.chat.load_config",
                return_value=_config(mode="single-process"),
            ),
            mock.patch(
                "openminion.cli.commands.chat.APIRuntime.from_config_path",
                return_value=runtime,
            ),
            mock.patch(
                "openminion.cli.commands.chat.run_turn",
                return_value={
                    "body": "",
                    "metadata": {
                        "tool_loop_termination_reason": (
                            "finalization_contract_missing"
                        )
                    },
                },
            ),
            mock.patch("builtins.input", side_effect=["hello", "/exit"]),
        ):
            buf = io.StringIO()
            with redirect_stdout(buf):
                code = chat_command.run_chat(args)

        self.assertEqual(code, 0)
        output = buf.getvalue()
        self.assertIn(
            "[chat] turn failed: The model ended the turn without the required "
            "completion contract. Please try again.",
            output,
        )
        self.assertNotIn("[ops-chat|ops] ops:", output)
        runtime.close.assert_called_once()

    def test_inproc_empty_body_pae_idle_noop_does_not_print_failure_message(
        self,
    ) -> None:
        args = Namespace(
            config="test-configs.json",
            agent="ops",
            session="ops-chat",
            quiet=False,
            no_progress=True,
        )
        runtime = mock.Mock()

        with (
            mock.patch(
                "openminion.cli.commands.chat.load_config",
                return_value=_config(mode="single-process"),
            ),
            mock.patch(
                "openminion.cli.commands.chat.APIRuntime.from_config_path",
                return_value=runtime,
            ),
            mock.patch(
                "openminion.cli.commands.chat.run_turn",
                return_value={
                    "body": "",
                    "metadata": {"pae_idle_tick_noop": "true"},
                },
            ),
            mock.patch("builtins.input", side_effect=["hello", "/exit"]),
        ):
            buf = io.StringIO()
            with redirect_stdout(buf):
                code = chat_command.run_chat(args)

        self.assertEqual(code, 0)
        self.assertNotIn("[chat] turn failed:", buf.getvalue())
        runtime.close.assert_called_once()

    def test_inproc_chat_renders_phase_status_updates_when_tty_progress_enabled(
        self,
    ) -> None:
        from openminion.modules.brain.diagnostics.status import PhaseStatus

        args = Namespace(
            config="test-configs.json",
            agent="ops",
            session="ops-chat",
            quiet=False,
            no_progress=False,
        )
        runtime = mock.Mock()

        def _run_turn(**kwargs):
            progress_callback = kwargs.get("progress_callback")
            self.assertIsNotNone(progress_callback)
            progress_callback(
                PhaseStatus(
                    trace_id="trace-chat-status",
                    status_key="analyzing",
                    label="Analyzing request...",
                )
            )
            progress_callback(
                PhaseStatus(
                    trace_id="trace-chat-status",
                    status_key="executing",
                    label="Executing step 1/2...",
                    step_index=1,
                    step_total=2,
                )
            )
            progress_callback(
                PhaseStatus(
                    trace_id="trace-chat-status",
                    status_key="evaluating_completion",
                    label="Evaluating results...",
                )
            )
            return {"body": "ops: progress ok", "metadata": {}}

        with (
            mock.patch(
                "openminion.cli.commands.chat.load_config",
                return_value=_config(mode="single-process"),
            ),
            mock.patch(
                "openminion.cli.commands.chat.APIRuntime.from_config_path",
                return_value=runtime,
            ),
            mock.patch("openminion.cli.commands.chat.run_turn", side_effect=_run_turn),
            mock.patch("builtins.input", side_effect=["hello", "/exit"]),
        ):
            buf = _TTYStringIO()
            with redirect_stdout(buf):
                code = chat_command.run_chat(args)

        self.assertEqual(code, 0)
        output = buf.getvalue()
        self.assertIn("Analyzing request...", output)
        self.assertIn("Executing step 1/2...", output)
        self.assertIn("Evaluating results...", output)
        self.assertIn("[ops-chat|ops] ops: progress ok", _normalize_cli_output(output))
        runtime.close.assert_called_once()

    def test_inproc_chat_accepts_adaptive_status_payload_without_cli_specific_logic(
        self,
    ) -> None:
        from openminion.modules.brain.diagnostics.status import PhaseStatus

        args = Namespace(
            config="test-configs.json",
            agent="ops",
            session="ops-chat",
            quiet=False,
            no_progress=False,
        )
        runtime = mock.Mock()

        def _run_turn(**kwargs):
            progress_callback = kwargs.get("progress_callback")
            self.assertIsNotNone(progress_callback)
            progress_callback(
                PhaseStatus(
                    trace_id="trace-chat-adaptive",
                    status_key="executing",
                    label="Executing step...",
                    mode="act_adaptive",
                    mode_label="adaptive loop",
                    detail_text="checking previous tool output",
                )
            )
            return {"body": "ops: adaptive ok", "metadata": {}}

        with (
            mock.patch(
                "openminion.cli.commands.chat.load_config",
                return_value=_config(mode="single-process"),
            ),
            mock.patch(
                "openminion.cli.commands.chat.APIRuntime.from_config_path",
                return_value=runtime,
            ),
            mock.patch("openminion.cli.commands.chat.run_turn", side_effect=_run_turn),
            mock.patch("builtins.input", side_effect=["hello", "/exit"]),
        ):
            buf = _TTYStringIO()
            with redirect_stdout(buf):
                code = chat_command.run_chat(args)

        self.assertEqual(code, 0)
        output = buf.getvalue()
        self.assertIn("Executing step...", output)
        self.assertIn("adaptive loop", output)
        self.assertNotIn("adaptive.profile", output)
        self.assertNotIn("adaptive.termination_reason", output)
        self.assertIn("[ops-chat|ops] ops: adaptive ok", _normalize_cli_output(output))
        runtime.close.assert_called_once()

    def test_daemon_chat_renders_streamed_phase_status_updates_when_tty_progress_enabled(
        self,
    ) -> None:
        from openminion.cli.transport.daemon_client import DaemonStreamEvent

        args = Namespace(
            config="test-configs.json",
            agent="ops",
            session="ops-chat",
            quiet=False,
            no_progress=False,
        )
        endpoint = SimpleNamespace(host="127.0.0.1", port=18789)

        def _daemon_request(*, path, **kwargs):
            del kwargs
            if path == "/v1/turn/stream":
                raise AssertionError(
                    "TTY progress path should use daemon_stream_request"
                )
            return 200, {"ok": True}

        def _daemon_stream_request(*, on_event=None, **kwargs):
            del kwargs
            assert on_event is not None
            on_event(
                DaemonStreamEvent(
                    event="chunk",
                    data={
                        "kind": "status",
                        "data": {
                            "trace_id": "trace-daemon-status",
                            "status_key": "analyzing",
                            "label": "Analyzing request...",
                        },
                    },
                )
            )
            on_event(
                DaemonStreamEvent(
                    event="chunk",
                    data={
                        "kind": "status",
                        "data": {
                            "trace_id": "trace-daemon-status",
                            "status_key": "planning",
                            "label": "Planning steps...",
                        },
                    },
                )
            )
            on_event(
                DaemonStreamEvent(
                    event="chunk",
                    data={
                        "kind": "status",
                        "data": {
                            "trace_id": "trace-daemon-status",
                            "status_key": "completed",
                            "label": "Completed.",
                            "detail_text": "ops: daemon ok",
                            "terminal": True,
                        },
                    },
                )
            )
            return 200, {
                "ok": True,
                "turn": {
                    "trace_id": "trace-daemon-status",
                    "final_text": "ops: daemon ok",
                },
            }

        with (
            mock.patch(
                "openminion.cli.commands.chat.load_config",
                return_value=_config(mode="daemon"),
            ),
            mock.patch(
                "openminion.cli.commands.chat.ensure_daemon_running",
                return_value=endpoint,
            ),
            mock.patch(
                "openminion.cli.commands.chat.daemon_request",
                side_effect=_daemon_request,
            ) as daemon_request,
            mock.patch(
                "openminion.cli.commands.chat.daemon_stream_request",
                side_effect=_daemon_stream_request,
            ) as daemon_stream_request,
            mock.patch("builtins.input", side_effect=["hello", "/exit"]),
        ):
            buf = _TTYStringIO()
            with redirect_stdout(buf):
                code = chat_command.run_chat(args)

        self.assertEqual(code, 0)
        output = buf.getvalue()
        self.assertIn("Analyzing request...", output)
        self.assertIn("Planning steps...", output)
        self.assertIn("[ops-chat|ops] ops: daemon ok", _normalize_cli_output(output))
        self.assertNotIn("Completed. ops: daemon ok", output)
        daemon_stream_request.assert_called_once()
        turn_calls = [
            call
            for call in daemon_request.call_args_list
            if call.kwargs.get("path") == "/v1/turn/stream"
        ]
        self.assertEqual(turn_calls, [])

    def test_chat_no_progress_keeps_legacy_daemon_json_path(self) -> None:
        args = Namespace(
            config="test-configs.json",
            agent="ops",
            session="ops-chat",
            quiet=False,
            no_progress=True,
        )
        endpoint = SimpleNamespace(host="127.0.0.1", port=18789)

        def _daemon_request(*, path, **kwargs):
            del kwargs
            if path == "/v1/turn/stream":
                return 200, {
                    "ok": True,
                    "turn": {
                        "trace_id": "trace-daemon-json",
                        "final_text": "ops: json ok",
                    },
                }
            return 200, {"ok": True}

        with (
            mock.patch(
                "openminion.cli.commands.chat.load_config",
                return_value=_config(mode="daemon"),
            ),
            mock.patch(
                "openminion.cli.commands.chat.ensure_daemon_running",
                return_value=endpoint,
            ),
            mock.patch(
                "openminion.cli.commands.chat.daemon_request",
                side_effect=_daemon_request,
            ) as daemon_request,
            mock.patch(
                "openminion.cli.commands.chat.daemon_stream_request"
            ) as daemon_stream_request,
            mock.patch("builtins.input", side_effect=["hello", "/exit"]),
        ):
            buf = io.StringIO()
            with redirect_stdout(buf):
                code = chat_command.run_chat(args)

        self.assertEqual(code, 0)
        output = buf.getvalue()
        self.assertIn("[ops-chat|ops] ops: json ok", _normalize_cli_output(output))
        self.assertNotIn("Analyzing request...", output)
        daemon_stream_request.assert_not_called()
        turn_calls = [
            call
            for call in daemon_request.call_args_list
            if call.kwargs.get("path") == "/v1/turn/stream"
        ]
        self.assertEqual(len(turn_calls), 1)

    def test_inproc_chat_waiting_for_user_progress_does_not_inline_reply_text(
        self,
    ) -> None:
        from openminion.modules.brain.diagnostics.status import PhaseStatus

        args = Namespace(
            config="test-configs.json",
            agent="ops",
            session="ops-chat",
            quiet=False,
            no_progress=False,
        )
        runtime = mock.Mock()
        clarification = "Could you clarify what you'd like me to do?"

        def _run_turn(**kwargs):
            progress_callback = kwargs.get("progress_callback")
            self.assertIsNotNone(progress_callback)
            progress_callback(
                PhaseStatus(
                    trace_id="trace-chat-waiting",
                    status_key="waiting_for_user",
                    label="Waiting for your reply...",
                    detail_text=clarification,
                )
            )
            return {"body": f"ops: {clarification}", "metadata": {}}

        with (
            mock.patch(
                "openminion.cli.commands.chat.load_config",
                return_value=_config(mode="single-process"),
            ),
            mock.patch(
                "openminion.cli.commands.chat.APIRuntime.from_config_path",
                return_value=runtime,
            ),
            mock.patch("openminion.cli.commands.chat.run_turn", side_effect=_run_turn),
            mock.patch("builtins.input", side_effect=["hello", "/exit"]),
        ):
            buf = _TTYStringIO()
            with redirect_stdout(buf):
                code = chat_command.run_chat(args)

        self.assertEqual(code, 0)
        output = buf.getvalue()
        self.assertIn("Waiting for your reply...", output)
        self.assertNotIn(f"Waiting for your reply... {clarification}", output)
        self.assertIn(
            f"[ops-chat|ops] ops: {clarification}", _normalize_cli_output(output)
        )
        runtime.close.assert_called_once()

    def test_daemon_chat_waiting_for_user_progress_does_not_inline_reply_text(
        self,
    ) -> None:
        from openminion.cli.transport.daemon_client import DaemonStreamEvent

        args = Namespace(
            config="test-configs.json",
            agent="ops",
            session="ops-chat",
            quiet=False,
            no_progress=False,
        )
        endpoint = SimpleNamespace(host="127.0.0.1", port=18789)
        clarification = "Could you clarify what you'd like me to do?"

        def _daemon_request(*, path, **kwargs):
            del kwargs
            if path == "/v1/turn/stream":
                raise AssertionError(
                    "TTY progress path should use daemon_stream_request"
                )
            return 200, {"ok": True}

        def _daemon_stream_request(*, on_event=None, **kwargs):
            del kwargs
            assert on_event is not None
            on_event(
                DaemonStreamEvent(
                    event="chunk",
                    data={
                        "kind": "status",
                        "data": {
                            "trace_id": "trace-daemon-waiting",
                            "status_key": "waiting_for_user",
                            "label": "Waiting for your reply...",
                            "detail_text": clarification,
                        },
                    },
                )
            )
            return 200, {
                "ok": True,
                "turn": {
                    "trace_id": "trace-daemon-waiting",
                    "final_text": f"ops: {clarification}",
                },
            }

        with (
            mock.patch(
                "openminion.cli.commands.chat.load_config",
                return_value=_config(mode="daemon"),
            ),
            mock.patch(
                "openminion.cli.commands.chat.ensure_daemon_running",
                return_value=endpoint,
            ),
            mock.patch(
                "openminion.cli.commands.chat.daemon_request",
                side_effect=_daemon_request,
            ),
            mock.patch(
                "openminion.cli.commands.chat.daemon_stream_request",
                side_effect=_daemon_stream_request,
            ),
            mock.patch("builtins.input", side_effect=["hello", "/exit"]),
        ):
            buf = _TTYStringIO()
            with redirect_stdout(buf):
                code = chat_command.run_chat(args)

        self.assertEqual(code, 0)
        output = buf.getvalue()
        self.assertIn("Waiting for your reply...", output)
        self.assertNotIn(f"Waiting for your reply... {clarification}", output)
        self.assertIn(
            f"[ops-chat|ops] ops: {clarification}", _normalize_cli_output(output)
        )

    def test_chat_non_tty_still_attaches_phase_status_callback(self) -> None:
        args = Namespace(
            config="test-configs.json",
            agent="ops",
            session="ops-chat",
            quiet=False,
            no_progress=False,
        )
        runtime = mock.Mock()
        seen_callback: list[object] = []

        def _run_turn(**kwargs):
            # callback IS attached now (was None pre-fix).
            seen_callback.append(kwargs.get("progress_callback"))
            return {"body": "ops: plain tty-off", "metadata": {}}

        with (
            mock.patch(
                "openminion.cli.commands.chat.load_config",
                return_value=_config(mode="single-process"),
            ),
            mock.patch(
                "openminion.cli.commands.chat.APIRuntime.from_config_path",
                return_value=runtime,
            ),
            mock.patch("openminion.cli.commands.chat.run_turn", side_effect=_run_turn),
            mock.patch("builtins.input", side_effect=["hello", "/exit"]),
        ):
            buf = io.StringIO()
            with redirect_stdout(buf):
                code = chat_command.run_chat(args)

        self.assertEqual(code, 0)
        self.assertIn(
            "[ops-chat|ops] ops: plain tty-off",
            _normalize_cli_output(buf.getvalue()),
        )
        # CPC-01 contract: callback is attached even in non-TTY.
        self.assertEqual(len(seen_callback), 1)
        self.assertIsNotNone(
            seen_callback[0],
            "CPC-01: phase-status callback must be attached even when "
            "stdout is non-TTY so tool-call notes are visible inline",
        )
        runtime.close.assert_called_once()

    def test_chat_tty_progress_suppresses_console_info_logs_by_default(self) -> None:
        args = Namespace(
            config="test-configs.json",
            agent="ops",
            session="ops-chat",
            quiet=False,
            no_progress=False,
        )
        runtime = mock.Mock()

        with (
            mock.patch(
                "openminion.cli.commands.chat.load_config",
                return_value=_config(mode="single-process"),
            ),
            mock.patch(
                "openminion.cli.commands.chat.APIRuntime.from_config_path",
                return_value=runtime,
            ),
            mock.patch(
                "openminion.cli.commands.chat.run_turn",
                return_value={"body": "ops: tty-progress", "metadata": {}},
            ),
            mock.patch(
                "openminion.cli.commands.chat.set_quiet_log_level"
            ) as quiet_logs,
            mock.patch("openminion.cli.commands.chat.has_tty", return_value=True),
            mock.patch("builtins.input", side_effect=["hello", "/exit"]),
        ):
            buf = _TTYStringIO()
            with redirect_stdout(buf):
                code = chat_command.run_chat(args)

        self.assertEqual(code, 0)
        quiet_logs.assert_called()

    def test_chat_no_progress_does_not_auto_suppress_console_info_logs(self) -> None:
        args = Namespace(
            config="test-configs.json",
            agent="ops",
            session="ops-chat",
            quiet=False,
            no_progress=True,
        )
        runtime = mock.Mock()

        with (
            mock.patch(
                "openminion.cli.commands.chat.load_config",
                return_value=_config(mode="single-process"),
            ),
            mock.patch(
                "openminion.cli.commands.chat.APIRuntime.from_config_path",
                return_value=runtime,
            ),
            mock.patch(
                "openminion.cli.commands.chat.run_turn",
                return_value={"body": "ops: no-progress", "metadata": {}},
            ),
            mock.patch(
                "openminion.cli.commands.chat.set_quiet_log_level"
            ) as quiet_logs,
            mock.patch("openminion.cli.commands.chat.has_tty", return_value=True),
            mock.patch("builtins.input", side_effect=["hello", "/exit"]),
        ):
            buf = _TTYStringIO()
            with redirect_stdout(buf):
                code = chat_command.run_chat(args)

        self.assertEqual(code, 0)
        quiet_logs.assert_not_called()

    def test_quiet_no_progress_chat_suppresses_status_and_logs(self) -> None:
        args = Namespace(
            config="test-configs.json",
            agent="ops",
            session="ops-chat",
            quiet=True,
            no_progress=True,
        )
        runtime = mock.Mock()

        def _run_turn(**kwargs):
            progress_callback = kwargs.get("progress_callback")
            self.assertIsNone(progress_callback)
            return {"body": "ops: quiet no-progress ok", "metadata": {}}

        with (
            mock.patch(
                "openminion.cli.commands.chat.load_config",
                return_value=_config(mode="single-process"),
            ),
            mock.patch(
                "openminion.cli.commands.chat.APIRuntime.from_config_path",
                return_value=runtime,
            ) as from_config_path,
            mock.patch("openminion.cli.commands.chat.run_turn", side_effect=_run_turn),
            mock.patch(
                "openminion.cli.commands.chat.set_quiet_log_level"
            ) as quiet_logs,
            mock.patch("builtins.input", side_effect=["hello", "/exit"]),
        ):
            buf = _TTYStringIO()
            with redirect_stdout(buf):
                code = chat_command.run_chat(args)

        self.assertEqual(code, 0)
        output = buf.getvalue()
        self.assertIn(
            "[ops-chat|ops] ops: quiet no-progress ok", _normalize_cli_output(output)
        )
        self.assertNotIn("Analyzing request...", output)
        self.assertNotIn("registered via REGISTRAR", output)
        quiet_logs.assert_called()
        from_config_path.assert_called_once_with(
            "test-configs.json",
            home_root=None,
            data_root=None,
            logging_mode="interactive",
        )
        runtime.close.assert_called_once()

    def test_chat_uses_stdin_lines_without_prompt_for_non_tty_input(self) -> None:
        args = Namespace(
            config="test-configs.json",
            agent="ops",
            session="ops-chat",
            quiet=False,
            no_progress=True,
        )

        stdin_buf = _PipeStringIO("hello from pipe\n/exit\n")
        stdout_buf = _TTYStringIO()

        with (
            mock.patch(
                "openminion.cli.commands.chat.load_config",
                return_value=_config(mode="single-process"),
            ),
            mock.patch(
                "openminion.cli.commands.chat.APIRuntime.from_config_path",
                return_value=mock.Mock(),
            ),
            mock.patch(
                "openminion.cli.commands.chat.run_turn",
                return_value={
                    "body": "ops: piped ok",
                    "metadata": {
                        "total_input_tokens_used": 1200,
                        "total_output_tokens_used": 300,
                        "total_tokens_used": 1500,
                    },
                },
            ) as run_turn,
            mock.patch(
                "openminion.cli.chat.runtime.time.monotonic",
                side_effect=[10.0, 12.0, 12.0],
            ),
            mock.patch("sys.stdin", stdin_buf),
            mock.patch(
                "builtins.input",
                side_effect=AssertionError(
                    "interactive input() should not be used for piped stdin"
                ),
            ),
            redirect_stdout(stdout_buf),
        ):
            code = chat_command.run_chat(args)

        self.assertEqual(code, 0)
        output = stdout_buf.getvalue()
        self.assertIn("chat ready agent=ops session=ops-chat", output)
        self.assertIn("processing turn", output)
        self.assertIn("[ops-chat|ops] ops: piped ok", _normalize_cli_output(output))
        self.assertIn("[chat] turn 1.5k", output)
        self.assertIn("session 1.5k", output)
        self.assertIn("total 2s", output)
        self.assertNotIn("you> ", output)
        run_turn.assert_called_once()

    def test_chat_no_activity_indicator_suppresses_minimal_wait_notice(self) -> None:
        args = Namespace(
            config="test-configs.json",
            agent="ops",
            session="ops-chat",
            quiet=False,
            no_progress=True,
            no_activity_indicator=True,
        )

        stdin_buf = _PipeStringIO("hello from pipe\n/exit\n")
        stdout_buf = _TTYStringIO()

        with (
            mock.patch(
                "openminion.cli.commands.chat.load_config",
                return_value=_config(mode="single-process"),
            ),
            mock.patch(
                "openminion.cli.commands.chat.APIRuntime.from_config_path",
                return_value=mock.Mock(),
            ),
            mock.patch(
                "openminion.cli.commands.chat.run_turn",
                return_value={"body": "ops: piped ok", "metadata": {}},
            ),
            mock.patch("sys.stdin", stdin_buf),
            redirect_stdout(stdout_buf),
        ):
            code = chat_command.run_chat(args)

        self.assertEqual(code, 0)
        output = stdout_buf.getvalue()
        self.assertNotIn("processing turn", output)
        self.assertIn("[ops-chat|ops] ops: piped ok", _normalize_cli_output(output))

    def test_chat_uses_stream_reader_when_both_stdio_are_non_tty(self) -> None:
        args = Namespace(
            config="test-configs.json",
            agent="ops",
            session="ops-chat",
            quiet=False,
            no_progress=True,
        )

        stdin_buf = _PipeStringIO("hello from pipe\n/debug\n/exit\n")
        stdout_buf = _PipeStringIO()

        with mock.patch(
            "openminion.cli.commands.chat.load_config",
            return_value=_config(mode="single-process"),
        ):
            with mock.patch(
                "openminion.cli.commands.chat.APIRuntime.from_config_path",
                return_value=mock.Mock(),
            ):
                with mock.patch(
                    "openminion.cli.commands.chat.run_turn",
                    return_value={"body": "ops: piped ok", "metadata": {}},
                ) as run_turn:
                    with mock.patch(
                        "openminion.cli.chat.commands._handle_debug_command"
                    ) as debug_cmd:
                        with mock.patch("sys.stdin", stdin_buf):
                            with mock.patch("sys.stdout", stdout_buf):
                                with mock.patch(
                                    "builtins.input",
                                    side_effect=AssertionError(
                                        "interactive input() should not be used when stdin is piped"
                                    ),
                                ):
                                    code = chat_command.run_chat(args)

        self.assertEqual(code, 0)
        self.assertEqual(run_turn.call_count, 1)
        debug_cmd.assert_called_once()
        output = stdout_buf.getvalue()
        self.assertNotIn("you> ", output)
        self.assertIn("[ops-chat|ops] ops: piped ok", _normalize_cli_output(output))

    def test_chat_stdin_one_shot_preserves_multiline_prompt_as_single_turn(
        self,
    ) -> None:
        args = Namespace(
            config="test-configs.json",
            agent="ops",
            session="ops-chat",
            quiet=False,
            no_progress=True,
            stdin_one_shot=True,
        )

        stdin_buf = _PipeStringIO(
            "Tell me:\n1. today's date in Los Angeles\n2. the active model\n"
        )
        stdout_buf = _TTYStringIO()

        with (
            mock.patch(
                "openminion.cli.commands.chat.load_config",
                return_value=_config(mode="single-process"),
            ),
            mock.patch(
                "openminion.cli.commands.chat.APIRuntime.from_config_path",
                return_value=mock.Mock(),
            ),
            mock.patch(
                "openminion.cli.commands.chat.run_turn",
                return_value={"body": "ops: one-shot ok", "metadata": {}},
            ) as run_turn,
            mock.patch("sys.stdin", stdin_buf),
            mock.patch(
                "builtins.input",
                side_effect=AssertionError(
                    "interactive input() should not be used for piped stdin"
                ),
            ),
            redirect_stdout(stdout_buf),
        ):
            code = chat_command.run_chat(args)

        self.assertEqual(code, 0)
        run_turn.assert_called_once()
        self.assertEqual(
            run_turn.call_args.kwargs["payload"]["message"],
            "Tell me:\n1. today's date in Los Angeles\n2. the active model",
        )
        output = stdout_buf.getvalue()
        self.assertNotIn("you> ", output)
        self.assertIn("[ops-chat|ops] ops: one-shot ok", _normalize_cli_output(output))

    def test_chat_interactive_tty_uses_prompt_toolkit_reader_for_multiline_turns(
        self,
    ) -> None:
        roots = SimpleNamespace(
            home_root=Path("/tmp/home"),
            data_root=Path("/tmp/data"),
        )
        config = _config()
        runtime_state = SimpleNamespace(
            quiet=False,
            transport="in-process",
            mode="hot",
        )
        captured_messages: list[str] = []
        deps = ChatRunnerDeps(
            resolve_chat_roots=lambda args: (Path("/tmp/config.json"), roots),
            load_config=lambda *args, **kwargs: config,
            inspect_chat_onboarding=lambda args: (
                SimpleNamespace(
                    action=SimpleNamespace(value="ok"),
                    state=SimpleNamespace(value="ok"),
                ),
                None,
                None,
            ),
            print_onboarding_fail_fast=lambda status: 2,
            run_inline_setup_for_chat=lambda args: 1,
            materialize_demo_config_for_chat=lambda args, *, roots, config_path: Path(
                "/tmp/demo.json"
            ),
            normalize_chat_args=lambda args, config: SimpleNamespace(
                session_id="ops-chat",
                session_name="",
                quiet=False,
                show_progress=False,
            ),
            perform_identity_sync=lambda **kwargs: None,
            should_suppress_console_info_logs=lambda **kwargs: False,
            set_quiet_log_level=lambda: None,
            init_runtime_state=lambda args, config: (runtime_state, None),
            mark_stale_cli_sessions=lambda **kwargs: 0,
            resolve_initial_chat_agent_id=lambda args, *, config, session_id: (
                "ops",
                {},
            ),
            resolve_lifecycle_state=lambda *args, **kwargs: (
                {"source": "fresh"},
                "conv-1",
                "thread-1",
                "attach-1",
                {},
            ),
            session_profile_mismatch_message=lambda **kwargs: "",
            print_chat_ready_banner=lambda **kwargs: None,
            print_agent_resolution_notice=lambda **kwargs: None,
            print_stale_session_notice=lambda **kwargs: None,
            print_first_session_tip_if_requested=lambda args: None,
            get_session_record=lambda **kwargs: None,
            emit_session_open_events=lambda **kwargs: None,
            set_session_name_if_missing=lambda **kwargs: False,
            handle_chat_command=lambda **kwargs: SimpleNamespace(handled=False),
            handle_repl_command=lambda **kwargs: {},
            local_human_post_block_reason=lambda **kwargs: "",
            build_lifecycle_payload=lambda **kwargs: {},
            build_inbound_metadata=lambda **kwargs: {},
            build_turn_idempotency_key=lambda **kwargs: "turn-key",
            build_run_profile_override_payload=lambda args: {},
            execute_turn=lambda **kwargs: (
                captured_messages.append(kwargs["payload"]["message"]) or {"stop": True}
            ),
            maybe_auto_name_session=lambda **kwargs: False,
            emit_session_event_safe=lambda **kwargs: None,
            close_runtime=lambda state: None,
            chat_input_prompt=lambda **kwargs: "[ops-chat|ops] you> ",
            conversation_env_name=chat_command.OPENMINION_CONVERSATION_ID_ENV,
            resolve_environment_config=dict,
            stale_timeout_default=10,
            turn_timeout_default=90.0,
            turn_max_attempts_default=2,
        )
        args = _chat_args()
        stdin_buf = _TTYStringIO()
        stdout_buf = _TTYStringIO()
        fake_reader = SimpleNamespace(
            read_line=mock.Mock(side_effect=["line one\nline two", None])
        )

        with mock.patch("sys.stdin", stdin_buf):
            with mock.patch("sys.stdout", stdout_buf):
                with mock.patch(
                    "openminion.cli.chat.runner._build_prompt_toolkit_chat_reader",
                    return_value=fake_reader,
                ):
                    with mock.patch(
                        "builtins.input",
                        side_effect=AssertionError(
                            "input() fallback should not be used for interactive TTY multiline prompts"
                        ),
                    ):
                        code = run_chat_runner(args, deps=deps)

        self.assertEqual(code, 0)
        self.assertEqual(captured_messages, ["line one\nline two"])
        fake_reader.read_line.assert_called()

    def test_prompt_toolkit_chat_reader_normalizes_carriage_return_paste(self) -> None:
        if not chat_command.chat_runner._PROMPT_TOOLKIT_AVAILABLE:
            self.skipTest("prompt_toolkit not available")
        reader = chat_command.chat_runner._PromptToolkitInteractiveChatReader()

        class _Buffer:
            def __init__(self) -> None:
                self.calls: list[str] = []

            def insert_text(self, text: str) -> None:
                self.calls.append(text)

        buffer = _Buffer()
        reader._insert_pasted_text("line one\r\nline two\rline three", buffer=buffer)
        self.assertTrue(reader._multiline)
        self.assertEqual(buffer.calls, ["line one\nline two\nline three"])

    def test_prompt_toolkit_chat_reader_resets_multiline_after_submit(self) -> None:
        if not chat_command.chat_runner._PROMPT_TOOLKIT_AVAILABLE:
            self.skipTest("prompt_toolkit not available")
        reader = chat_command.chat_runner._PromptToolkitInteractiveChatReader()
        reader._multiline = True
        reader._session = SimpleNamespace(prompt=mock.Mock(return_value="hello"))
        self.assertEqual(reader.read_line("[ops-chat|ops] you> "), "hello")
        self.assertFalse(reader._multiline)

    def test_phase_status_display_dedupes_duplicate_terminal_line(self) -> None:
        from openminion.cli.chat.ui import PhaseStatusDisplay
        from openminion.modules.brain.diagnostics.status import PhaseStatus

        buf = _TTYStringIO()
        with redirect_stdout(buf):
            with PhaseStatusDisplay(enabled=True, animate=True) as display:
                display.update(
                    PhaseStatus(
                        trace_id="trace-terminal-status",
                        status_key="completed",
                        label="Completed.",
                        terminal=True,
                        total_output_tokens_used=8,
                    )
                )
                display.update(
                    PhaseStatus(
                        trace_id="trace-terminal-status",
                        status_key="completed",
                        label="Completed.",
                        terminal=True,
                        total_output_tokens_used=21,
                    )
                )
                time.sleep(0.25)

        output = buf.getvalue()
        self.assertIn("Completed.", output)
        self.assertEqual(output.count("Completed."), 1)

    def test_chat_terminal_text_truncates_to_terminal_width(self) -> None:
        from openminion.cli.chat.ui import _truncate_terminal_text

        with mock.patch(
            "openminion.cli.chat.ui.shutil.get_terminal_size",
            return_value=os.terminal_size((20, 20)),
        ):
            truncated = _truncate_terminal_text(
                "LLM 2/24 | Executing step... llm progress [act] composing answer",
                prefix_width=4,
            )
        self.assertLessEqual(len(truncated), 16)
        self.assertTrue(truncated.endswith("…"))

    def test_chat_terminal_text_sanitizes_multiline_status_to_single_line(self) -> None:
        from openminion.cli.chat.ui import _truncate_terminal_text

        sanitized = _truncate_terminal_text(
            "## Iteration 1: Deep Research\n\n### Key Iran Developments\t(June 2025)",
            prefix_width=0,
        )

        self.assertEqual(
            sanitized,
            "## Iteration 1: Deep Research ### Key Iran Developments (June 2025)",
        )
        self.assertNotIn("\n", sanitized)
        self.assertNotIn("\r", sanitized)

    def test_phase_status_display_dedupes_same_truncated_spinner_line(self) -> None:
        from openminion.cli.chat.ui import PhaseStatusDisplay
        from openminion.modules.brain.diagnostics.status import PhaseStatus

        buf = _TTYStringIO()
        with (
            mock.patch(
                "openminion.cli.chat.ui.shutil.get_terminal_size",
                return_value=os.terminal_size((36, 20)),
            ),
            redirect_stdout(buf),
        ):
            with PhaseStatusDisplay(enabled=True, animate=True) as display:
                display.update(
                    PhaseStatus(
                        trace_id="trace-spinner-trunc",
                        status_key="executing",
                        label="Executing step...",
                        llm_call_count=2,
                        llm_call_limit=24,
                        detail_text="llm progress [act] composing answer",
                        total_input_tokens_used=17900,
                        total_output_tokens_used=1100,
                    )
                )
                display.update(
                    PhaseStatus(
                        trace_id="trace-spinner-trunc",
                        status_key="executing",
                        label="Executing step...",
                        llm_call_count=2,
                        llm_call_limit=24,
                        detail_text="llm progress [act] composing answer",
                        total_input_tokens_used=17980,
                        total_output_tokens_used=1110,
                    )
                )
                time.sleep(0.05)

        output = buf.getvalue()
        self.assertEqual(output.count("LLM 2/24"), 1)

    def test_phase_status_display_collapses_multiline_partial_result_to_inline_summary(
        self,
    ) -> None:
        from openminion.cli.chat.ui import PhaseStatusDisplay
        from openminion.modules.brain.diagnostics.status import PhaseStatus

        buf = _TTYStringIO()
        with redirect_stdout(buf):
            with PhaseStatusDisplay(enabled=True, animate=True) as display:
                display.update(
                    PhaseStatus(
                        trace_id="trace-research-partial",
                        status_key="executing",
                        label="Executing step...",
                        mode_label="[act:research] partial result",
                        detail_text=(
                            "## Iteration 1: Deep Research - Iran News & Market Implications\n\n"
                            "### Key Iran Developments (June 2025)\n"
                            "- item one\n"
                        ),
                    )
                )
                time.sleep(0.15)

        output = buf.getvalue()
        self.assertIn("## Iteration 1: Deep Research", output)
        self.assertNotIn("### Key Iran Developments", output)

    def test_phase_status_display_sanitizes_unsafely_multiline_label_before_render(
        self,
    ) -> None:
        from openminion.cli.chat.ui import PhaseStatusDisplay

        buf = _TTYStringIO()
        with redirect_stdout(buf):
            with PhaseStatusDisplay(enabled=True, animate=True) as display:
                display._label = (
                    "Executing step... [act:research] partial result "
                    "## Iteration 1: Deep Research\n\n"
                    "### Key Iran Developments (June 2025)"
                )
                display._render_once(frame="*")
                time.sleep(0.05)

        output = buf.getvalue()
        self.assertIn("## Iteration 1: Deep Research", output)
        self.assertIn("### Key Iran D", output)
        self.assertEqual(output.count("### Key Iran D"), 1)
        self.assertNotIn("\n\n### Key Iran Developments", output)

    def test_inproc_chat_renders_awaiting_confirmation_status(self) -> None:
        from openminion.modules.brain.diagnostics.status import PhaseStatus

        args = Namespace(
            config="test-configs.json",
            agent="ops",
            session="ops-chat",
            quiet=False,
            no_progress=False,
        )
        runtime = mock.Mock()

        def _run_turn(**kwargs):
            progress_callback = kwargs.get("progress_callback")
            self.assertIsNotNone(progress_callback)
            progress_callback(
                PhaseStatus(
                    trace_id="trace-chat-confirm",
                    status_key="awaiting_confirmation",
                    label="Waiting for confirmation...",
                )
            )
            return {"body": "ops: Please confirm before I continue.", "metadata": {}}

        with (
            mock.patch(
                "openminion.cli.commands.chat.load_config",
                return_value=_config(mode="single-process"),
            ),
            mock.patch(
                "openminion.cli.commands.chat.APIRuntime.from_config_path",
                return_value=runtime,
            ),
            mock.patch("openminion.cli.commands.chat.run_turn", side_effect=_run_turn),
            mock.patch("builtins.input", side_effect=["hello", "/exit"]),
        ):
            buf = _TTYStringIO()
            with redirect_stdout(buf):
                code = chat_command.run_chat(args)

        self.assertEqual(code, 0)
        output = buf.getvalue()
        self.assertIn("Waiting for confirmation...", output)
        self.assertIn(
            "[ops-chat|ops] ops: Please confirm before I continue.",
            _normalize_cli_output(output),
        )
        runtime.close.assert_called_once()

    def test_chat_progress_falls_back_to_working_for_unknown_status_payload(
        self,
    ) -> None:
        from openminion.cli.transport.daemon_client import DaemonStreamEvent

        args = Namespace(
            config="test-configs.json",
            agent="ops",
            session="ops-chat",
            quiet=False,
            no_progress=False,
        )
        endpoint = SimpleNamespace(host="127.0.0.1", port=18789)

        def _daemon_stream_request(*, on_event=None, **kwargs):
            del kwargs
            assert on_event is not None
            on_event(
                DaemonStreamEvent(
                    event="chunk",
                    data={
                        "kind": "status",
                        "data": {
                            "trace_id": "trace-daemon-unknown",
                            "status_key": "mystery",
                            "label": "",
                        },
                    },
                )
            )
            return 200, {
                "ok": True,
                "turn": {
                    "trace_id": "trace-daemon-unknown",
                    "final_text": "ops: fallback ok",
                },
            }

        with (
            mock.patch(
                "openminion.cli.commands.chat.load_config",
                return_value=_config(mode="daemon"),
            ),
            mock.patch(
                "openminion.cli.commands.chat.ensure_daemon_running",
                return_value=endpoint,
            ),
            mock.patch(
                "openminion.cli.commands.chat.daemon_request",
                return_value=(200, {"ok": True}),
            ),
            mock.patch(
                "openminion.cli.commands.chat.daemon_stream_request",
                side_effect=_daemon_stream_request,
            ),
            mock.patch("builtins.input", side_effect=["hello", "/exit"]),
        ):
            buf = _TTYStringIO()
            with redirect_stdout(buf):
                code = chat_command.run_chat(args)

        self.assertEqual(code, 0)
        output = buf.getvalue()
        self.assertIn("Working...", output)
        self.assertIn("[ops-chat|ops] ops: fallback ok", _normalize_cli_output(output))

    def test_chat_progress_falls_back_to_working_when_no_status_updates_arrive(
        self,
    ) -> None:
        args = Namespace(
            config="test-configs.json",
            agent="ops",
            session="ops-chat",
            quiet=False,
            no_progress=False,
        )
        runtime = mock.Mock()

        def _run_turn(**kwargs):
            self.assertIsNotNone(kwargs.get("progress_callback"))
            return {"body": "ops: no status updates", "metadata": {}}

        with (
            mock.patch(
                "openminion.cli.commands.chat.load_config",
                return_value=_config(mode="single-process"),
            ),
            mock.patch(
                "openminion.cli.commands.chat.APIRuntime.from_config_path",
                return_value=runtime,
            ),
            mock.patch("openminion.cli.commands.chat.run_turn", side_effect=_run_turn),
            mock.patch("builtins.input", side_effect=["hello", "/exit"]),
        ):
            buf = _TTYStringIO()
            with redirect_stdout(buf):
                code = chat_command.run_chat(args)

        self.assertEqual(code, 0)
        output = buf.getvalue()
        self.assertIn("Working...", output)
        self.assertIn(
            "[ops-chat|ops] ops: no status updates", _normalize_cli_output(output)
        )
        runtime.close.assert_called_once()

    def test_quiet_chat_still_renders_phase_status_when_enabled(self) -> None:
        from openminion.modules.brain.diagnostics.status import PhaseStatus

        args = Namespace(
            config="test-configs.json",
            agent="ops",
            session="ops-chat",
            quiet=True,
            no_progress=False,
        )
        runtime = mock.Mock()

        def _run_turn(**kwargs):
            progress_callback = kwargs.get("progress_callback")
            self.assertIsNotNone(progress_callback)
            progress_callback(
                PhaseStatus(
                    trace_id="trace-chat-quiet",
                    status_key="analyzing",
                    label="Analyzing request...",
                )
            )
            return {"body": "ops: quiet ok", "metadata": {}}

        with (
            mock.patch(
                "openminion.cli.commands.chat.load_config",
                return_value=_config(mode="single-process"),
            ),
            mock.patch(
                "openminion.cli.commands.chat.APIRuntime.from_config_path",
                return_value=runtime,
            ),
            mock.patch("openminion.cli.commands.chat.run_turn", side_effect=_run_turn),
            mock.patch("builtins.input", side_effect=["hello", "/exit"]),
        ):
            buf = _TTYStringIO()
            with redirect_stdout(buf):
                code = chat_command.run_chat(args)

        self.assertEqual(code, 0)
        output = buf.getvalue()
        self.assertIn("Analyzing request...", output)
        self.assertIn("[ops-chat|ops] ops: quiet ok", _normalize_cli_output(output))
        runtime.close.assert_called_once()

    def test_tty_progress_clears_before_turn_error_output(self) -> None:
        from openminion.modules.brain.diagnostics.status import PhaseStatus

        args = Namespace(
            config="test-configs.json",
            agent="ops",
            session="ops-chat",
            quiet=False,
            no_progress=False,
        )
        runtime = mock.Mock()

        def _run_turn(**kwargs):
            progress_callback = kwargs.get("progress_callback")
            self.assertIsNotNone(progress_callback)
            progress_callback(
                PhaseStatus(
                    trace_id="trace-chat-error",
                    status_key="analyzing",
                    label="Analyzing request...",
                )
            )
            raise RuntimeError("provider failed")

        with (
            mock.patch(
                "openminion.cli.commands.chat.load_config",
                return_value=_config(mode="single-process"),
            ),
            mock.patch(
                "openminion.cli.commands.chat.APIRuntime.from_config_path",
                return_value=runtime,
            ),
            mock.patch("openminion.cli.commands.chat.run_turn", side_effect=_run_turn),
            mock.patch("builtins.input", side_effect=["hello", "/exit"]),
        ):
            buf = _TTYStringIO()
            with redirect_stdout(buf):
                code = chat_command.run_chat(args)

        self.assertEqual(code, 0)
        output = buf.getvalue()
        self.assertIn("[chat] turn failed: provider failed", output)
        self.assertFalse(output.rstrip().endswith("Analyzing request..."))
        runtime.close.assert_called_once()

    def test_phase_status_renderer_keeps_cli_free_of_phase_mapping_logic(self) -> None:
        import openminion.cli.chat.ui as chat_ui

        source = inspect.getsource(chat_ui)

        self.assertIn("from openminion.cli.status import", source)
        self.assertIn("format_primary_status_text", source)
        self.assertNotIn(
            "from openminion.modules.brain.diagnostics.status import", source
        )
        self.assertNotIn("_PHASE_STATUS_MAP", source)
        self.assertNotIn("brain.closure_gate.started", source)
        self.assertNotIn("DECIDE", source)

    def test_chat_ready_prints_provider_banner(self) -> None:
        args = Namespace(
            config="test-configs.json",
            agent="ops",
            session="ops-chat",
            quiet=False,
            no_progress=True,
        )

        with (
            mock.patch(
                "openminion.cli.commands.chat.load_config",
                return_value=_config(mode="single-process", provider="openrouter"),
            ),
            mock.patch("builtins.input", side_effect=["/exit"]),
        ):
            buf = io.StringIO()
            with redirect_stdout(buf):
                code = chat_command.run_chat(args)

        self.assertEqual(code, 0)
        output = buf.getvalue()
        self.assertIn("provider=openrouter", output)
        self.assertNotIn("provider=echo active", output)

    def test_chat_ready_warns_when_echo_provider_active(self) -> None:
        args = Namespace(
            config="test-configs.json",
            agent="ops",
            session="ops-chat",
            quiet=False,
            no_progress=True,
        )

        with (
            mock.patch(
                "openminion.cli.commands.chat.load_config",
                return_value=_config(mode="single-process", provider="echo"),
            ),
            mock.patch("builtins.input", side_effect=["/exit"]),
        ):
            buf = io.StringIO()
            with redirect_stdout(buf):
                code = chat_command.run_chat(args)

        self.assertEqual(code, 0)
        self.assertIn(
            "provider=echo active; responses mirror input text", buf.getvalue()
        )

    def test_chat_demo_mode_prints_explicit_demo_banner(self) -> None:
        args = Namespace(
            config="test-configs.json",
            agent="ops",
            session="ops-chat",
            quiet=False,
            no_progress=True,
            demo=True,
        )

        with (
            mock.patch(
                "openminion.cli.commands.chat._materialize_demo_config_for_chat",
                return_value=Path("/tmp/demo-chat.json"),
            ),
            mock.patch(
                "openminion.cli.commands.chat.load_config",
                return_value=_config(
                    mode="single-process",
                    provider="echo",
                    demo_mode=True,
                ),
            ),
            mock.patch("builtins.input", side_effect=["/exit"]),
        ):
            buf = io.StringIO()
            with redirect_stdout(buf):
                code = chat_command.run_chat(args)

        self.assertEqual(code, 0)
        self.assertIn("demo mode active", buf.getvalue())
        self.assertNotIn("provider=echo active", buf.getvalue())

    def test_chat_missing_explicit_config_noninteractive_fails_fast_without_setup(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            args = _chat_args(config=str(Path(tmp) / "missing.json"))
            with mock.patch("openminion.cli.commands.chat.has_tty", return_value=False):
                buf = io.StringIO()
                with redirect_stdout(buf):
                    code = chat_command.run_chat(args)

        self.assertEqual(code, 2)
        self.assertIn("Config file does not exist:", buf.getvalue())
        self.assertIn(str(Path(tmp) / "missing.json"), buf.getvalue())
        self.assertNotIn("run: openminion setup", buf.getvalue())

    def test_chat_missing_explicit_config_interactive_fails_fast(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            args = Namespace(
                config="missing.json",
                agent="ops",
                session="ops-chat",
                quiet=False,
                no_progress=True,
                conversation=None,
                resume=False,
                reset_session=False,
                demo=False,
                verbose=False,
                tools_verbose=False,
                home_root=str(Path(tmp)),
                data_root=str(Path(tmp) / ".openminion"),
            )

            previous_cwd = Path.cwd()
            try:
                os.chdir(tmp)
                with mock.patch(
                    "openminion.cli.commands.chat.has_tty", return_value=True
                ):
                    buf = io.StringIO()
                    with redirect_stdout(buf):
                        code = chat_command.run_chat(args)
            finally:
                os.chdir(previous_cwd)

        self.assertEqual(code, 2)
        self.assertIn("Config file does not exist:", buf.getvalue())
        self.assertIn(str(Path(tmp) / "missing.json"), buf.getvalue())
        self.assertIn("current working directory", buf.getvalue())
        self.assertNotIn("run: openminion setup", buf.getvalue())

    def test_chat_missing_explicit_config_shows_nearby_suggestion(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home_root = Path(tmp)
            config_dir = home_root / "test-configs"
            config_dir.mkdir()
            suggested = config_dir / "per-agent-openrouter-claude-haiku-3.json"
            suggested.write_text("{}", encoding="utf-8")
            args = Namespace(
                config=str(config_dir / "per-agent-openrouter-claude-3-haiku.json"),
                agent="ops",
                session="ops-chat",
                quiet=False,
                no_progress=True,
                conversation=None,
                resume=False,
                reset_session=False,
                demo=False,
                verbose=False,
                tools_verbose=False,
                home_root=str(home_root),
                data_root=str(home_root / ".openminion"),
            )

            with mock.patch("openminion.cli.commands.chat.has_tty", return_value=True):
                buf = io.StringIO()
                with redirect_stdout(buf):
                    code = chat_command.run_chat(args)

        self.assertEqual(code, 2)
        self.assertIn(str(suggested), buf.getvalue())
        self.assertNotIn("run: openminion setup", buf.getvalue())

    def test_chat_missing_default_config_interactive_runs_inline_setup(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            args = Namespace(
                config=None,
                agent="ops",
                session="ops-chat",
                quiet=False,
                no_progress=True,
                conversation=None,
                resume=False,
                reset_session=False,
                demo=False,
                verbose=False,
                tools_verbose=False,
                home_root=str(Path(tmp)),
                data_root=str(Path(tmp) / ".openminion"),
            )

            with mock.patch("openminion.cli.commands.chat.has_tty", return_value=True):
                with mock.patch(
                    "openminion.cli.commands.chat._run_inline_setup_for_chat",
                    return_value=0,
                ) as setup_mock:
                    with mock.patch(
                        "openminion.cli.commands.chat.load_config",
                        side_effect=[
                            ConfigManagerError("config file not found"),
                            _config(
                                mode="single-process",
                                provider="openrouter",
                            ),
                        ],
                    ):
                        with mock.patch("builtins.input", side_effect=["/exit"]):
                            code = chat_command.run_chat(args)

        self.assertEqual(code, 0)
        setup_mock.assert_called_once_with(args)

    def test_chat_existing_echo_config_without_demo_mode_fails_fast(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config_path = Path(tmp) / "config.json"
            config = OpenMinionConfig()
            _csc_install_default_agent(config, provider="echo")
            config.runtime.demo_mode = False
            save_config(config, str(config_path))
            args = _chat_args(config=str(config_path), agent="openminion")

            with mock.patch("openminion.cli.commands.chat.has_tty", return_value=False):
                buf = io.StringIO()
                with redirect_stdout(buf):
                    code = chat_command.run_chat(args)

        self.assertEqual(code, 2)
        self.assertIn("demo-only", buf.getvalue())

    def test_chat_onboarding_accepts_provider_credentials_from_config_runtime_env(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config_path = root / "config.json"
            config = OpenMinionConfig()
            _csc_install_default_agent(config, provider="openai")
            config.providers.openai.api_key_env = "DASHSCOPE_API_KEY"
            config.providers.openai.base_url = (
                "https://coding-intl.dashscope.aliyuncs.com/v1"
            )
            config.runtime.env = {"DASHSCOPE_API_KEY": "sk-test"}
            config.storage.path = str(root / ".openminion" / "state" / "openminion.db")
            save_config(config, str(config_path))

            args = Namespace(
                config=str(config_path),
                agent="openminion",
                session="alibaba-minimax-chat111",
                quiet=False,
                no_progress=True,
                conversation=None,
                resume=False,
                reset_session=False,
                demo=False,
                verbose=False,
                tools_verbose=False,
                home_root=str(root),
                data_root=str(root / ".openminion"),
            )

            with mock.patch("openminion.cli.commands.chat.has_tty", return_value=True):
                status, _, _ = chat_command._inspect_chat_onboarding(args)

        self.assertEqual(status.state.value, "ready")
        self.assertEqual(status.action.value, "continue")

    def test_chat_onboarding_no_interactive_forces_fail_fast(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            args = Namespace(
                config=None,
                agent="openminion",
                session="chat-no-interactive",
                quiet=False,
                no_progress=True,
                conversation=None,
                resume=False,
                reset_session=False,
                demo=False,
                verbose=False,
                tools_verbose=False,
                home_root=str(root),
                data_root=str(root / ".openminion"),
                no_interactive=True,
            )

            with mock.patch("openminion.cli.commands.chat.has_tty", return_value=True):
                status, _, _ = chat_command._inspect_chat_onboarding(args)

        self.assertEqual(status.state.value, "missing_config")
        self.assertEqual(status.action.value, "fail_fast")

    def test_chat_first_session_tip_prints_once_when_requested(self) -> None:
        args = _chat_args(first_session_tip=True)

        with (
            mock.patch(
                "openminion.cli.commands.chat.load_config",
                return_value=_config(mode="single-process", provider="openrouter"),
            ),
            mock.patch("builtins.input", side_effect=["/exit"]),
        ):
            buf = io.StringIO()
            with redirect_stdout(buf):
                code = chat_command.run_chat(args)

        self.assertEqual(code, 0)
        output = buf.getvalue()
        self.assertIn("Tip: type `openminion chat`", output)
        self.assertIn("`openminion focus`", output)
        self.assertEqual(output.count("Tip: type `openminion chat`"), 1)

    def test_chat_ready_warns_when_session_has_prior_history(self) -> None:
        args = _chat_args(reset_session=False)

        with (
            mock.patch(
                "openminion.cli.commands.chat.load_config",
                return_value=_config(mode="single-process", provider="openrouter"),
            ),
            mock.patch(
                "openminion.cli.commands.chat._latest_session_conversation_id",
                return_value="conv-prev",
            ),
            mock.patch("builtins.input", side_effect=["/exit"]),
        ):
            buf = io.StringIO()
            with redirect_stdout(buf):
                code = chat_command.run_chat(args)

        self.assertEqual(code, 0)
        output = buf.getvalue()
        self.assertIn(
            "[chat] resuming conversation 'conv-prev' for session 'ops-chat'.",
            output,
        )

    def test_session_has_prior_trace_history_detects_modern_trace_layout(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "traces" / "llm" / "ops" / "20260403-ops-chat").mkdir(parents=True)

            with mock.patch(
                "openminion.cli.commands.chat.resolve_cli_roots",
                return_value=SimpleNamespace(data_root=root, env={}),
            ):
                found = chat_command._session_has_prior_trace_history(
                    session_id="ops::ops-chat",
                    config_path="ignored.json",
                )

        self.assertTrue(found)

    def test_session_has_prior_trace_history_ignores_legacy_llm_requests_dir(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            legacy_dir = root / "traces" / "llm_requests"
            legacy_dir.mkdir(parents=True)
            (legacy_dir / "ops::ops-chat-legacy.json").write_text("{}")

            with mock.patch(
                "openminion.cli.commands.chat.resolve_cli_roots",
                return_value=SimpleNamespace(data_root=root, env={}),
            ):
                found = chat_command._session_has_prior_trace_history(
                    session_id="ops::ops-chat",
                    config_path="ignored.json",
                )

        self.assertFalse(found)

    def test_chat_ready_skips_resume_warning_when_reset_requested(self) -> None:
        args = _chat_args(reset_session=True)

        with (
            mock.patch(
                "openminion.cli.commands.chat.load_config",
                return_value=_config(mode="single-process", provider="openrouter"),
            ),
            mock.patch(
                "openminion.cli.commands.chat._latest_session_conversation_id",
                return_value="conv-prev",
            ),
            mock.patch("builtins.input", side_effect=["/exit"]),
        ):
            buf = io.StringIO()
            with redirect_stdout(buf):
                code = chat_command.run_chat(args)

        self.assertEqual(code, 0)
        self.assertNotIn("resuming existing session context", buf.getvalue())

    def test_chat_ready_resume_warns_when_no_prior_conversation_found(self) -> None:
        args = _chat_args(resume=True)

        with (
            mock.patch(
                "openminion.cli.commands.chat.load_config",
                return_value=_config(mode="single-process", provider="openrouter"),
            ),
            mock.patch(
                "openminion.cli.commands.chat._latest_session_conversation_id",
                return_value="",
            ),
            mock.patch("builtins.input", side_effect=["hello", "/exit"]),
            mock.patch(
                "openminion.cli.commands.chat.APIRuntime.from_config_path",
                return_value=mock.Mock(),
            ),
            mock.patch(
                "openminion.cli.commands.chat.run_turn",
                return_value={"body": "ops: hi", "metadata": {}},
            ) as run_turn,
        ):
            buf = io.StringIO()
            with redirect_stdout(buf):
                code = chat_command.run_chat(args)

        self.assertEqual(code, 0)
        output = buf.getvalue()
        self.assertIn(
            "no prior conversation found for session 'ops-chat'; starting fresh", output
        )
        payload = run_turn.call_args.kwargs["payload"]
        self.assertTrue(
            payload["inbound_metadata"].get("conversation_id", "").startswith("conv-")
        )

    def test_chat_uses_prior_session_agent_when_agent_omitted(self) -> None:
        args = _chat_args(agent="", session="shared-chat", reset_session=False)

        with (
            mock.patch(
                "openminion.cli.commands.chat.load_config",
                return_value=_config(
                    mode="single-process",
                    provider="openrouter",
                    agent_name="hello-agent",
                ),
            ),
            mock.patch(
                "openminion.cli.commands.chat._latest_session_conversation_id",
                return_value="conv-prev",
            ),
            mock.patch(
                "openminion.cli.commands.chat._latest_session_agent_id",
                return_value="alibaba-minimax",
            ),
            mock.patch("builtins.input", side_effect=["/exit"]),
        ):
            buf = io.StringIO()
            with redirect_stdout(buf):
                code = chat_command.run_chat(args)

        self.assertEqual(code, 0)
        output = buf.getvalue()
        self.assertIn("chat ready agent=alibaba-minimax session=shared-chat", output)
        self.assertIn(
            "[chat] using prior session agent 'alibaba-minimax' for 'shared-chat' "
            "instead of config default 'hello-agent'.",
            output,
        )

    def test_chat_warns_on_explicit_cross_agent_resume(self) -> None:
        args = _chat_args(
            agent="hello-agent",
            session="shared-chat",
            reset_session=False,
        )

        with (
            mock.patch(
                "openminion.cli.commands.chat.load_config",
                return_value=_config(
                    mode="single-process",
                    provider="openrouter",
                    agent_name="hello-agent",
                ),
            ),
            mock.patch(
                "openminion.cli.commands.chat._latest_session_conversation_id",
                return_value="conv-prev",
            ),
            mock.patch(
                "openminion.cli.commands.chat._latest_session_agent_id",
                return_value="alibaba-minimax",
            ),
            mock.patch("builtins.input", side_effect=["/exit"]),
        ):
            buf = io.StringIO()
            with redirect_stdout(buf):
                code = chat_command.run_chat(args)

        self.assertEqual(code, 2)
        output = buf.getvalue()
        self.assertIn(
            "[chat] session 'shared-chat' is already bound to profile "
            "'alibaba-minimax', but requested profile 'hello-agent' does not match. "
            "Use --reset-session or a new --session id to start fresh.",
            output,
        )
        self.assertNotIn("chat ready agent=hello-agent session=shared-chat", output)

    def test_ensure_inproc_runtime_uses_cli_roots(self) -> None:
        state = chat_command.ChatRuntimeState(
            endpoint=None,
            transport="in-process",
            inproc_runtime=None,
            mode="single-process",
            auto_start=False,
            show_progress=False,
            quiet=False,
            home_root="/tmp/apso-home",
            data_root="/tmp/apso-data",
        )
        runtime = object()

        with mock.patch(
            "openminion.cli.chat.runtime.APIRuntime.from_config_path",
            return_value=runtime,
        ) as from_config_path:
            resolved = chat_command.ensure_inproc_runtime(state, "config.json")

        self.assertIs(resolved, runtime)
        from_config_path.assert_called_once_with(
            "config.json",
            home_root="/tmp/apso-home",
            data_root="/tmp/apso-data",
        )

    def test_ensure_inproc_runtime_uses_interactive_logging_for_quiet_chat(
        self,
    ) -> None:
        state = chat_command.ChatRuntimeState(
            endpoint=None,
            transport="in-process",
            inproc_runtime=None,
            mode="single-process",
            auto_start=False,
            show_progress=False,
            quiet=True,
            home_root="/tmp/apso-home",
            data_root="/tmp/apso-data",
        )
        runtime = object()

        with mock.patch(
            "openminion.cli.chat.runtime.APIRuntime.from_config_path",
            return_value=runtime,
        ) as from_config_path:
            resolved = chat_command.ensure_inproc_runtime(state, "config.json")

        self.assertIs(resolved, runtime)
        from_config_path.assert_called_once_with(
            "config.json",
            home_root="/tmp/apso-home",
            data_root="/tmp/apso-data",
            logging_mode="interactive",
        )

    def test_chat_inproc_turn_includes_runtime_override_payload(self) -> None:
        args = _chat_args(
            agent="ops",
            override_provider="anthropic",
            override_model="claude-3-5-haiku-latest",
            override_system_prompt="Stay concise.",
        )
        runtime_state = SimpleNamespace(
            endpoint=None,
            show_progress=False,
            quiet=False,
            transport="in-process",
            mode="brain",
            inproc_runtime=None,
            approval_state=ChatApprovalState(),
        )

        with (
            mock.patch(
                "openminion.cli.commands.chat.load_config",
                return_value=_config(mode="single-process", provider="openrouter"),
            ),
            mock.patch(
                "openminion.cli.commands.chat.init_runtime_state",
                return_value=(runtime_state, None),
            ),
            mock.patch(
                "openminion.cli.commands.chat.ensure_inproc_runtime",
                return_value=object(),
            ),
            mock.patch(
                "openminion.cli.commands.chat.request_inproc_turn",
                return_value={"body": "ops: hi", "metadata": {}},
            ) as request_inproc_turn,
            mock.patch("openminion.cli.commands.chat.emit_session_event"),
            mock.patch("openminion.cli.commands.chat.close_runtime"),
            mock.patch(
                "openminion.cli.chat.runner._build_prompt_toolkit_chat_reader",
                return_value=None,
            ),
            mock.patch("sys.stdin", _TTYStringIO("")),
            mock.patch("builtins.input", side_effect=["hello", "/exit"]),
        ):
            buf = io.StringIO()
            with redirect_stdout(buf):
                code = chat_command.run_chat(args)

        self.assertEqual(code, 0)
        payload = request_inproc_turn.call_args.kwargs["payload"]
        self.assertEqual(payload["override_provider"], "anthropic")
        self.assertEqual(payload["override_model"], "claude-3-5-haiku-latest")
        self.assertEqual(payload["override_system_prompt"], "Stay concise.")

    def test_emit_session_event_creates_session_before_appending_event(self) -> None:
        sessions = mock.Mock()
        sessions.get_session.return_value = None
        config = OpenMinionConfig()
        _csc_install_default_agent(config, name="hello-agent")  # type: ignore[attr-defined]
        runtime = SimpleNamespace(
            sessions=sessions,
            config=config,
        )
        state = chat_command.ChatRuntimeState(
            endpoint=None,
            transport="in-process",
            inproc_runtime=runtime,
            mode="single-process",
            auto_start=False,
            show_progress=False,
            quiet=False,
        )

        chat_command.emit_session_event(
            state=state,
            config_path="config.json",
            session_id="shared-chat",
            event_type="client.attach",
            payload={"selected_profile_id": "planner-safe"},
        )

        sessions.resolve_session.assert_called_once_with(
            agent_id="planner-safe",
            channel="console",
            target="cli-chat",
            session_id="shared-chat",
            metadata={"selected_profile_id": "planner-safe"},
        )
        sessions.append_event.assert_called_once_with(
            session_id="shared-chat",
            event_type="client.attach",
            payload={"selected_profile_id": "planner-safe"},
        )

    def test_run_chat_emits_session_created_for_new_session(self) -> None:
        args = _chat_args()
        runtime_state = chat_command.ChatRuntimeState(
            endpoint=None,
            transport="in-process",
            inproc_runtime=None,
            mode="single-process",
            auto_start=False,
            show_progress=False,
            quiet=False,
        )

        with (
            mock.patch(
                "openminion.cli.commands.chat.load_config",
                return_value=_config(mode="single-process"),
            ),
            mock.patch(
                "openminion.cli.commands.chat.init_runtime_state",
                return_value=(runtime_state, None),
            ),
            mock.patch(
                "openminion.cli.commands.chat._mark_stale_cli_sessions",
                return_value=0,
            ),
            mock.patch(
                "openminion.cli.commands.chat._get_session_record",
                return_value=None,
            ),
            mock.patch(
                "openminion.cli.commands.chat._emit_session_event_safe"
            ) as emit_event,
            mock.patch("openminion.cli.commands.chat.close_runtime"),
            mock.patch("builtins.input", side_effect=["/exit"]),
        ):
            code = chat_command.run_chat(args)

        self.assertEqual(code, 0)
        event_types = [
            call.kwargs["event_type"]
            for call in emit_event.call_args_list
            if "event_type" in call.kwargs
        ]
        self.assertEqual(event_types[:2], ["session.created", "client.attach"])

    def test_run_chat_emits_session_resumed_and_warns_for_stale_session(self) -> None:
        args = _chat_args()
        runtime_state = chat_command.ChatRuntimeState(
            endpoint=None,
            transport="in-process",
            inproc_runtime=None,
            mode="single-process",
            auto_start=False,
            show_progress=False,
            quiet=False,
        )
        session_record = SimpleNamespace(status="stale", metadata={})

        with (
            mock.patch(
                "openminion.cli.commands.chat.load_config",
                return_value=_config(mode="single-process"),
            ),
            mock.patch(
                "openminion.cli.commands.chat.init_runtime_state",
                return_value=(runtime_state, None),
            ),
            mock.patch(
                "openminion.cli.commands.chat._mark_stale_cli_sessions",
                return_value=1,
            ),
            mock.patch(
                "openminion.cli.commands.chat._get_session_record",
                return_value=session_record,
            ),
            mock.patch(
                "openminion.cli.commands.chat._emit_session_event_safe"
            ) as emit_event,
            mock.patch("openminion.cli.commands.chat.close_runtime"),
            mock.patch("builtins.input", side_effect=["/exit"]),
        ):
            buf = io.StringIO()
            with redirect_stdout(buf):
                code = chat_command.run_chat(args)

        self.assertEqual(code, 0)
        event_types = [
            call.kwargs["event_type"]
            for call in emit_event.call_args_list
            if "event_type" in call.kwargs
        ]
        self.assertEqual(event_types[:2], ["session.resumed", "client.attach"])
        self.assertIn("marked stale after inactivity", buf.getvalue())

    def test_new_session_command_closes_current_session_and_creates_new_one(
        self,
    ) -> None:
        args = _chat_args()
        runtime_state = chat_command.ChatRuntimeState(
            endpoint=None,
            transport="in-process",
            inproc_runtime=None,
            mode="single-process",
            auto_start=False,
            show_progress=False,
            quiet=False,
        )

        with (
            mock.patch(
                "openminion.cli.commands.chat.load_config",
                return_value=_config(mode="single-process"),
            ),
            mock.patch(
                "openminion.cli.commands.chat.init_runtime_state",
                return_value=(runtime_state, None),
            ),
            mock.patch(
                "openminion.cli.commands.chat._mark_stale_cli_sessions",
                return_value=0,
            ),
            mock.patch(
                "openminion.cli.commands.chat._get_session_record",
                return_value=None,
            ),
            mock.patch(
                "openminion.cli.commands.chat._emit_session_open_events"
            ) as emit_open,
            mock.patch(
                "openminion.cli.commands.chat._close_session_record"
            ) as close_session,
            mock.patch("openminion.cli.commands.chat.close_runtime"),
            mock.patch(
                "builtins.input",
                side_effect=["/new session", "/exit"],
            ),
        ):
            code = chat_command.run_chat(args)

        self.assertEqual(code, 0)
        close_session.assert_called_once_with(
            session_id="ops-chat",
            config_path="test-configs.json",
            reason="new_session",
        )
        created_session_id = emit_open.call_args_list[1].kwargs["session_id"]
        self.assertNotEqual(created_session_id, "ops-chat")
        self.assertTrue(created_session_id.startswith("sess-"))

    def test_explicit_session_name_sets_name_only_when_missing(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_path = Path(tmpdir)
            db_path = tmp_path / "state" / "openminion.db"
            migrate_database(db_path)
            connection = connect_database(db_path)
            try:
                store = SessionStore(connection)
                session = store.resolve_session(
                    agent_id="main",
                    channel="console",
                    target="cli-chat",
                    session_id="named-session",
                )
            finally:
                connection.close()

            roots = SimpleNamespace(home_root=tmp_path, data_root=tmp_path, env={})
            with mock.patch(
                "openminion.cli.commands.chat.resolve_cli_roots",
                return_value=roots,
            ):
                applied = chat_command._set_session_name_if_missing(
                    session_id=session.id,
                    config_path="cfg.json",
                    name="First Session Name",
                )
                skipped = chat_command._set_session_name_if_missing(
                    session_id=session.id,
                    config_path="cfg.json",
                    name="Second Name",
                )

            connection = connect_database(db_path)
            try:
                store = SessionStore(connection)
                reloaded = store.get_session(session.id)
            finally:
                connection.close()

        self.assertTrue(applied)
        self.assertFalse(skipped)
        assert reloaded is not None
        self.assertEqual(reloaded.metadata.get("name"), "First Session Name")

    def test_auto_name_session_uses_first_user_message_once_response_exists(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_path = Path(tmpdir)
            db_path = tmp_path / "state" / "openminion.db"
            migrate_database(db_path)
            connection = connect_database(db_path)
            try:
                store = SessionStore(connection)
                session = store.resolve_session(
                    agent_id="main",
                    channel="console",
                    target="cli-chat",
                    session_id="auto-named-session",
                )
                store.append_message(
                    session_id=session.id,
                    role="inbound",
                    body="Summarize the latest release notes",
                )
                store.append_message(
                    session_id=session.id,
                    role="outbound",
                    body="Here is the summary.",
                )
            finally:
                connection.close()

            roots = SimpleNamespace(home_root=tmp_path, data_root=tmp_path, env={})
            with mock.patch(
                "openminion.cli.commands.chat.resolve_cli_roots",
                return_value=roots,
            ):
                renamed = chat_command._maybe_auto_name_session(
                    session_id=session.id,
                    config_path="cfg.json",
                    first_user_text="Summarize the latest release notes",
                )
                skipped = chat_command._maybe_auto_name_session(
                    session_id=session.id,
                    config_path="cfg.json",
                    first_user_text="Summarize the latest release notes",
                )

            connection = connect_database(db_path)
            try:
                store = SessionStore(connection)
                reloaded = store.get_session(session.id)
            finally:
                connection.close()

        self.assertTrue(renamed)
        self.assertFalse(skipped)
        assert reloaded is not None
        self.assertEqual(
            reloaded.metadata.get("name"),
            "Summarize the latest release notes",
        )

    def test_chat_ready_resume_warns_when_reusing_prior_conversation(self) -> None:
        args = _chat_args(resume=True)

        with (
            mock.patch(
                "openminion.cli.commands.chat.load_config",
                return_value=_config(mode="single-process", provider="openrouter"),
            ),
            mock.patch(
                "openminion.cli.commands.chat._latest_session_conversation_id",
                return_value="conv-prev",
            ),
            mock.patch("builtins.input", side_effect=["/exit"]),
        ):
            buf = io.StringIO()
            with redirect_stdout(buf):
                code = chat_command.run_chat(args)

        self.assertEqual(code, 0)
        self.assertIn(
            "resuming conversation 'conv-prev' for session 'ops-chat'", buf.getvalue()
        )

    def test_daemon_turn_error_keeps_daemon_transport(self) -> None:
        args = Namespace(
            config="test-configs.json",
            agent="ops",
            session="ops-chat",
            quiet=False,
            no_progress=True,
        )
        endpoint = SimpleNamespace(host="127.0.0.1", port=18789)

        with (
            mock.patch(
                "openminion.cli.commands.chat.load_config",
                return_value=_config(mode="daemon"),
            ),
            mock.patch(
                "openminion.cli.commands.chat.ensure_daemon_running",
                return_value=endpoint,
            ),
            mock.patch(
                "openminion.cli.commands.chat.daemon_request",
                return_value=(
                    500,
                    {"ok": False, "error": {"message": "provider failed"}},
                ),
            ) as daemon_request,
            mock.patch(
                "openminion.cli.commands.chat.APIRuntime.from_config_path"
            ) as runtime_ctor,
            mock.patch("openminion.cli.commands.chat.run_turn") as run_turn,
            mock.patch("builtins.input", side_effect=["hello", "/exit"]),
        ):
            buf = io.StringIO()
            with redirect_stdout(buf):
                code = chat_command.run_chat(args)

        self.assertEqual(code, 0)
        self.assertIn(
            "[chat] turn failed: daemon request failed (500): provider failed",
            buf.getvalue(),
        )
        self.assertNotIn("falling back to in-process runtime", buf.getvalue())
        turn_calls = [
            call
            for call in daemon_request.call_args_list
            if call.kwargs.get("path") == "/v1/turn/stream"
        ]
        self.assertEqual(len(turn_calls), 1)
        turn_payload = turn_calls[0].kwargs["payload"]
        self.assertTrue(
            str(turn_payload.get("idempotency_key", "")).startswith("cli-chat:")
        )
        self.assertEqual(turn_payload.get("timeout_seconds"), 90.0)
        runtime_ctor.assert_not_called()
        run_turn.assert_not_called()

    def test_chat_falls_back_before_first_turn_on_daemon_config_mismatch(self) -> None:
        args = Namespace(
            config="test-configs.json",
            agent="ops",
            session="ops-chat",
            quiet=False,
            no_progress=True,
        )
        runtime = mock.Mock()

        with (
            mock.patch.object(
                chat_command, "load_config", return_value=_config(mode="daemon")
            ),
            mock.patch.object(
                chat_command,
                "ensure_daemon_running",
                side_effect=RuntimeError(
                    "openminion daemon endpoint is occupied by a different config "
                    "(expected /expected/config.json, got /other/config.json)."
                ),
            ),
            mock.patch.object(
                chat_command.APIRuntime, "from_config_path", return_value=runtime
            ),
            mock.patch.object(
                chat_command,
                "run_turn",
                return_value={"body": "ops: inproc ok"},
            ) as run_turn,
            mock.patch("builtins.input", side_effect=["hello", "/exit"]),
        ):
            buf = io.StringIO()
            with redirect_stdout(buf):
                code = chat_command.run_chat(args)

        self.assertEqual(code, 0)
        output = buf.getvalue()
        self.assertIn("falling back to in-process runtime", output)
        self.assertIn("occupied by a different config", output)
        self.assertIn("transport=in-process", output)
        self.assertIn("[ops-chat|ops] ops: inproc ok", _normalize_cli_output(output))
        run_turn.assert_called_once()

    def test_inproc_transient_turn_failure_retries_once(self) -> None:
        args = Namespace(
            config="test-configs.json",
            agent="ops",
            session="ops-chat",
            quiet=False,
            no_progress=True,
        )
        runtime = mock.Mock()

        with (
            mock.patch(
                "openminion.cli.commands.chat.load_config",
                return_value=_config(mode="single-process"),
            ),
            mock.patch(
                "openminion.cli.commands.chat.APIRuntime.from_config_path",
                return_value=runtime,
            ),
            mock.patch(
                "openminion.cli.commands.chat.run_turn",
                side_effect=[
                    RuntimeError("request timed out"),
                    {"body": "ops: retry succeeded"},
                ],
            ) as run_turn,
            mock.patch("builtins.input", side_effect=["hello", "/exit"]),
        ):
            buf = io.StringIO()
            with redirect_stdout(buf):
                code = chat_command.run_chat(args)

        self.assertEqual(code, 0)
        self.assertEqual(run_turn.call_count, 2)
        output = buf.getvalue()
        self.assertIn(
            "[chat] transient failure, retrying (2/2): request timed out", output
        )
        self.assertIn(
            "[ops-chat|ops] ops: retry succeeded", _normalize_cli_output(output)
        )
        first_payload = run_turn.call_args_list[0].kwargs["payload"]
        second_payload = run_turn.call_args_list[1].kwargs["payload"]
        self.assertEqual(
            first_payload.get("idempotency_key"),
            second_payload.get("idempotency_key"),
        )
        self.assertEqual(first_payload.get("timeout_seconds"), 90.0)
        runtime.close.assert_called_once()

    def test_inproc_replayed_response_auto_retries_current_message(self) -> None:
        args = Namespace(
            config="test-configs.json",
            agent="ops",
            session="ops-chat",
            quiet=False,
            no_progress=True,
            reset_session=False,
            resume=False,
        )
        runtime = mock.Mock()

        with (
            mock.patch(
                "openminion.cli.commands.chat.load_config",
                return_value=_config(mode="single-process"),
            ),
            mock.patch(
                "openminion.cli.commands.chat.APIRuntime.from_config_path",
                return_value=runtime,
            ),
            mock.patch(
                "openminion.cli.commands.chat.run_turn",
                side_effect=[
                    {
                        "body": "ops: stale pending response",
                        "metadata": {"replayed_response": "true"},
                    },
                    {"body": "ops: fresh response", "metadata": {}},
                ],
            ) as run_turn,
            mock.patch("builtins.input", side_effect=["hello", "/exit"]),
        ):
            buf = io.StringIO()
            with redirect_stdout(buf):
                code = chat_command.run_chat(args)

        self.assertEqual(code, 0)
        self.assertEqual(run_turn.call_count, 2)
        output = buf.getvalue()
        self.assertIn("pending response replayed; retrying your message once", output)
        self.assertIn(
            "[ops-chat|ops] ops: stale pending response",
            _normalize_cli_output(output),
        )
        self.assertIn(
            "[ops-chat|ops] ops: fresh response", _normalize_cli_output(output)
        )
        runtime.close.assert_called_once()

    def test_inproc_fail_closed_contract_body_retries_current_message(self) -> None:
        args = Namespace(
            config="test-configs.json",
            agent="ops",
            session="ops-chat",
            quiet=False,
            no_progress=True,
            reset_session=False,
            resume=False,
        )
        runtime = mock.Mock()

        with (
            mock.patch(
                "openminion.cli.commands.chat.load_config",
                return_value=_config(mode="single-process"),
            ),
            mock.patch(
                "openminion.cli.commands.chat.APIRuntime.from_config_path",
                return_value=runtime,
            ),
            mock.patch(
                "openminion.cli.commands.chat.run_turn",
                side_effect=[
                    {
                        "body": (
                            "General act work ended without the required typed "
                            "finalization_status contract."
                        ),
                        "metadata": {},
                    },
                    {"body": "ops: fresh response", "metadata": {}},
                ],
            ) as run_turn,
            mock.patch("builtins.input", side_effect=["hello", "/exit"]),
        ):
            buf = io.StringIO()
            with redirect_stdout(buf):
                code = chat_command.run_chat(args)

        self.assertEqual(code, 0)
        self.assertEqual(run_turn.call_count, 2)
        output = buf.getvalue()
        self.assertIn(
            "[chat] transient failure, retrying (2/2): The model ended the turn without the required completion contract. Please try again.",
            output,
        )
        self.assertIn(
            "[ops-chat|ops] ops: fresh response", _normalize_cli_output(output)
        )
        self.assertNotIn(
            "General act work ended without the required typed finalization_status contract.",
            output,
        )
        runtime.close.assert_called_once()

    def test_inproc_distinct_user_turns_get_distinct_idempotency_keys(self) -> None:
        args = Namespace(
            config="test-configs.json",
            agent="ops",
            session="ops-chat",
            quiet=False,
            no_progress=True,
            reset_session=False,
            resume=False,
        )
        runtime = mock.Mock()

        with (
            mock.patch(
                "openminion.cli.commands.chat.load_config",
                return_value=_config(mode="single-process"),
            ),
            mock.patch(
                "openminion.cli.commands.chat.APIRuntime.from_config_path",
                return_value=runtime,
            ),
            mock.patch(
                "openminion.cli.commands.chat.run_turn",
                side_effect=[
                    {"body": "ops: first", "metadata": {}},
                    {"body": "ops: second", "metadata": {}},
                ],
            ) as run_turn,
            mock.patch("builtins.input", side_effect=["hello", "hello", "/exit"]),
        ):
            code = chat_command.run_chat(args)

        self.assertEqual(code, 0)
        self.assertEqual(run_turn.call_count, 2)
        first_payload = run_turn.call_args_list[0].kwargs["payload"]
        second_payload = run_turn.call_args_list[1].kwargs["payload"]
        self.assertNotEqual(
            first_payload.get("idempotency_key"),
            second_payload.get("idempotency_key"),
        )
        runtime.close.assert_called_once()

    def test_new_command_rotates_conversation_scope(self) -> None:
        args = _chat_args()
        runtime = mock.Mock()

        with (
            mock.patch(
                "openminion.cli.commands.chat.load_config",
                return_value=_config(mode="single-process"),
            ),
            mock.patch(
                "openminion.cli.commands.chat.APIRuntime.from_config_path",
                return_value=runtime,
            ),
            mock.patch(
                "openminion.cli.commands.chat.run_turn",
                side_effect=[
                    {"body": "ops: one", "metadata": {}},
                    {"body": "ops: two", "metadata": {}},
                ],
            ) as run_turn,
            mock.patch(
                "builtins.input",
                side_effect=["hello", "/new", "next", "/exit"],
            ),
        ):
            code = chat_command.run_chat(args)

        self.assertEqual(code, 0)
        self.assertEqual(run_turn.call_count, 2)
        first_payload = run_turn.call_args_list[0].kwargs["payload"]
        second_payload = run_turn.call_args_list[1].kwargs["payload"]
        first_meta = first_payload["inbound_metadata"]
        second_meta = second_payload["inbound_metadata"]
        self.assertNotEqual(
            first_meta.get("conversation_id", ""),
            second_meta.get("conversation_id", ""),
        )
        self.assertEqual(
            second_meta.get("thread_id", ""),
            second_meta.get("conversation_id", ""),
        )
        runtime.close.assert_called_once()

    def test_new_command_bypasses_restart_reuse_path(self) -> None:
        args = _chat_args()
        runtime = mock.Mock()

        with (
            mock.patch(
                "openminion.cli.commands.chat.load_config",
                return_value=_config(mode="single-process"),
            ),
            mock.patch(
                "openminion.cli.commands.chat._latest_session_conversation_id",
                return_value="conv-prev",
            ),
            mock.patch(
                "openminion.cli.commands.chat.APIRuntime.from_config_path",
                return_value=runtime,
            ),
            mock.patch(
                "openminion.cli.commands.chat.run_turn",
                side_effect=[
                    {"body": "ops: one", "metadata": {}},
                    {"body": "ops: two", "metadata": {}},
                ],
            ) as run_turn,
            mock.patch(
                "builtins.input",
                side_effect=["hello", "/new", "next", "/exit"],
            ),
        ):
            code = chat_command.run_chat(args)

        self.assertEqual(code, 0)
        first_meta = run_turn.call_args_list[0].kwargs["payload"]["inbound_metadata"]
        second_meta = run_turn.call_args_list[1].kwargs["payload"]["inbound_metadata"]
        self.assertEqual(first_meta.get("conversation_id"), "conv-prev")
        self.assertNotEqual(second_meta.get("conversation_id"), "conv-prev")
        self.assertTrue(second_meta.get("conversation_id", "").startswith("conv-"))
        self.assertEqual(
            second_meta.get("thread_id", ""),
            second_meta.get("conversation_id", ""),
        )
        runtime.close.assert_called_once()

    def test_restart_reuses_latest_session_conversation_by_default(self) -> None:
        args = _chat_args()
        runtime = mock.Mock()

        with (
            mock.patch(
                "openminion.cli.commands.chat.load_config",
                return_value=_config(mode="single-process"),
            ),
            mock.patch(
                "openminion.cli.commands.chat._latest_session_conversation_id",
                return_value="conv-prev",
            ),
            mock.patch(
                "openminion.cli.commands.chat.APIRuntime.from_config_path",
                return_value=runtime,
            ),
            mock.patch(
                "openminion.cli.commands.chat.run_turn",
                return_value={"body": "ops: hi", "metadata": {}},
            ) as run_turn,
            mock.patch("builtins.input", side_effect=["hello", "/exit"]),
        ):
            code = chat_command.run_chat(args)

        self.assertEqual(code, 0)
        payload = run_turn.call_args.kwargs["payload"]
        inbound_metadata = payload["inbound_metadata"]
        self.assertEqual(inbound_metadata.get("conversation_id"), "conv-prev")
        self.assertEqual(inbound_metadata.get("thread_id"), "")
        self.assertEqual(inbound_metadata.get("resume"), "true")
        runtime.close.assert_called_once()

    def test_chat_runner_refreshes_thread_id_from_turn_metadata(self) -> None:
        args = _chat_args()
        runtime = mock.Mock()

        with (
            mock.patch(
                "openminion.cli.commands.chat.load_config",
                return_value=_config(mode="single-process"),
            ),
            mock.patch(
                "openminion.cli.commands.chat.APIRuntime.from_config_path",
                return_value=runtime,
            ),
            mock.patch(
                "openminion.cli.commands.chat.run_turn",
                side_effect=[
                    {
                        "body": "ops: first",
                        "metadata": {
                            "conversation_id": "conv-1",
                            "thread_id": "thread-real-1",
                        },
                    },
                    {"body": "ops: second", "metadata": {}},
                ],
            ) as run_turn,
            mock.patch("builtins.input", side_effect=["hello", "again", "/exit"]),
        ):
            code = chat_command.run_chat(args)

        self.assertEqual(code, 0)
        self.assertEqual(run_turn.call_count, 2)
        second_payload = run_turn.call_args_list[1].kwargs["payload"]
        self.assertEqual(
            second_payload["inbound_metadata"].get("thread_id"),
            "thread-real-1",
        )
        runtime.close.assert_called_once()

    def test_debug_command_preserves_last_turn_metadata_after_failed_turn(self) -> None:
        args = _chat_args()
        runtime = mock.Mock()

        with (
            mock.patch(
                "openminion.cli.commands.chat.load_config",
                return_value=_config(mode="single-process"),
            ),
            mock.patch(
                "openminion.cli.commands.chat.APIRuntime.from_config_path",
                return_value=runtime,
            ),
            mock.patch(
                "openminion.cli.commands.chat._execute_turn",
                side_effect=[
                    {
                        "stop": True,
                        "last_turn_debug": {
                            "source": "in_process",
                            "metadata": {
                                "error": "finalization_status contract missing",
                                "trace_id": "trace-failed",
                            },
                            "failure_message": (
                                "The model ended the turn without the required "
                                "completion contract. Please try again."
                            ),
                        },
                    }
                ],
            ),
            mock.patch("builtins.input", side_effect=["hello", "/debug", "/exit"]),
        ):
            buf = io.StringIO()
            with redirect_stdout(buf):
                code = chat_command.run_chat(args)

        self.assertEqual(code, 0)
        output = buf.getvalue()
        self.assertIn('"last_turn": {', output)
        self.assertIn('"trace_id": "trace-failed"', output)
        self.assertIn('"failure_message":', output)
        runtime.close.assert_called_once()

    def test_reset_session_generates_fresh_conversation_even_with_prior_history(
        self,
    ) -> None:
        args = _chat_args(reset_session=True)
        runtime = mock.Mock()

        with (
            mock.patch(
                "openminion.cli.commands.chat.load_config",
                return_value=_config(mode="single-process"),
            ),
            mock.patch(
                "openminion.cli.commands.chat._latest_session_conversation_id",
                return_value="conv-prev",
            ),
            mock.patch(
                "openminion.cli.commands.chat.APIRuntime.from_config_path",
                return_value=runtime,
            ),
            mock.patch(
                "openminion.cli.commands.chat.run_turn",
                return_value={"body": "ops: hi", "metadata": {}},
            ) as run_turn,
            mock.patch("builtins.input", side_effect=["hello", "/exit"]),
        ):
            code = chat_command.run_chat(args)

        self.assertEqual(code, 0)
        inbound_metadata = run_turn.call_args.kwargs["payload"]["inbound_metadata"]
        self.assertNotEqual(inbound_metadata.get("conversation_id"), "conv-prev")
        self.assertTrue(inbound_metadata.get("conversation_id", "").startswith("conv-"))
        runtime.close.assert_called_once()

    def test_session_switch_reuses_latest_conversation_for_new_session(self) -> None:
        args = _chat_args()
        runtime = mock.Mock()

        def _latest(*, session_id: str, config_path: str | None) -> str:
            del config_path
            return {
                "ops-chat": "conv-ops",
                "next-session": "conv-next",
            }.get(session_id, "")

        with (
            mock.patch(
                "openminion.cli.commands.chat.load_config",
                return_value=_config(mode="single-process"),
            ),
            mock.patch(
                "openminion.cli.commands.chat._latest_session_conversation_id",
                side_effect=_latest,
            ),
            mock.patch(
                "openminion.cli.commands.chat.APIRuntime.from_config_path",
                return_value=runtime,
            ),
            mock.patch(
                "openminion.cli.commands.chat.run_turn",
                return_value={"body": "ops: switched", "metadata": {}},
            ) as run_turn,
            mock.patch(
                "builtins.input",
                side_effect=["/session next-session", "hello", "/exit"],
            ),
        ):
            code = chat_command.run_chat(args)

        self.assertEqual(code, 0)
        payload = run_turn.call_args.kwargs["payload"]
        self.assertEqual(payload["session_id"], "next-session")
        self.assertEqual(
            payload["inbound_metadata"].get("conversation_id"), "conv-next"
        )
        runtime.close.assert_called_once()

    def test_explicit_conversation_override_still_wins_over_restart_reuse(self) -> None:
        args = _chat_args(conversation="fixed-conv")
        runtime = mock.Mock()

        with (
            mock.patch(
                "openminion.cli.commands.chat.load_config",
                return_value=_config(mode="single-process"),
            ),
            mock.patch(
                "openminion.cli.commands.chat._latest_session_conversation_id",
                return_value="conv-prev",
            ),
            mock.patch(
                "openminion.cli.commands.chat.APIRuntime.from_config_path",
                return_value=runtime,
            ),
            mock.patch(
                "openminion.cli.commands.chat.run_turn",
                return_value={"body": "ops: fixed", "metadata": {}},
            ) as run_turn,
            mock.patch("builtins.input", side_effect=["hello", "/exit"]),
        ):
            code = chat_command.run_chat(args)

        self.assertEqual(code, 0)
        self.assertEqual(
            run_turn.call_args.kwargs["payload"]["inbound_metadata"].get(
                "conversation_id"
            ),
            "fixed-conv",
        )
        runtime.close.assert_called_once()

    def test_run_chat_does_not_inject_cli_introspection_intent_metadata(self) -> None:
        args = Namespace(
            config="test-configs.json",
            agent="ops",
            session="ops-chat",
            quiet=False,
            no_progress=True,
        )
        runtime = mock.Mock()

        with (
            mock.patch(
                "openminion.cli.commands.chat.load_config",
                return_value=_config(mode="single-process"),
            ),
            mock.patch(
                "openminion.cli.commands.chat.APIRuntime.from_config_path",
                return_value=runtime,
            ),
            mock.patch(
                "openminion.cli.commands.chat.run_turn",
                return_value={"body": "ops: memory summary", "metadata": {}},
            ) as run_turn,
            mock.patch(
                "builtins.input",
                side_effect=["what do you remember", "/exit"],
            ),
        ):
            code = chat_command.run_chat(args)

        self.assertEqual(code, 0)
        payload = run_turn.call_args.kwargs["payload"]
        inbound_metadata = payload["inbound_metadata"]
        self.assertNotIn("introspection_intent", inbound_metadata)
        runtime.close.assert_called_once()

    def test_new_command_is_ignored_for_fixed_conversation(self) -> None:
        args = Namespace(
            config="test-configs.json",
            agent="ops",
            session="ops-chat",
            quiet=False,
            no_progress=True,
            conversation="fixed-conv",
        )
        runtime = mock.Mock()

        with (
            mock.patch(
                "openminion.cli.commands.chat.load_config",
                return_value=_config(mode="single-process"),
            ),
            mock.patch(
                "openminion.cli.commands.chat.APIRuntime.from_config_path",
                return_value=runtime,
            ),
            mock.patch(
                "openminion.cli.commands.chat.run_turn",
                return_value={"body": "ops: done", "metadata": {}},
            ) as run_turn,
            mock.patch(
                "builtins.input",
                side_effect=["/new", "hello", "/exit"],
            ),
        ):
            buf = io.StringIO()
            with redirect_stdout(buf):
                code = chat_command.run_chat(args)

        self.assertEqual(code, 0)
        self.assertEqual(run_turn.call_count, 1)
        payload = run_turn.call_args.kwargs["payload"]
        self.assertEqual(
            payload["inbound_metadata"].get("conversation_id", ""),
            "fixed-conv",
        )
        self.assertIn("/new ignored because conversation id is fixed", buf.getvalue())
        runtime.close.assert_called_once()

    def test_load_session_debug_snapshot_reports_continuity_signals(self) -> None:
        from openminion.cli.chat.session import load_session_debug_snapshot

        session_id = "debug-session"
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "state.db"
            conn = sqlite3.connect(str(db_path))
            conn.execute(
                """
                CREATE TABLE session_events (
                    seq INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id TEXT,
                    event_type TEXT,
                    timestamp TEXT,
                    payload_json TEXT
                )
                """
            )
            conn.executemany(
                """
                INSERT INTO session_events(session_id, event_type, timestamp, payload_json)
                VALUES (?, ?, ?, ?)
                """,
                [
                    (
                        session_id,
                        "llm.call.started",
                        "2026-03-05T10:00:00Z",
                        json.dumps({"llm_call_id": "call-1"}),
                    ),
                    (
                        session_id,
                        "context.manifest.created",
                        "2026-03-05T10:00:01Z",
                        json.dumps(
                            {
                                "llm_call_id": "call-1",
                                "prompt_context_id": "ctx-1",
                                "pack_policy_used": "default",
                                "compressors_used": ["rolling", "summary"],
                                "included_segment_ids": ["seg-1", "seg-2"],
                                "dropped_segment_ids": ["seg-3"],
                            }
                        ),
                    ),
                    (
                        session_id,
                        "llm.call.completed",
                        "2026-03-05T10:00:02Z",
                        json.dumps({"llm_call_id": "call-1"}),
                    ),
                    (
                        session_id,
                        "summary.updated",
                        "2026-03-05T10:00:03Z",
                        json.dumps({"summary": "ok"}),
                    ),
                    (
                        session_id,
                        "session.compaction.archive",
                        "2026-03-05T10:00:04Z",
                        json.dumps({"refs": ["r1"]}),
                    ),
                    (
                        session_id,
                        "compression.checkpoint.created",
                        "2026-03-05T10:00:05Z",
                        json.dumps({"checkpoint_id": "cp-1"}),
                    ),
                ],
            )
            conn.commit()
            conn.close()

            snapshot = load_session_debug_snapshot(
                storage_path=str(db_path),
                session_id=session_id,
            )

        self.assertTrue(snapshot["available"])
        self.assertEqual(snapshot["latest_manifest"]["llm_call_id"], "call-1")
        self.assertEqual(snapshot["latest_manifest"]["included_segments"], 2)
        self.assertEqual(snapshot["latest_manifest"]["dropped_segments"], 1)
        self.assertGreaterEqual(len(snapshot["event_tail"]), 1)
        self.assertTrue(snapshot["continuity_checks"]["llm_pipeline_complete"])
        self.assertTrue(snapshot["continuity_checks"]["continuity_pipeline_active"])

    def test_load_session_debug_snapshot_falls_back_to_events_table(self) -> None:
        from openminion.cli.chat.session import load_session_debug_snapshot

        session_id = "legacy-events-session"
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "state.db"
            conn = sqlite3.connect(str(db_path))
            conn.execute(
                """
                CREATE TABLE core_events (
                    namespace TEXT,
                    event_id TEXT,
                    session_id TEXT,
                    ts TEXT,
                    agent_id TEXT,
                    trace_id TEXT,
                    event_type TEXT,
                    payload_json TEXT,
                    blob_refs_json TEXT
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id TEXT,
                    event_type TEXT,
                    payload_json TEXT,
                    created_at TEXT
                )
                """
            )
            conn.executemany(
                """
                INSERT INTO events(session_id, event_type, payload_json, created_at)
                VALUES (?, ?, ?, ?)
                """,
                [
                    (
                        session_id,
                        "run.running",
                        json.dumps({"run_id": "r1"}),
                        "2026-03-05T10:00:00Z",
                    ),
                    (
                        session_id,
                        "run.responding",
                        json.dumps({"run_id": "r1"}),
                        "2026-03-05T10:00:01Z",
                    ),
                    (
                        session_id,
                        "run.completed",
                        json.dumps({"run_id": "r1"}),
                        "2026-03-05T10:00:02Z",
                    ),
                ],
            )
            conn.commit()
            conn.close()

            snapshot = load_session_debug_snapshot(
                storage_path=str(db_path),
                session_id=session_id,
            )

        self.assertTrue(snapshot["available"])
        self.assertEqual(snapshot["event_source"], "events")
        self.assertEqual(len(snapshot["event_tail"]), 3)
        self.assertEqual(snapshot["event_tail"][0]["event_type"], "run.running")

    def test_load_session_debug_snapshot_reports_memory_trace_signals(self) -> None:
        from openminion.cli.chat.session import load_session_debug_snapshot

        session_id = "memory-debug-session"
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "state.db"
            conn = sqlite3.connect(str(db_path))
            conn.execute(
                """
                CREATE TABLE session_events (
                    seq INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id TEXT,
                    event_type TEXT,
                    timestamp TEXT,
                    payload_json TEXT
                )
                """
            )
            conn.executemany(
                """
                INSERT INTO session_events(session_id, event_type, timestamp, payload_json)
                VALUES (?, ?, ?, ?)
                """,
                [
                    (
                        session_id,
                        "memory.context.built",
                        "2026-03-11T10:00:00Z",
                        json.dumps(
                            {
                                "run_id": "run-1",
                                "strategy": "refresh_on_write",
                                "capsule_chars": "128",
                            }
                        ),
                    ),
                    (
                        session_id,
                        "memory.turn.recorded",
                        "2026-03-11T10:00:01Z",
                        json.dumps(
                            {
                                "run_id": "run-1",
                                "facts_added": "1",
                                "changed": "true",
                            }
                        ),
                    ),
                    (
                        session_id,
                        "memory.capsule.refreshed",
                        "2026-03-11T10:00:02Z",
                        json.dumps(
                            {
                                "run_id": "run-1",
                                "changed": "true",
                                "before_fingerprint": "",
                                "after_fingerprint": "abc123",
                            }
                        ),
                    ),
                ],
            )
            conn.commit()
            conn.close()

            snapshot = load_session_debug_snapshot(
                storage_path=str(db_path),
                session_id=session_id,
            )

        self.assertTrue(snapshot["available"])
        self.assertEqual(snapshot["memory_event_counts"]["memory.context.built"], 1)
        self.assertEqual(snapshot["memory_event_counts"]["memory.turn.recorded"], 1)
        self.assertEqual(snapshot["memory_event_counts"]["memory.capsule.refreshed"], 1)
        self.assertTrue(snapshot["continuity_checks"]["memory_trace_active"])
        self.assertEqual(
            snapshot["memory_trace"]["latest_capsule_refreshed"]["payload"]["run_id"],
            "run-1",
        )
        self.assertEqual(
            snapshot["memory_trace"]["latest_turn_recorded"]["payload"]["facts_added"],
            "1",
        )


def _write_identity_profile(
    db_path: Path,
    *,
    agent_id: str,
    mission: str,
    tone: str,
    meta: dict[str, object] | None = None,
) -> None:
    db_path = db_path.expanduser().resolve()
    db_path.parent.mkdir(parents=True, exist_ok=True)
    ctl = IdentityCtl(store=SQLiteIdentityStore(sqlite_path=str(db_path)))
    try:
        profile = AgentProfile(
            agent_id=agent_id,
            display_name=agent_id,
            profile_revision=1,
            role=RoleSpec(
                mission=mission,
                responsibilities=["Provide clear debug identity metadata."],
                hard_constraints=["Do not fabricate profile state."],
                domain=["cli"],
                escalation_rules=[],
            ),
            personality=PersonalitySpec(
                tone=tone,
                verbosity="normal",
                formatting=[],
                interaction_style=[],
            ),
            risk=RiskSpec(
                risk_level="medium",
                confirm_before=["destructive_actions"],
                auto_proceed_rules=[],
            ),
            tool_posture=ToolPostureSpec(
                tool_use="allowed",
                blocked_patterns=[],
                allowed_tools=[],
                sandbox_root=None,
            ),
            meta=dict(meta or {}),
        )
        ctl.upsert_profile(profile)
    finally:
        ctl.close()


class MenuAndPairingTests(unittest.TestCase):
    def test_print_grouped_menu_renders_sections(self) -> None:
        from openminion.cli.chat.ui import print_grouped_menu

        cfg = SimpleNamespace(
            runtime=SimpleNamespace(menu_pairing_enabled=True),
        )
        buf = io.StringIO()
        with redirect_stdout(buf):
            print_grouped_menu(config=cfg)
        output = buf.getvalue()
        self.assertIn("=== SESSION ===", output)
        self.assertIn("=== AGENT ===", output)
        self.assertIn("=== TOOLS & DEBUG ===", output)
        self.assertIn("/trust <cat>", output)
        self.assertIn("/untrust <cat>", output)
        self.assertIn("/grants", output)
        self.assertIn("=== PAIRING ===", output)
        self.assertIn("=== CONTROL ===", output)

    def test_print_grouped_menu_includes_pairing_commands_when_enabled(self) -> None:
        from openminion.cli.chat.ui import print_grouped_menu

        cfg = SimpleNamespace(
            runtime=SimpleNamespace(menu_pairing_enabled=True),
        )
        buf = io.StringIO()
        with redirect_stdout(buf):
            print_grouped_menu(config=cfg)
        output = buf.getvalue()
        self.assertIn("/pair status", output)
        self.assertIn("/pair create", output)

    def test_print_grouped_menu_shows_disabled_when_pairing_disabled(self) -> None:
        from openminion.cli.chat.ui import print_grouped_menu

        cfg = SimpleNamespace(
            runtime=SimpleNamespace(menu_pairing_enabled=False),
        )
        buf = io.StringIO()
        with redirect_stdout(buf):
            print_grouped_menu(config=cfg)
        output = buf.getvalue()
        self.assertIn("=== PAIRING ===", output)
        self.assertIn("pairing disabled", output.lower())

    def test_pair_status_handler_exists(self) -> None:
        from openminion.cli.chat.commands import _handle_pair_status

        self.assertTrue(callable(_handle_pair_status))

    def test_pair_create_handler_exists(self) -> None:
        from openminion.cli.chat.commands import _handle_pair_create

        self.assertTrue(callable(_handle_pair_create))

    def test_pair_revoke_handler_exists(self) -> None:
        from openminion.cli.chat.commands import _handle_pair_revoke

        self.assertTrue(callable(_handle_pair_revoke))

    def test_handle_pair_status_shows_disabled_when_config_disabled(self) -> None:
        from openminion.cli.chat.commands import _handle_pair_status

        cfg = SimpleNamespace(
            runtime=SimpleNamespace(menu_pairing_enabled=False),
        )
        buf = io.StringIO()
        with redirect_stdout(buf):
            _handle_pair_status(config=cfg)
        output = buf.getvalue()
        self.assertIn("disabled", output.lower())

    def test_handle_pair_create_shows_usage_without_args(self) -> None:
        from openminion.cli.chat.commands import _handle_pair_create

        cfg = SimpleNamespace(
            runtime=SimpleNamespace(menu_pairing_enabled=True),
        )
        buf = io.StringIO()
        with redirect_stdout(buf):
            _handle_pair_create(line="/pair create", config=cfg)
        output = buf.getvalue()
        self.assertIn("Usage:", output)
        self.assertIn("--user-id", output)

    def test_handle_pair_create_uses_real_pairing_helper(self) -> None:
        from openminion.cli.chat.commands import _handle_pair_create
        from openminion.cli.commands import channel

        cfg = SimpleNamespace(
            runtime=SimpleNamespace(menu_pairing_enabled=True),
            config_path="/tmp/openminion-agent.json",
        )

        def _fake_create(**kwargs):
            self.assertEqual(kwargs["config_path"], "/tmp/openminion-agent.json")
            self.assertEqual(kwargs["user_id"], "123")
            self.assertEqual(kwargs["chat_id"], "456")
            return channel.PairTokenOutput(
                token="tok",
                token_hint="tok",
                token_hash_prefix="abc123",
                expires_at_iso="2026-07-01T00:00:00+00:00",
                scopes=["chat.interact"],
                deep_link="https://t.me/bot?start=tok",
            )

        original = channel.create_telegram_pair_token_for_cli
        channel.create_telegram_pair_token_for_cli = _fake_create
        try:
            buf = io.StringIO()
            with redirect_stdout(buf):
                _handle_pair_create(
                    line="/pair create --user-id 123 --chat-id 456",
                    config=cfg,
                )
        finally:
            channel.create_telegram_pair_token_for_cli = original

        output = buf.getvalue()
        self.assertIn("PAIR_TOKEN=tok", output)
        self.assertIn("PAIR_DEEP_LINK=https://t.me/bot?start=tok", output)

    def test_handle_pair_revoke_shows_usage_without_token(self) -> None:
        from openminion.cli.chat.commands import _handle_pair_revoke

        cfg = SimpleNamespace(
            runtime=SimpleNamespace(menu_pairing_enabled=True),
        )
        buf = io.StringIO()
        with redirect_stdout(buf):
            _handle_pair_revoke(line="/pair revoke", config=cfg)
        output = buf.getvalue()
        self.assertIn("Usage:", output)
        self.assertIn("--token-id", output)

    def test_theme_handler_exists(self) -> None:
        from openminion.cli.chat.ui import handle_theme

        self.assertTrue(callable(handle_theme))

    def test_chat_help_lines_include_new_commands(self) -> None:
        from openminion.cli.chat.ui import chat_help_lines

        lines = chat_help_lines()
        rendered = "\n".join(lines)
        self.assertIn("/theme", rendered)
        self.assertIn("/identity help", rendered)


class IdentityChatCommandTests(unittest.TestCase):
    def test_handle_identity_help_prints_usage(self) -> None:
        from openminion.cli.chat.commands import _handle_identity_command

        cfg = SimpleNamespace(identity=SimpleNamespace(root=""))
        buf = io.StringIO()
        with redirect_stdout(buf):
            _handle_identity_command(
                line="/identity help",
                config=cfg,
                agent_id="cortensor35",
            )
        output = buf.getvalue()
        self.assertIn("Identity commands:", output)
        self.assertIn("/identity list", output)

    def test_handle_identity_list_uses_builder(self) -> None:
        from openminion.cli.chat.commands import _handle_identity_command

        cfg = SimpleNamespace(identity=SimpleNamespace(root=""))

        class _Ctl:
            def list_profiles(self):
                return []

            def close(self):
                return None

        buf = io.StringIO()
        with (
            mock.patch(
                "openminion.cli.chat.commands._build_identityctl",
                return_value=(_Ctl(), "identity.db"),
            ),
            redirect_stdout(buf),
        ):
            _handle_identity_command(
                line="/identity list",
                config=cfg,
                agent_id="cortensor35",
            )
        output = buf.getvalue()
        self.assertIn("Identity DB:", output)
        self.assertIn("(no identity profiles found)", output)

    def test_handle_identity_show_defaults_to_active_agent(self) -> None:
        from openminion.cli.chat.commands import _handle_identity_command

        cfg = SimpleNamespace(identity=SimpleNamespace(root=""))

        class _Profile:
            def model_dump(self, mode="python", exclude_none=True):  # noqa: ARG002
                return {"agent_id": "cortensor35", "display_name": "Cortensor 35"}

        class _Ctl:
            last_agent_id = None

            def get_profile(self, agent_id):
                self.last_agent_id = agent_id
                return _Profile()

            def close(self):
                return None

        ctl = _Ctl()
        buf = io.StringIO()
        with (
            mock.patch(
                "openminion.cli.chat.commands._build_identityctl",
                return_value=(ctl, "identity.db"),
            ),
            redirect_stdout(buf),
        ):
            _handle_identity_command(
                line="/identity show",
                config=cfg,
                agent_id="cortensor35",
            )
        output = buf.getvalue()
        self.assertEqual(ctl.last_agent_id, "cortensor35")
        self.assertTrue("agent_id:" in output or "'agent_id':" in output)


class AgentInspectPairingTests(unittest.TestCase):
    def test_get_pairing_state_returns_dict(self) -> None:
        from openminion.cli.commands.agents import _get_pairing_state

        result = _get_pairing_state()
        self.assertIsInstance(result, dict)
        self.assertIn("available", result)
        self.assertIn("paired_channels", result)
        self.assertIn("pending_tokens", result)
        self.assertIn("last_pairing_event", result)

    def test_get_pairing_state_returns_available_false_when_no_store(self) -> None:
        from openminion.cli.commands.agents import _get_pairing_state

        result = _get_pairing_state()
        self.assertFalse(result["available"])
        self.assertEqual(result["paired_channels"], [])


class PolicyGrantChatCommandTests(unittest.TestCase):
    def _policy_config(self):
        return SimpleNamespace(
            action_policy=SimpleNamespace(
                mode="auto",
                default_action="require_confirm",
                allow_read_only_without_prompt=True,
                affirmative_tokens=[],
                negative_tokens=[],
            ),
            runtime=SimpleNamespace(menu_pairing_enabled=True),
        )

    def test_trust_untrust_grants_roundtrip_for_session(self) -> None:
        from openminion.cli.chat.commands import (
            _handle_grants_command,
            _handle_trust_command,
            _handle_untrust_command,
        )

        with tempfile.TemporaryDirectory() as tmp:
            old_data_root = os.environ.get("OPENMINION_DATA_ROOT")
            old_home_root = os.environ.get("OPENMINION_HOME")
            os.environ["OPENMINION_DATA_ROOT"] = str(Path(tmp) / ".openminion")
            os.environ["OPENMINION_HOME"] = str(Path(tmp).resolve())
            try:
                cfg = self._policy_config()
                session_id = "policy-chat-session-1"

                trust_buf = io.StringIO()
                with redirect_stdout(trust_buf):
                    _handle_trust_command(
                        line="/trust file /tmp/workspace",
                        config=cfg,
                        session_id=session_id,
                    )
                self.assertIn("trusted category=file", trust_buf.getvalue())

                grants_buf = io.StringIO()
                with redirect_stdout(grants_buf):
                    _handle_grants_command(config=cfg, session_id=session_id)
                grants_output = grants_buf.getvalue()
                self.assertIn(
                    "Active grants for session=policy-chat-session-1", grants_output
                )
                self.assertIn("tool=file method=read", grants_output)
                self.assertIn("tool=file method=write", grants_output)

                untrust_buf = io.StringIO()
                with redirect_stdout(untrust_buf):
                    _handle_untrust_command(
                        line="/untrust file",
                        config=cfg,
                        session_id=session_id,
                    )
                self.assertIn(
                    "untrusted category=file revoked=", untrust_buf.getvalue()
                )

                grants_after_buf = io.StringIO()
                with redirect_stdout(grants_after_buf):
                    _handle_grants_command(config=cfg, session_id=session_id)
                self.assertIn("(none)", grants_after_buf.getvalue())
            finally:
                if old_data_root is None:
                    os.environ.pop("OPENMINION_DATA_ROOT", None)
                else:
                    os.environ["OPENMINION_DATA_ROOT"] = old_data_root
                if old_home_root is None:
                    os.environ.pop("OPENMINION_HOME", None)
                else:
                    os.environ["OPENMINION_HOME"] = old_home_root

    def test_trust_unknown_category_prints_error(self) -> None:
        from openminion.cli.chat.commands import _handle_trust_command

        cfg = self._policy_config()
        buf = io.StringIO()
        with redirect_stdout(buf):
            _handle_trust_command(
                line="/trust unknown",
                config=cfg,
                session_id="policy-chat-session-2",
            )
        self.assertIn("unknown category", buf.getvalue())


class FileAliasIntegrationTests(unittest.TestCase):
    def test_file_list_dir_canonical_resolves(self) -> None:
        from openminion.modules.llm.providers.tool_calling import (
            _resolve_allowed_tool_name,
        )

        allowed_tools = {"file.list_dir", "file.read", "file.find"}
        resolved = _resolve_allowed_tool_name(
            "file.list_dir", allowed_tool_names=allowed_tools
        )
        self.assertEqual(resolved, "file.list_dir")

    def test_file_read_canonical_resolves(self) -> None:
        from openminion.modules.llm.providers.tool_calling import (
            _resolve_allowed_tool_name,
        )

        allowed_tools = {"file.list_dir", "file.read", "file.find"}
        resolved = _resolve_allowed_tool_name(
            "file.read", allowed_tool_names=allowed_tools
        )
        self.assertEqual(resolved, "file.read")

    def test_legacy_file_aliases_are_rejected(self) -> None:
        from openminion.modules.llm.providers.tool_calling import (
            _resolve_allowed_tool_name,
        )

        allowed_tools = {"file.list_dir", "file.read", "file.find"}
        self.assertIsNone(
            _resolve_allowed_tool_name("list_files", allowed_tool_names=allowed_tools)
        )
        self.assertIsNone(
            _resolve_allowed_tool_name("read_file", allowed_tool_names=allowed_tools)
        )
        self.assertIsNone(
            _resolve_allowed_tool_name("find_files", allowed_tool_names=allowed_tools)
        )

    def test_tool_not_allowed_returns_none(self) -> None:
        from openminion.modules.llm.providers.tool_calling import (
            _resolve_allowed_tool_name,
        )

        allowed_tools = {"file.read", "file.find"}
        resolved = _resolve_allowed_tool_name(
            "file.list_dir", allowed_tool_names=allowed_tools
        )
        self.assertIsNone(resolved)

    def test_unmapped_file_tool_returns_none(self) -> None:
        from openminion.modules.llm.providers.tool_calling import (
            _resolve_allowed_tool_name,
        )

        allowed_tools = {"file.list_dir", "file.read", "file.find"}
        resolved = _resolve_allowed_tool_name(
            "legacy.write", allowed_tool_names=allowed_tools
        )
        self.assertIsNone(resolved)

    def test_file_list_dir_case_insensitive_resolution(self) -> None:
        from openminion.modules.llm.providers.tool_calling import (
            _resolve_allowed_tool_name,
        )

        allowed_tools = {"file.list_dir"}
        resolved = _resolve_allowed_tool_name(
            "FILE.LIST_DIR", allowed_tool_names=allowed_tools
        )
        self.assertEqual(resolved, "file.list_dir")

    def test_submit_output_noncanonical_resolution(self) -> None:
        from openminion.modules.llm.providers.tool_calling import (
            _resolve_allowed_tool_name,
        )

        allowed_tools = {"submit_output"}
        resolved = _resolve_allowed_tool_name(
            "SUBMIT_OUTPUT", allowed_tool_names=allowed_tools
        )
        self.assertEqual(resolved, "submit_output")

    def test_tool_list_canonical_resolution_preserves_prefix(self) -> None:
        from openminion.modules.llm.providers.tool_calling import (
            _resolve_allowed_tool_name,
        )

        allowed_tools = {"tool.list"}
        resolved = _resolve_allowed_tool_name(
            "tool.list", allowed_tool_names=allowed_tools
        )
        self.assertEqual(resolved, "tool.list")


class RoomChatCommandTests(unittest.TestCase):
    def test_handle_repl_command_rotates_session_on_agent_switch(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            database_path = root / "state" / "openminion.db"
            migrate_database(database_path)
            connection = connect_database(database_path)
            store = SessionStore(connection)
            try:
                session = store.resolve_session(
                    agent_id="hello-agent",
                    channel="console",
                    target="cli-chat",
                    session_id="shared-chat",
                )
            finally:
                connection.close()

            args = _chat_args(config="ignored.json", session=session.id)
            runtime_state = chat_command.ChatRuntimeState(
                endpoint=None,
                transport="in-process",
                inproc_runtime=None,
                mode="single-process",
                auto_start=False,
                show_progress=False,
                quiet=True,
            )
            command_result = chat_command.ChatCommandResult(
                handled=True,
                agent_id="planner-safe",
                rotate_session_on_agent_change=True,
            )

            with mock.patch(
                "openminion.cli.commands.chat.resolve_cli_roots",
                return_value=SimpleNamespace(data_root=root, env={}),
            ):
                with mock.patch(
                    "openminion.cli.commands.chat._print_chat_provider_banner"
                ):
                    updated = chat_command._handle_repl_command(
                        command_result=command_result,
                        args=args,
                        config=_config(agent_name="hello-agent"),
                        runtime_state=runtime_state,
                        agent_id="hello-agent",
                        session_id=session.id,
                        conversation_selection={
                            "conversation_id": "conv-1",
                            "source": "fresh",
                        },
                        conversation_id="conv-1",
                        thread_id="thread-1",
                        attach_id="att-1",
                        lifecycle_payload={
                            "conversation_id": "conv-1",
                            "thread_id": "thread-1",
                            "attach_id": "att-1",
                        },
                        conversation_id_fixed=False,
                        resume_requested=False,
                        reset_requested=False,
                    )

                reopened = connect_database(database_path)
                try:
                    final_store = SessionStore(reopened)
                    old_session = final_store.get_session(session.id)
                    new_session = final_store.get_session(updated["session_id"])
                finally:
                    reopened.close()

            self.assertEqual(updated["agent_id"], "planner-safe")
            self.assertNotEqual(updated["session_id"], session.id)
            self.assertEqual(old_session.status, "closed")
            self.assertIsNotNone(new_session)

    def test_local_observer_human_cannot_post(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            database_path = root / "state" / "openminion.db"
            migrate_database(database_path)
            connection = connect_database(database_path)
            store = SessionStore(connection)
            try:
                session = store.create_room(
                    channel="cli",
                    target="room",
                    session_id="observer-room",
                    metadata={"local_human_id": "alice"},
                )
                store.add_participant(
                    session_id=session.id,
                    participant_type="human",
                    participant_id="alice",
                    channel="cli",
                    role="observer",
                    display_name="alice",
                )
            finally:
                connection.close()

            with mock.patch(
                "openminion.cli.commands.chat.resolve_cli_roots",
                return_value=SimpleNamespace(data_root=root, env={}),
            ):
                message = chat_command._local_human_post_block_reason(
                    session_id=session.id,
                    config_path="ignored.json",
                )

            self.assertIn("observer", message)
            self.assertIn("alice", message)
