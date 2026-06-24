import io
import json
import tempfile
import unittest
from argparse import Namespace
from contextlib import redirect_stdout
import os
from pathlib import Path
from tests._csc_fixtures import _csc_install_default_agent


from openminion.api.server import dispatch_request
from openminion.cli.commands.status import run_status
from openminion.cli.commands.status import _print_run_events
from openminion.cli.config import load_cli_manager
from openminion.base.config import OpenMinionConfig, save_config
from openminion.modules.session.storage.sqlite_store import SQLiteSessionStore
from openminion.modules.storage.runtime.sqlite import resolve_database_path
from openminion.services.lifecycle.self_improvement import SelfImprovementEngine
from openminion.modules.brain.paths import resolve_brain_sessions_db_path
from openminion.modules.tool.base import ToolExecutionResult
from openminion.modules.identity.models import (
    AgentProfile,
    PersonalitySpec,
    RiskSpec,
    RoleSpec,
    ToolPostureSpec,
)
from openminion.modules.identity.runtime.service import IdentityCtl
from openminion.modules.identity.storage.store import SQLiteIdentityStore

# Set soft enforcement mode for tests


class StatusCommandTests(unittest.TestCase):
    def test_status_runs_json(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            os.environ["OPENMINION_DATA_ROOT"] = str(Path(tmp) / ".openminion")
            config_path = _write_echo_config(Path(tmp))
            session_id = "status-session-1"
            with redirect_stdout(io.StringIO()):
                dispatch_request(
                    "POST",
                    "/turns",
                    str(config_path),
                    body={"message": "status runs", "session_id": session_id},
                )

            args = Namespace(
                config=str(config_path),
                status_command="runs",
                session_id=session_id,
                limit=10,
                json=True,
            )
            buf = io.StringIO()
            with redirect_stdout(buf):
                code = run_status(args)
            self.assertEqual(code, 0)

            payload = json.loads(buf.getvalue())
            self.assertTrue(payload["ok"])
            self.assertEqual(payload["session"]["id"], session_id)
            self.assertEqual(len(payload["runs"]), 1)
            self.assertEqual(payload["runs"][0]["state"], "completed")

    def test_status_run_events_json(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            os.environ["OPENMINION_DATA_ROOT"] = str(Path(tmp) / ".openminion")
            config_path = _write_echo_config(Path(tmp))
            session_id = "status-session-2"
            with redirect_stdout(io.StringIO()):
                status, payload = dispatch_request(
                    "POST",
                    "/turns",
                    str(config_path),
                    body={"message": "status events", "session_id": session_id},
                )
            self.assertEqual(int(status), 200)
            run_id = payload["turn"]["run_id"]

            args = Namespace(
                config=str(config_path),
                status_command="run-events",
                session_id=session_id,
                run_id=run_id,
                limit=20,
                json=True,
            )
            buf = io.StringIO()
            with redirect_stdout(buf):
                code = run_status(args)
            self.assertEqual(code, 0)

            result = json.loads(buf.getvalue())
            self.assertTrue(result["ok"])
            self.assertEqual(result["run_id"], run_id)
            self.assertGreaterEqual(len(result["events"]), 4)
            self.assertEqual(result["events"][0]["state"], "queued")
            self.assertEqual(result["events"][-1]["state"], "completed")

    def test_status_missing_session_raises_runtime_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            os.environ["OPENMINION_DATA_ROOT"] = str(Path(tmp) / ".openminion")
            config_path = _write_echo_config(Path(tmp))
            args = Namespace(
                config=str(config_path),
                status_command="runs",
                session_id="missing",
                limit=10,
                json=True,
            )
            with self.assertRaisesRegex(
                RuntimeError, "Session 'missing' was not found"
            ):
                run_status(args)

    def test_print_run_events_includes_thread_decision_details(self) -> None:
        payload = {
            "session": {"id": "s1"},
            "run_id": "r1",
            "events": [
                {
                    "id": 1,
                    "created_at": "2026-03-13T01:00:00Z",
                    "state": "queued",
                    "current_step": "turn.accepted",
                    "event_type": "run.queued",
                    "payload": {
                        "thread_decision_action": "fork_thread",
                        "thread_decision_reason": "settled_without_resume",
                        "thread_state_before": "settled",
                        "thread_state_qualifier": "completed_with_outbound",
                    },
                }
            ],
        }
        buf = io.StringIO()
        with redirect_stdout(buf):
            _print_run_events(payload=payload, as_json=False)
        output = buf.getvalue()
        self.assertIn("thread_decision=fork_thread", output)
        self.assertIn("reason=settled_without_resume", output)
        self.assertIn("thread_state_before=settled", output)
        self.assertIn("thread_state_qualifier=completed_with_outbound", output)

    def test_status_notes_and_note_activate_json(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            os.environ["OPENMINION_DATA_ROOT"] = str(Path(tmp) / ".openminion")
            config_path = _write_echo_config(Path(tmp))
            config = OpenMinionConfig.from_dict(
                json.loads(Path(config_path).read_text(encoding="utf-8"))
            )
            config.self_improvement.notes_path = str(Path(tmp) / "notes")
            config.self_improvement.application_mode = "review_first"
            save_config(config, str(config_path))

            engine = SelfImprovementEngine.from_config(config)
            captured = engine.capture_tool_failures(
                agent_id=config.agents[next(iter(config.agents.keys()))].name,
                user_message="weather in san francisco",
                tool_results=[
                    ToolExecutionResult(
                        tool_name="weather.openmeteo.current",
                        ok=False,
                        verified=False,
                        content="",
                        error="missing city argument",
                    )
                ],
            )
            self.assertEqual(len(captured), 1)

            notes_args = Namespace(
                config=str(config_path),
                status_command="notes",
                agent_id=config.agents[next(iter(config.agents.keys()))].name,
                json=True,
            )
            notes_buf = io.StringIO()
            with redirect_stdout(notes_buf):
                notes_code = run_status(notes_args)
            self.assertEqual(notes_code, 0)
            notes_payload = json.loads(notes_buf.getvalue())
            self.assertEqual(notes_payload["application_mode"], "review_first")
            self.assertEqual(notes_payload["count"], 1)
            self.assertEqual(notes_payload["notes"][0]["status"], "candidate")

            activate_args = Namespace(
                config=str(config_path),
                status_command="note-activate",
                agent_id=config.agents[next(iter(config.agents.keys()))].name,
                signature=captured[0],
                json=True,
            )
            activate_buf = io.StringIO()
            with redirect_stdout(activate_buf):
                activate_code = run_status(activate_args)
            self.assertEqual(activate_code, 0)
            activate_payload = json.loads(activate_buf.getvalue())
            self.assertTrue(activate_payload["ok"])
            self.assertEqual(activate_payload["status"], "active")

    def test_status_action_policy_json_default_source(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            os.environ["OPENMINION_DATA_ROOT"] = str(Path(tmp) / ".openminion")
            config_path = _write_echo_config(Path(tmp))
            raw = json.loads(Path(config_path).read_text(encoding="utf-8"))
            raw.pop("action_policy", None)
            Path(config_path).write_text(
                json.dumps(raw, indent=2, sort_keys=True),
                encoding="utf-8",
            )

            args = Namespace(
                config=str(config_path),
                status_command="action-policy",
                session_id="",
                json=True,
            )
            buf = io.StringIO()
            with redirect_stdout(buf):
                code = run_status(args)
            self.assertEqual(code, 0)

            payload = json.loads(buf.getvalue())
            self.assertTrue(payload["ok"])
            self.assertEqual(payload["permission_mode"], "auto")
            self.assertEqual(payload["source_level"], "global")
            self.assertEqual(payload["source_attribution"]["permission_mode"], "global")
            self.assertEqual(payload["source_attribution"]["default_action"], "global")
            self.assertIsInstance(payload["effective_rules"], list)
            self.assertIsInstance(payload["active_grants"], list)

    def test_status_action_policy_json_session_override_source(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            os.environ["OPENMINION_DATA_ROOT"] = str(Path(tmp) / ".openminion")
            config_path = _write_echo_config(Path(tmp))
            session_id = "policy-session-1"
            config = OpenMinionConfig.from_dict(
                json.loads(Path(config_path).read_text(encoding="utf-8"))
            )
            _csc_install_default_agent(config, action_policy=config.action_policy)
            config.agents[
                next(iter(config.agents.keys()))
            ].action_policy.default_action = "allow"
            config.agents[
                next(iter(config.agents.keys()))
            ].action_policy.allow_read_only_without_prompt = False
            save_config(config, str(config_path))
            manager = load_cli_manager(str(config_path))
            storage_env = manager.env.snapshot()
            storage_env.setdefault("OPENMINION_HOME", str(manager.home_root))
            storage_env.setdefault("OPENMINION_DATA_ROOT", str(manager.data_root))
            resolved_storage_path = resolve_database_path(
                config.storage.path,
                env=storage_env,
            )
            self.assertEqual(
                resolved_storage_path,
                (Path(config.storage.path).expanduser().resolve()),
            )
            brain_store = SQLiteSessionStore(
                resolve_brain_sessions_db_path(storage_path=resolved_storage_path)
            )
            try:
                brain_store.create_session(
                    session_id=session_id,
                    initial_agent_id=config.agents[
                        next(iter(config.agents.keys()))
                    ].name,
                    meta={"session_action_policy_mode_override": "bypass"},
                )
            finally:
                brain_store.close()

            args = Namespace(
                config=str(config_path),
                status_command="action-policy",
                session_id=session_id,
                json=True,
            )
            buf = io.StringIO()
            with redirect_stdout(buf):
                code = run_status(args)
            self.assertEqual(code, 0)

            payload = json.loads(buf.getvalue())
            self.assertTrue(payload["ok"])
            self.assertEqual(payload["session_id"], session_id)
            self.assertEqual(payload["permission_mode"], "bypass")
            self.assertEqual(payload["source_level"], "session-override")
            self.assertEqual(
                payload["source_attribution"]["permission_mode"], "session-override"
            )
            self.assertEqual(
                payload["source_attribution"]["default_action"], "agent-config"
            )
            self.assertEqual(
                payload["resolved_action_policy"]["default_action"],
                "allow",
            )
            self.assertFalse(
                payload["resolved_action_policy"]["allow_read_only_without_prompt"]
            )

    def test_status_action_policy_json_uses_per_agent_override(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            os.environ["OPENMINION_DATA_ROOT"] = str(Path(tmp) / ".openminion")
            config_path = Path(tmp) / "config.json"
            config = OpenMinionConfig.from_dict(
                {
                    "agents": {
                        "researcher": {
                            "name": "researcher",
                            "provider": "echo",
                            "action_policy": {"mode": "bypass"},
                        }
                    },
                    "default_agent": "researcher",
                    "storage": {
                        "path": str(
                            Path(tmp) / ".openminion" / "state" / "openminion.db"
                        )
                    },
                }
            )
            save_config(config, str(config_path))

            args = Namespace(
                config=str(config_path),
                status_command="action-policy",
                session_id="",
                agent_id="researcher",
                json=True,
            )
            buf = io.StringIO()
            with redirect_stdout(buf):
                code = run_status(args)
            self.assertEqual(code, 0)

            payload = json.loads(buf.getvalue())
            self.assertTrue(payload["ok"])
            self.assertEqual(payload["agent_id"], "researcher")
            self.assertEqual(payload["permission_mode"], "bypass")
            self.assertEqual(payload["source_level"], "agent-config")

    def test_status_owner_json(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            os.environ["OPENMINION_DATA_ROOT"] = str(Path(tmp) / ".openminion")
            config_path = _write_echo_config(Path(tmp))
            with redirect_stdout(io.StringIO()):
                dispatch_request(
                    "POST",
                    "/turns",
                    str(config_path),
                    body={
                        "message": "owner status run",
                        "session_id": "owner-status-session",
                    },
                    request_id="owner-status-turn-1",
                )

            args = Namespace(
                config=str(config_path),
                status_command="owner",
                session_limit=10,
                run_limit=10,
                hours=24,
                json=True,
            )
            buf = io.StringIO()
            with redirect_stdout(buf):
                code = run_status(args)
            self.assertEqual(code, 0)

            payload = json.loads(buf.getvalue())
            self.assertTrue(payload["ok"])
            self.assertEqual(payload["window_hours"], 24)
            self.assertGreaterEqual(payload["summary"]["runs_total"], 1)
            self.assertGreaterEqual(payload["sessions_total"], 1)

    def test_status_onboarding_json_distinguishes_configured_and_available(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            config_path = tmp_path / "config.json"
            config = OpenMinionConfig()
            _csc_install_default_agent(config, provider="openai")
            config.providers.openai.api_key_env = "OPENAI_API_KEY"
            config.runtime.env = {"OPENAI_API_KEY": "sk-test"}
            config.storage.path = str(
                (tmp_path / ".openminion" / "state" / "openminion.db").resolve()
            )
            save_config(config, str(config_path))

            args = Namespace(
                config=str(config_path),
                status_command="onboarding",
                agent_id=None,
                json=True,
                home_root=str(tmp_path),
                data_root=str(tmp_path / ".openminion"),
            )
            buf = io.StringIO()
            with redirect_stdout(buf):
                code = run_status(args)

            self.assertEqual(code, 0)
            payload = json.loads(buf.getvalue())
            self.assertEqual(payload["state"], "ready")
            self.assertTrue(payload["configured_now"])
            self.assertTrue(payload["available_later"])
            self.assertTrue(
                any(item["id"] == "provider_chat" for item in payload["configured_now"])
            )
            self.assertTrue(
                any(item["id"] == "demo_mode" for item in payload["available_later"])
            )

    def test_status_tools_json(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            os.environ["OPENMINION_DATA_ROOT"] = str(Path(tmp) / ".openminion")
            config_path = _write_echo_config(Path(tmp))

            args = Namespace(
                config=str(config_path),
                status_command="tools",
                json=True,
            )
            buf = io.StringIO()
            with redirect_stdout(buf):
                code = run_status(args)
            self.assertEqual(code, 0)

            payload = json.loads(buf.getvalue())
            self.assertTrue(payload["ok"])
            self.assertIn(payload["source"], {"daemon", "inproc"})
            self.assertGreater(payload["tool_count"], 0)
            self.assertIsInstance(payload["tools"], list)

    def test_status_capabilities_json(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            os.environ["OPENMINION_DATA_ROOT"] = str(Path(tmp) / ".openminion")
            root = Path(tmp)
            config_path = _write_echo_config(root)
            raw = json.loads(Path(config_path).read_text(encoding="utf-8"))
            raw["system"] = {
                "runtime": {
                    "provider_policy": {
                        "enabled": ["echo"],
                        "default_provider": "echo",
                    },
                    "modes": {
                        "delegate": {"enabled": False},
                    },
                }
            }
            # After CSC, per-agent modes live directly on agents.<id>.modes
            from openminion.base.config.core import resolve_default_agent_id as _rda

            _default_agent_id = _rda(OpenMinionConfig.from_dict(raw))
            raw["agents"][_default_agent_id]["modes"] = {"delegate": {"enabled": True}}
            save_config(OpenMinionConfig.from_dict(raw), str(config_path))

            args = Namespace(
                config=str(config_path),
                status_command="capabilities",
                json=True,
            )
            buf = io.StringIO()
            with redirect_stdout(buf):
                code = run_status(args)
            self.assertEqual(code, 0)

            payload = json.loads(buf.getvalue())
            self.assertTrue(payload["ok"])
            self.assertIn(payload["source"], {"daemon", "inproc"})
            capabilities = payload["capabilities"]
            self.assertEqual(capabilities["providers"]["selected"], "echo")
            self.assertIn("delegate", capabilities["modes"]["blocked_reasons"])
            mode_items = {item["name"]: item for item in capabilities["modes"]["items"]}
            self.assertEqual(
                mode_items["respond"]["registration_source"]["category"],
                "essential_builtin",
            )
            self.assertEqual(mode_items["delegate"]["registration_source"], {})
            self.assertEqual(
                mode_items["respond"]["thinking_policy"]["default_reasoning_profile"],
                "off",
            )
            self.assertEqual(
                capabilities["thinking"]["effective"]["reasoning_profile"],
                "minimal",
            )
            self.assertGreater(payload["summary"]["visible_tools"], 0)

    def test_status_runtime_json(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            os.environ["OPENMINION_DATA_ROOT"] = str(Path(tmp) / ".openminion")
            config_path = _write_echo_config(Path(tmp))

            args = Namespace(
                config=str(config_path),
                status_command="runtime",
                json=True,
            )
            buf = io.StringIO()
            with redirect_stdout(buf):
                code = run_status(args)
            self.assertEqual(code, 0)

            payload = json.loads(buf.getvalue())
            self.assertTrue(payload["ok"])
            self.assertIn(payload["source"], {"daemon", "inproc"})
            runtime = payload["runtime"]
            self.assertEqual(runtime["runtime_mode"], "brain")
            self.assertTrue(runtime["brain_bridge_active"])
            self.assertTrue(runtime["canonical_turn_path"])

    def test_status_extensions_json_includes_capability_layering(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            os.environ["OPENMINION_DATA_ROOT"] = str(Path(tmp) / ".openminion")
            root = Path(tmp)
            config_path = _write_echo_config(root)
            raw = json.loads(Path(config_path).read_text(encoding="utf-8"))
            raw["enabled_plugins"] = ["validate"]
            from openminion.base.config.core import resolve_default_agent_id as _rda

            _default_agent_id = _rda(OpenMinionConfig.from_dict(raw))
            raw["agents"][_default_agent_id]["provider"] = ""
            raw["system"] = {
                "runtime": {
                    "provider_policy": {
                        "enabled": ["echo"],
                        "default_provider": "echo",
                    },
                    "modes": {
                        "delegate": {"enabled": False},
                    },
                }
            }
            raw["agents"][_default_agent_id]["modes"] = {"delegate": {"enabled": True}}
            save_config(OpenMinionConfig.from_dict(raw), str(config_path))

            args = Namespace(
                config=str(config_path),
                status_command="extensions",
                json=True,
            )
            buf = io.StringIO()
            with redirect_stdout(buf):
                code = run_status(args)
            self.assertEqual(code, 0)

            payload = json.loads(buf.getvalue())
            self.assertTrue(payload["ok"])
            layering = payload["capability_layering"]
            self.assertEqual(layering["provider"]["selected"], "echo")
            self.assertEqual(layering["provider"]["source"], "system_runtime")
            self.assertIn(
                "system.runtime.modes.delegate.enabled=false",
                layering["modes"]["blocked_reasons"]["delegate"],
            )

    def test_status_identity_json(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            os.environ["OPENMINION_DATA_ROOT"] = str(Path(tmp) / ".openminion")
            root = Path(tmp)
            config_path = _write_echo_config(root)
            config = OpenMinionConfig.from_dict(
                json.loads(Path(config_path).read_text(encoding="utf-8"))
            )
            db_path = root / "identity" / "identity.db"
            config.identity.db_path = str(db_path)
            save_config(config, str(config_path))
            _write_identity_profile(
                db_path,
                agent_id=config.agents[next(iter(config.agents.keys()))].name,
                mission="Provide status identity output.",
                tone="precise",
                meta={
                    "bundle_imported": True,
                    "bundle_fingerprint": "fp-status-001",
                },
            )

            args = Namespace(
                config=str(config_path),
                status_command="identity",
                agent_id=None,
                root=None,
                json=True,
            )
            buf = io.StringIO()
            with redirect_stdout(buf):
                code = run_status(args)
            self.assertEqual(code, 0)
            payload = json.loads(buf.getvalue())
            self.assertTrue(payload["ok"])
            self.assertEqual(
                payload["agent_id"],
                config.agents[next(iter(config.agents.keys()))].name,
            )
            self.assertEqual(payload["identity_db_path"], str(db_path.resolve()))
            self.assertEqual(payload["profile_revision"], 1)
            self.assertTrue(bool(payload["profile_version"]))
            self.assertTrue(bool(payload["render_version"]))
            self.assertTrue(payload["bundle_imported"])
            self.assertEqual(payload["bundle_fingerprint"], "fp-status-001")
            self.assertEqual(payload["meta_source"], "")
            self.assertEqual(payload["source_classification"], "legacy-bundle")
            self.assertTrue(payload["source_refreshable_by_bundle"])

    def test_status_identity_json_surfaces_source_provenance(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            os.environ["OPENMINION_DATA_ROOT"] = str(Path(tmp) / ".openminion")
            root = Path(tmp)
            config_path = _write_echo_config(root)
            config = OpenMinionConfig.from_dict(
                json.loads(Path(config_path).read_text(encoding="utf-8"))
            )
            db_path = root / "identity" / "identity.db"
            config.identity.db_path = str(db_path)
            save_config(config, str(config_path))
            _write_identity_profile(
                db_path,
                agent_id=config.agents[next(iter(config.agents.keys()))].name,
                mission="Status diagnostics baseline mission.",
                tone="direct",
                meta={
                    "bundle_imported": True,
                    "bundle_fingerprint": "fp-status-baseline-001",
                    "source": "yaml",
                },
            )

            args = Namespace(
                config=str(config_path),
                status_command="identity",
                agent_id=None,
                root=None,
                json=True,
            )
            buf = io.StringIO()
            with redirect_stdout(buf):
                code = run_status(args)
            self.assertEqual(code, 0)
            payload = json.loads(buf.getvalue())
            self.assertTrue(payload["ok"])
            self.assertTrue(payload["bundle_imported"])
            self.assertEqual(payload["bundle_fingerprint"], "fp-status-baseline-001")
            self.assertEqual(payload["meta_source"], "yaml")
            self.assertEqual(payload["source_classification"], "yaml")
            self.assertFalse(payload["source_refreshable_by_bundle"])

    def test_status_identity_json_snapshot_for_identityctl_profile(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            os.environ["OPENMINION_DATA_ROOT"] = str(Path(tmp) / ".openminion")
            root = Path(tmp)
            config_path = _write_echo_config(root)
            config = OpenMinionConfig.from_dict(
                json.loads(Path(config_path).read_text(encoding="utf-8"))
            )
            db_path = root / "identity" / "identity.db"
            config.identity.db_path = str(db_path)
            save_config(config, str(config_path))
            ctl = _write_identity_profile(
                db_path,
                agent_id=config.agents[next(iter(config.agents.keys()))].name,
                mission="Snapshot mission.",
                tone="calm",
                meta={
                    "bundle_imported": True,
                    "bundle_fingerprint": "fp-snapshot-001",
                },
            )
            snippet = ctl.render(
                agent_id=config.agents[next(iter(config.agents.keys()))].name,
                purpose="act",
                max_tokens=200,
            )
            ctl.close()

            args = Namespace(
                config=str(config_path),
                status_command="identity",
                agent_id=None,
                root=None,
                json=True,
            )
            buf = io.StringIO()
            with redirect_stdout(buf):
                code = run_status(args)
            self.assertEqual(code, 0)
            payload = json.loads(buf.getvalue())

            expected = {
                "ok": True,
                "agent_id": config.agents[next(iter(config.agents.keys()))].name,
                "identity_db_path": str(db_path.resolve()),
                "profile_revision": 1,
                "profile_version": snippet.profile_version,
                "render_version": snippet.render_version,
                "bundle_imported": True,
                "bundle_fingerprint": "fp-snapshot-001",
                "meta_source": "",
                "source_classification": "legacy-bundle",
                "source_refreshable_by_bundle": True,
            }
            self.assertEqual(payload, expected)

    def test_status_identity_prefers_db_path_over_legacy_root_alias(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            os.environ["OPENMINION_DATA_ROOT"] = str(Path(tmp) / ".openminion")
            root = Path(tmp)
            config_path = _write_echo_config(root)
            config = OpenMinionConfig.from_dict(
                json.loads(Path(config_path).read_text(encoding="utf-8"))
            )
            legacy_db = root / "legacy" / "identity.db"
            primary_db = root / "primary" / "identity.db"
            config.identity.root = str(legacy_db)
            config.identity.db_path = str(primary_db)
            save_config(config, str(config_path))

            _write_identity_profile(
                primary_db,
                agent_id=config.agents[next(iter(config.agents.keys()))].name,
                mission="Primary db mission.",
                tone="direct",
            )

            args = Namespace(
                config=str(config_path),
                status_command="identity",
                agent_id=None,
                root=None,
                json=True,
            )
            buf = io.StringIO()
            with redirect_stdout(buf):
                code = run_status(args)
            self.assertEqual(code, 0)
            payload = json.loads(buf.getvalue())
            self.assertTrue(payload["ok"])
            self.assertEqual(payload["identity_db_path"], str(primary_db.resolve()))

    def test_status_identity_returns_failure_when_profile_missing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            os.environ["OPENMINION_DATA_ROOT"] = str(Path(tmp) / ".openminion")
            root = Path(tmp)
            config_path = _write_echo_config(root)
            config = OpenMinionConfig.from_dict(
                json.loads(Path(config_path).read_text(encoding="utf-8"))
            )
            db_path = root / "identity" / "identity.db"
            config.identity.db_path = str(db_path)
            save_config(config, str(config_path))
            args = Namespace(
                config=str(config_path),
                status_command="identity",
                agent_id="missing-agent",
                root=None,
                json=True,
            )
            buf = io.StringIO()
            with redirect_stdout(buf):
                code = run_status(args)
            self.assertEqual(code, 1)
            payload = json.loads(buf.getvalue())
            self.assertFalse(payload["ok"])
            self.assertEqual(
                payload["error"],
                "identity profile not found: missing-agent",
            )
            self.assertEqual(payload["identity_db_path"], str(db_path.resolve()))
            self.assertEqual(payload["meta_source"], "")
            self.assertEqual(payload["source_classification"], "missing")
            self.assertFalse(payload["source_refreshable_by_bundle"])

    def test_status_identity_render_shows_provenance(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config_path = _write_echo_config(root)
            config = OpenMinionConfig.from_dict(
                json.loads(Path(config_path).read_text(encoding="utf-8"))
            )
            db_path = root / "identity" / "identity.db"
            config.identity.db_path = str(db_path)
            save_config(config, str(config_path))
            _write_identity_profile(
                db_path,
                agent_id="test-agent",
                mission="Render mission from identity store.",
                tone="focused",
                meta={
                    "bundle_imported": True,
                    "bundle_fingerprint": "fp-render-001",
                },
            )
            args = Namespace(
                config=str(config_path),
                status_command="identity",
                agent_id="test-agent",
                root=None,
                render=True,
                purpose="act",
                json=True,
            )
            buf = io.StringIO()
            with redirect_stdout(buf):
                code = run_status(args)
            self.assertEqual(code, 0)
            payload = json.loads(buf.getvalue())
            self.assertEqual(payload["agent_id"], "test-agent")
            self.assertEqual(payload["purpose"], "act")
            self.assertTrue(bool(payload["profile_version"]))
            self.assertTrue(payload["render_version"].startswith("v1:"))
            self.assertTrue(payload["bundle_imported"])
            self.assertEqual(payload["bundle_fingerprint"], "fp-render-001")
            self.assertEqual(payload["meta_source"], "")
            self.assertEqual(payload["source_classification"], "legacy-bundle")
            self.assertTrue(payload["source_refreshable_by_bundle"])
            self.assertIn("Render mission from identity store.", payload["text"])

    def test_status_identity_render_fallback_when_missing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config_path = _write_echo_config(root)
            config = OpenMinionConfig.from_dict(
                json.loads(Path(config_path).read_text(encoding="utf-8"))
            )
            db_path = root / "identity" / "identity.db"
            config.identity.db_path = str(db_path)
            save_config(config, str(config_path))
            args = Namespace(
                config=str(config_path),
                status_command="identity",
                agent_id="missing-agent",
                root=None,
                render=True,
                purpose="act",
                json=True,
            )
            buf = io.StringIO()
            with redirect_stdout(buf):
                code = run_status(args)
            self.assertEqual(code, 1)
            payload = json.loads(buf.getvalue())
            self.assertFalse(payload["ok"])
            self.assertEqual(payload["profile_version"], None)
            self.assertEqual(payload["render_version"], None)
            self.assertEqual(
                payload["error"],
                "identity profile not found: missing-agent",
            )


def _write_echo_config(tmp_path: Path) -> Path:
    config_path = tmp_path / "config.json"
    config = OpenMinionConfig()
    _csc_install_default_agent(config)  # type: ignore[attr-defined]
    config.runtime.log_level = "ERROR"
    _csc_install_default_agent(config, provider="echo")
    config.storage.path = str(tmp_path / "state" / "api.db")
    # Set OPENMINION_DATA_ROOT for path validation
    old_data_root = os.environ.get("OPENMINION_DATA_ROOT")
    try:
        os.environ["OPENMINION_DATA_ROOT"] = str(tmp_path / ".openminion")
        save_config(config, str(config_path))
    finally:
        if old_data_root is None:
            os.environ.pop("OPENMINION_DATA_ROOT", None)
        else:
            os.environ["OPENMINION_DATA_ROOT"] = old_data_root
    return config_path


def _write_identity_profile(
    db_path: Path,
    *,
    agent_id: str,
    mission: str,
    tone: str,
    meta: dict[str, object] | None = None,
) -> IdentityCtl:
    db_path = db_path.expanduser().resolve()
    db_path.parent.mkdir(parents=True, exist_ok=True)
    ctl = IdentityCtl(store=SQLiteIdentityStore(sqlite_path=str(db_path)))
    profile = AgentProfile(
        agent_id=agent_id,
        display_name=agent_id,
        profile_revision=1,
        role=RoleSpec(
            mission=mission,
            responsibilities=["Provide accurate status identity output."],
            hard_constraints=["Do not fabricate identity details."],
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
    return ctl
