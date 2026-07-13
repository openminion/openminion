import io
import json
import os
import sqlite3
import tempfile
import unittest
from argparse import Namespace
from contextlib import contextmanager, redirect_stdout
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock
from tests._csc_fixtures import _csc_install_default_agent


from openminion.cli.commands.doctor import run_doctor
from openminion.cli.commands.setup import run_setup
from openminion.base.config import OpenMinionConfig, save_config
from openminion.modules.telemetry.lifecycle import (
    build_component_identity,
    build_lifecycle_telemetry_event,
)
from openminion.modules.telemetry.service import (
    TelemetryService,
    resolve_telemetry_db_path,
)
from openminion.modules.identity.models import (
    AgentProfile,
    PersonalitySpec,
    RiskSpec,
    RoleSpec,
    ToolPostureSpec,
)
from openminion.modules.identity.runtime.service import IdentityCtl
from openminion.modules.identity.storage.store import SQLiteIdentityStore


@contextmanager
def _isolated_openminion_home(home_root: Path):
    previous_home = os.environ.get("OPENMINION_HOME")
    previous_data_root = os.environ.get("OPENMINION_DATA_ROOT")
    os.environ["OPENMINION_HOME"] = str(home_root)
    os.environ.pop("OPENMINION_DATA_ROOT", None)
    try:
        yield
    finally:
        if previous_home is None:
            os.environ.pop("OPENMINION_HOME", None)
        else:
            os.environ["OPENMINION_HOME"] = previous_home
        if previous_data_root is None:
            os.environ.pop("OPENMINION_DATA_ROOT", None)
        else:
            os.environ["OPENMINION_DATA_ROOT"] = previous_data_root


def _record_lifecycle_event(
    *,
    home_root: Path,
    component: dict[str, object],
    event_type: str,
    reason: str | None = None,
    observed_at: datetime | None = None,
) -> None:
    service = TelemetryService(home_root=home_root)
    component_kind = str(component.get("component_kind") or "").strip()
    component_id = str(component.get("component_id") or "").strip()
    event = build_lifecycle_telemetry_event(
        event_type=event_type,
        component=component,
        module_id="openminion-runtime",
        session_id=f"lifecycle:{component_kind}:{component_id}",
        turn_id=f"{component_kind}:{event_type.rsplit('.', 1)[-1]}",
        status="error" if event_type == "component.crashed" else "ok",
        reason=reason,
        source_classification="native_canonical",
    )
    service.record_event_sync(event)
    service.close_sync()
    if observed_at is None:
        return
    telemetry_db = Path(resolve_telemetry_db_path(home_root=home_root).db_path)
    conn = sqlite3.connect(str(telemetry_db))
    try:
        conn.execute(
            "UPDATE events SET timestamp = ? WHERE rowid = (SELECT MAX(rowid) FROM events)",
            (float(observed_at.timestamp()),),
        )
        conn.commit()
    finally:
        conn.close()


class DoctorCommandTests(unittest.TestCase):
    def setUp(self) -> None:
        super().setUp()
        self._previous_openminion_home = os.environ.pop("OPENMINION_HOME", None)
        self._previous_openminion_data_root = os.environ.pop(
            "OPENMINION_DATA_ROOT",
            None,
        )

    def tearDown(self) -> None:
        if self._previous_openminion_home is not None:
            os.environ["OPENMINION_HOME"] = self._previous_openminion_home
        else:
            os.environ.pop("OPENMINION_HOME", None)
        if self._previous_openminion_data_root is not None:
            os.environ["OPENMINION_DATA_ROOT"] = self._previous_openminion_data_root
        else:
            os.environ.pop("OPENMINION_DATA_ROOT", None)
        super().tearDown()

    def test_setup_runs_doctor_and_continues_on_success(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config_path = Path(tmp) / "config.json"
            config = OpenMinionConfig()
            _csc_install_default_agent(config, provider="echo")
            config.runtime.demo_mode = True
            args = Namespace(
                config=str(config_path),
                home_root=str(Path(tmp)),
                data_root=str(Path(tmp) / ".openminion"),
                no_chat=False,
                agent="ops-agent",
            )
            with (
                mock.patch(
                    "openminion.cli.commands.setup._run_wizard",
                    return_value=(config, config_path),
                ),
                mock.patch(
                    "openminion.cli.commands.setup._run_setup_doctor",
                    return_value=0,
                ) as doctor_mock,
                mock.patch(
                    "openminion.cli.commands.setup._launch_post_setup_focus",
                    return_value=0,
                ) as chat_mock,
            ):
                buf = io.StringIO()
                with redirect_stdout(buf):
                    code = run_setup(args)

            self.assertEqual(code, 0)
            doctor_mock.assert_called_once_with(config_path=config_path)
            chat_mock.assert_called_once_with(args, config_path=config_path)
            self.assertIn("Setup validation passed. Entering Focus...", buf.getvalue())

    def test_setup_stops_when_doctor_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config_path = Path(tmp) / "config.json"
            config = OpenMinionConfig()
            _csc_install_default_agent(config, provider="openai")
            args = Namespace(
                config=str(config_path),
                home_root=str(Path(tmp)),
                data_root=str(Path(tmp) / ".openminion"),
                no_chat=False,
                agent="ops-agent",
            )
            with (
                mock.patch(
                    "openminion.cli.commands.setup._run_wizard",
                    return_value=(config, config_path),
                ),
                mock.patch(
                    "openminion.cli.commands.setup._run_setup_doctor",
                    return_value=1,
                ) as doctor_mock,
                mock.patch(
                    "openminion.cli.commands.setup._launch_post_setup_focus"
                ) as chat_mock,
            ):
                buf = io.StringIO()
                with redirect_stdout(buf):
                    code = run_setup(args)

            self.assertEqual(code, 1)
            doctor_mock.assert_called_once_with(config_path=config_path)
            chat_mock.assert_not_called()
            self.assertIn(
                "Setup validation failed. Fix the reported issues and rerun `openminion setup`.",
                buf.getvalue(),
            )

    def test_doctor_echo_config_ok(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config_path = Path(tmp) / "config.json"
            config = OpenMinionConfig()
            _csc_install_default_agent(config, provider="echo")
            config.runtime.log_level = "ERROR"
            config.storage.path = str(Path(tmp) / "state" / "doctor.db")
            save_config(config, str(config_path))

            args = Namespace(
                config=str(config_path),
                check_turn=False,
                message="doctor ping",
                target="doctor",
                channel=None,
                json=True,
            )
            buf = io.StringIO()
            with redirect_stdout(buf):
                code = run_doctor(args)
            self.assertEqual(code, 0)

            payload = json.loads(buf.getvalue())
            self.assertTrue(payload["summary"]["ok"])
            self.assertEqual(payload["summary"]["status"], "ok")
            check_by_id = {check["id"]: check for check in payload["checks"]}
            check_ids = set(check_by_id.keys())
            self.assertIn("storage.ready", check_ids)
            self.assertIn("security.validate.summary", check_ids)
            self.assertIn("security.execution.boundary.policy", check_ids)
            self.assertIn("identity.bundle", check_ids)
            self.assertEqual(check_by_id["identity.bundle"]["status"], "warn")
            self.assertEqual(check_by_id["security.validate.summary"]["status"], "warn")
            storage_check = check_by_id["storage.ready"]
            self.assertEqual(storage_check["finding_id"], "storage.ready")
            self.assertEqual(storage_check["summary"], storage_check["message"])
            self.assertIn("target_component", storage_check)
            self.assertIn("component_kind", storage_check["target_component"])
            self.assertIn("component_id", storage_check["target_component"])
            self.assertIn("scope", storage_check["target_component"])
            self.assertEqual(storage_check["related_probe_ids"], ["storage.ready"])
            self.assertNotIn("related_probe_ids", check_by_id["identity.bundle"])
            self.assertNotIn(
                "related_probe_ids", check_by_id["security.validate.summary"]
            )

    def test_doctor_skip_supervision_omits_supervision_checks(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config_path = Path(tmp) / "config.json"
            config = OpenMinionConfig()
            _csc_install_default_agent(config, provider="echo")
            config.runtime.log_level = "ERROR"
            config.storage.path = str(Path(tmp) / "state" / "doctor.db")
            save_config(config, str(config_path))

            args = Namespace(
                config=str(config_path),
                check_turn=False,
                message="doctor ping",
                target="doctor",
                channel=None,
                json=True,
                skip_supervision=True,
            )
            buf = io.StringIO()
            with redirect_stdout(buf):
                code = run_doctor(args)
            self.assertEqual(code, 0)

            payload = json.loads(buf.getvalue())
            check_ids = {check["id"] for check in payload["checks"]}
            self.assertFalse(
                any(check_id.startswith("supervision.") for check_id in check_ids)
            )

    def test_doctor_runtime_bootstrap_includes_runtime_posture(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config_path = Path(tmp) / "config.json"
            config = OpenMinionConfig()
            _csc_install_default_agent(config, provider="echo")
            config.runtime.log_level = "ERROR"
            config.storage.path = str(Path(tmp) / "state" / "doctor.db")
            save_config(config, str(config_path))

            args = Namespace(
                config=str(config_path),
                check_turn=False,
                message="doctor ping",
                target="doctor",
                channel=None,
                json=True,
            )
            buf = io.StringIO()
            with redirect_stdout(buf):
                code = run_doctor(args)
            self.assertEqual(code, 0)

            payload = json.loads(buf.getvalue())
            runtime_check = {check["id"]: check for check in payload["checks"]}[
                "runtime.bootstrap"
            ]
            details = runtime_check["details"]
            self.assertEqual(details["agent_runtime_mode"], "brain")
            self.assertTrue(details["brain_bridge_active"])
            self.assertIn("runtime_posture", details)
            self.assertEqual(
                details["runtime_posture"]["runtime_mode"],
                details["agent_runtime_mode"],
            )
            self.assertIn("capabilities", details)
            self.assertEqual(details["capabilities"]["providers"]["selected"], "echo")

    def test_doctor_identity_bundle_ok_when_markdown_bundle_exists(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config_path = Path(tmp) / "config.json"
            config = OpenMinionConfig()
            _csc_install_default_agent(config, provider="echo")
            config.runtime.log_level = "ERROR"
            config.storage.path = str(Path(tmp) / "state" / "doctor.db")
            identity_db = Path(tmp) / "identity" / "identity.db"
            config.identity.db_path = str(identity_db)
            save_config(config, str(config_path))

            bundle_root = (
                Path(tmp)
                / "agents"
                / config.agents[next(iter(config.agents.keys()))].name
            )
            (bundle_root / "SKILLS" / "hello").mkdir(parents=True)
            (bundle_root / "AGENT.md").write_text("# Agent\n", encoding="utf-8")
            (bundle_root / "SOUL.md").write_text("# Soul\n", encoding="utf-8")
            (bundle_root / "SKILLS" / "hello" / "SKILL.md").write_text(
                "# Skill\n", encoding="utf-8"
            )
            _write_identity_profile(
                identity_db,
                agent_id=config.agents[next(iter(config.agents.keys()))].name,
                mission="Doctor identity check mission.",
                tone="clear",
                meta={
                    "bundle_imported": True,
                    "bundle_fingerprint": "doctor-fingerprint-001",
                },
            )

            args = Namespace(
                config=str(config_path),
                check_turn=False,
                message="doctor ping",
                target="doctor",
                channel=None,
                json=True,
            )
            buf = io.StringIO()
            with redirect_stdout(buf):
                code = run_doctor(args)
            self.assertEqual(code, 0)

            payload = json.loads(buf.getvalue())
            check_by_id = {check["id"]: check for check in payload["checks"]}
            self.assertIn("identity.bundle", check_by_id)
            self.assertEqual(check_by_id["identity.bundle"]["status"], "ok")
            self.assertTrue(
                check_by_id["identity.bundle"]["details"]["profile_present"]
            )
            self.assertIn(
                "bundle_diagnostics", check_by_id["identity.bundle"]["details"]
            )

    def test_doctor_identity_bundle_snapshot_for_identityctl_profile(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config_path = Path(tmp) / "config.json"
            config = OpenMinionConfig()
            _csc_install_default_agent(config, provider="echo")
            config.runtime.log_level = "ERROR"
            config.storage.path = str(Path(tmp) / "state" / "doctor.db")
            identity_db = Path(tmp) / "identity" / "identity.db"
            config.identity.db_path = str(identity_db)
            save_config(config, str(config_path))

            bundle_root = (
                Path(tmp)
                / "agents"
                / config.agents[next(iter(config.agents.keys()))].name
            )
            (bundle_root / "SKILLS" / "hello").mkdir(parents=True)
            (bundle_root / "AGENT.md").write_text("# Agent\n", encoding="utf-8")
            (bundle_root / "SOUL.md").write_text("# Soul\n", encoding="utf-8")
            (bundle_root / "SKILLS" / "hello" / "SKILL.md").write_text(
                "# Skill\n", encoding="utf-8"
            )
            _write_identity_profile(
                identity_db,
                agent_id=config.agents[next(iter(config.agents.keys()))].name,
                mission="Doctor snapshot mission.",
                tone="pragmatic",
                meta={
                    "bundle_imported": True,
                    "bundle_fingerprint": "doctor-fingerprint-002",
                },
            )

            args = Namespace(
                config=str(config_path),
                check_turn=False,
                message="doctor ping",
                target="doctor",
                channel=None,
                json=True,
            )
            buf = io.StringIO()
            with redirect_stdout(buf):
                code = run_doctor(args)
            self.assertEqual(code, 0)
            payload = json.loads(buf.getvalue())
            check_by_id = {check["id"]: check for check in payload["checks"]}
            identity_check = check_by_id["identity.bundle"]

            expected = {
                "id": "identity.bundle",
                "status": "ok",
                "message": (
                    f"Identity profile is present in IdentityCtl for agent '{config.agents[next(iter(config.agents.keys()))].name}' "
                    f"(revision=1)"
                ),
                "details": {
                    "identity_db_path": str(identity_db.resolve()),
                    "profile_present": True,
                    "profile_revision": 1,
                    "bundle_imported": True,
                    "bundle_fingerprint": "doctor-fingerprint-002",
                    "meta_source": "",
                    "source_classification": "legacy-bundle",
                    "source_refreshable_by_bundle": True,
                    "bundle_diagnostics": {
                        "root_path": str(bundle_root.resolve()),
                        "fingerprint": identity_check["details"]["bundle_diagnostics"][
                            "fingerprint"
                        ],
                        "skills_count": 1,
                        "notes_count": 0,
                        "warnings": [],
                        "errors": [],
                    },
                },
            }
            projection = {
                "id": identity_check["id"],
                "status": identity_check["status"],
                "message": identity_check["message"],
                "details": identity_check["details"],
            }
            self.assertEqual(projection, expected)

    def test_doctor_identity_bundle_surfaces_source_provenance(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config_path = Path(tmp) / "config.json"
            config = OpenMinionConfig()
            _csc_install_default_agent(config, provider="echo")
            config.runtime.log_level = "ERROR"
            config.storage.path = str(Path(tmp) / "state" / "doctor.db")
            identity_db = Path(tmp) / "identity" / "identity.db"
            config.identity.db_path = str(identity_db)
            save_config(config, str(config_path))

            bundle_root = (
                Path(tmp)
                / "agents"
                / config.agents[next(iter(config.agents.keys()))].name
            )
            (bundle_root / "SKILLS" / "hello").mkdir(parents=True)
            (bundle_root / "AGENT.md").write_text("# Agent\n", encoding="utf-8")
            (bundle_root / "SOUL.md").write_text("# Soul\n", encoding="utf-8")
            (bundle_root / "SKILLS" / "hello" / "SKILL.md").write_text(
                "# Skill\n", encoding="utf-8"
            )
            _write_identity_profile(
                identity_db,
                agent_id=config.agents[next(iter(config.agents.keys()))].name,
                mission="Doctor diagnostics baseline mission.",
                tone="focused",
                meta={
                    "bundle_imported": True,
                    "bundle_fingerprint": "doctor-fingerprint-baseline-001",
                    "source": "yaml",
                },
            )

            args = Namespace(
                config=str(config_path),
                check_turn=False,
                message="doctor ping",
                target="doctor",
                channel=None,
                json=True,
            )
            buf = io.StringIO()
            with redirect_stdout(buf):
                code = run_doctor(args)
            self.assertEqual(code, 0)
            payload = json.loads(buf.getvalue())
            check_by_id = {check["id"]: check for check in payload["checks"]}
            details = check_by_id["identity.bundle"]["details"]
            self.assertTrue(details["bundle_imported"])
            self.assertEqual(
                details["bundle_fingerprint"],
                "doctor-fingerprint-baseline-001",
            )
            self.assertEqual(details["meta_source"], "yaml")
            self.assertEqual(details["source_classification"], "yaml")
            self.assertFalse(details["source_refreshable_by_bundle"])

    def test_doctor_openai_without_key_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config_path = Path(tmp) / "config.json"
            config = OpenMinionConfig()
            _csc_install_default_agent(config, provider="openai")
            config.runtime.log_level = "ERROR"
            config.providers.openai.api_key = ""
            config.providers.openai.api_key_env = "OPENMINION_TEST_OPENAI_KEY"
            config.storage.path = str(Path(tmp) / "state" / "doctor.db")
            save_config(config, str(config_path))

            previous = os.environ.pop("OPENMINION_TEST_OPENAI_KEY", None)
            try:
                args = Namespace(
                    config=str(config_path),
                    check_turn=False,
                    message="doctor ping",
                    target="doctor",
                    channel=None,
                    json=True,
                )
                buf = io.StringIO()
                with redirect_stdout(buf):
                    code = run_doctor(args)
                self.assertEqual(code, 1)

                payload = json.loads(buf.getvalue())
                self.assertFalse(payload["summary"]["ok"])
                self.assertEqual(payload["summary"]["status"], "fail")
            finally:
                if previous is not None:
                    os.environ["OPENMINION_TEST_OPENAI_KEY"] = previous

    def test_doctor_uses_resolved_openminion_home_for_supervision_lookup(self) -> None:
        with (
            tempfile.TemporaryDirectory() as home_tmp,
            tempfile.TemporaryDirectory() as cfg_tmp,
        ):
            home_root = Path(home_tmp)
            cfg_root = Path(cfg_tmp)
            config_path = cfg_root / "config.json"
            config = OpenMinionConfig()
            _csc_install_default_agent(config, provider="echo")
            config.runtime.log_level = "ERROR"
            config.storage.path = str(home_root / "state" / "doctor.db")
            save_config(config, str(config_path))

            with _isolated_openminion_home(home_root):
                _record_lifecycle_event(
                    home_root=home_root,
                    component=build_component_identity(
                        component_kind="daemon",
                        component_id="primary",
                        scope="system",
                        owner_module="openminion-runtime",
                    ),
                    event_type="component.heartbeat",
                    reason="heartbeat",
                    observed_at=datetime.now(tz=timezone.utc) - timedelta(seconds=70),
                )
                args = Namespace(
                    config=str(config_path),
                    check_turn=False,
                    message="doctor ping",
                    target="doctor",
                    channel=None,
                    json=True,
                )
                buf = io.StringIO()
                with redirect_stdout(buf):
                    code = run_doctor(args)

            self.assertEqual(code, 1)
            payload = json.loads(buf.getvalue())
            check_by_id = {check["id"]: check for check in payload["checks"]}
            self.assertIn("supervision.daemon.primary", check_by_id)
            self.assertEqual(
                check_by_id["supervision.daemon.primary"]["status"], "fail"
            )

    def test_doctor_normalization_logs_positive_and_negative_summary(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            ok_config_path = Path(tmp) / "ok-config.json"
            ok_config = OpenMinionConfig()
            _csc_install_default_agent(ok_config)
            ok_config.agents[next(iter(ok_config.agents.keys()))].provider = "echo"
            ok_config.runtime.log_level = "ERROR"
            ok_config.storage.path = str(Path(tmp) / "state" / "ok-doctor.db")
            save_config(ok_config, str(ok_config_path))

            ok_args = Namespace(
                config=str(ok_config_path),
                check_turn=False,
                message="doctor ping",
                target="doctor",
                channel=None,
                json=True,
            )
            with self.assertLogs("openminion.doctor", level="INFO") as ok_logs:
                with redirect_stdout(io.StringIO()):
                    code = run_doctor(ok_args)
            self.assertEqual(code, 0)
            self.assertTrue(
                any("doctor.normalization.summary" in entry for entry in ok_logs.output)
            )

            fail_config_path = Path(tmp) / "fail-config.json"
            fail_config = OpenMinionConfig()
            _csc_install_default_agent(fail_config)
            fail_config.agents[
                next(iter(fail_config.agents.keys()))
            ].provider = "openai"
            fail_config.runtime.log_level = "ERROR"
            fail_config.providers.openai.api_key = ""
            fail_config.providers.openai.api_key_env = "OPENMINION_TEST_OPENAI_KEY_LOGS"
            fail_config.storage.path = str(Path(tmp) / "state" / "fail-doctor.db")
            save_config(fail_config, str(fail_config_path))

            previous = os.environ.pop("OPENMINION_TEST_OPENAI_KEY_LOGS", None)
            try:
                fail_args = Namespace(
                    config=str(fail_config_path),
                    check_turn=False,
                    message="doctor ping",
                    target="doctor",
                    channel=None,
                    json=True,
                )
                with self.assertLogs("openminion.doctor", level="WARNING") as fail_logs:
                    with redirect_stdout(io.StringIO()):
                        code = run_doctor(fail_args)
                self.assertEqual(code, 1)
                self.assertTrue(
                    any(
                        "doctor.normalization.negative" in entry
                        for entry in fail_logs.output
                    )
                )
            finally:
                if previous is not None:
                    os.environ["OPENMINION_TEST_OPENAI_KEY_LOGS"] = previous

    def test_doctor_check_turn_echo(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config_path = Path(tmp) / "config.json"
            config = OpenMinionConfig()
            _csc_install_default_agent(
                config, provider="echo", default_channel="console"
            )
            config.runtime.log_level = "ERROR"
            config.storage.path = str(Path(tmp) / "state" / "doctor.db")
            save_config(config, str(config_path))

            args = Namespace(
                config=str(config_path),
                check_turn=True,
                message="doctor ping",
                target="doctor",
                channel=None,
                json=True,
            )
            buf = io.StringIO()
            with redirect_stdout(buf):
                code = run_doctor(args)
            self.assertEqual(code, 0)

            payload = json.loads(buf.getvalue())
            check_ids = {check["id"] for check in payload["checks"]}
            self.assertIn("agent.turn_smoke", check_ids)

    def test_doctor_openrouter_without_key_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config_path = Path(tmp) / "config.json"
            config = OpenMinionConfig()
            _csc_install_default_agent(config, provider="openrouter")
            config.runtime.log_level = "ERROR"
            config.providers.openrouter.api_key = ""
            config.providers.openrouter.api_key_env = "OPENMINION_TEST_OPENROUTER_KEY"
            config.storage.path = str(Path(tmp) / "state" / "doctor.db")
            save_config(config, str(config_path))

            previous = os.environ.pop("OPENMINION_TEST_OPENROUTER_KEY", None)
            try:
                args = Namespace(
                    config=str(config_path),
                    check_turn=False,
                    message="doctor ping",
                    target="doctor",
                    channel=None,
                    json=True,
                )
                buf = io.StringIO()
                with redirect_stdout(buf):
                    code = run_doctor(args)
                self.assertEqual(code, 1)

                payload = json.loads(buf.getvalue())
                self.assertFalse(payload["summary"]["ok"])
                check_ids = {check["id"] for check in payload["checks"]}
                self.assertIn("provider.openrouter.key", check_ids)
            finally:
                if previous is not None:
                    os.environ["OPENMINION_TEST_OPENROUTER_KEY"] = previous

    def test_doctor_cerebras_without_key_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config_path = Path(tmp) / "config.json"
            config = OpenMinionConfig()
            _csc_install_default_agent(config, provider="cerebras")
            config.runtime.log_level = "ERROR"
            config.providers.cerebras.api_key = ""
            config.providers.cerebras.api_key_env = "OPENMINION_TEST_CEREBRAS_KEY"
            config.storage.path = str(Path(tmp) / "state" / "doctor.db")
            save_config(config, str(config_path))

            previous = os.environ.pop("OPENMINION_TEST_CEREBRAS_KEY", None)
            try:
                args = Namespace(
                    config=str(config_path),
                    check_turn=False,
                    message="doctor ping",
                    target="doctor",
                    channel=None,
                    json=True,
                )
                buf = io.StringIO()
                with redirect_stdout(buf):
                    code = run_doctor(args)
                self.assertEqual(code, 1)

                payload = json.loads(buf.getvalue())
                self.assertFalse(payload["summary"]["ok"])
                check_ids = {check["id"] for check in payload["checks"]}
                self.assertIn("provider.cerebras.key", check_ids)
            finally:
                if previous is not None:
                    os.environ["OPENMINION_TEST_CEREBRAS_KEY"] = previous

    def test_doctor_groq_without_key_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config_path = Path(tmp) / "config.json"
            config = OpenMinionConfig()
            _csc_install_default_agent(config, provider="groq")
            config.runtime.log_level = "ERROR"
            config.providers.groq.api_key = ""
            config.providers.groq.api_key_env = "OPENMINION_TEST_GROQ_KEY"
            config.storage.path = str(Path(tmp) / "state" / "doctor.db")
            save_config(config, str(config_path))

            previous = os.environ.pop("OPENMINION_TEST_GROQ_KEY", None)
            try:
                args = Namespace(
                    config=str(config_path),
                    check_turn=False,
                    message="doctor ping",
                    target="doctor",
                    channel=None,
                    json=True,
                )
                buf = io.StringIO()
                with redirect_stdout(buf):
                    code = run_doctor(args)
                self.assertEqual(code, 1)

                payload = json.loads(buf.getvalue())
                self.assertFalse(payload["summary"]["ok"])
                check_ids = {check["id"] for check in payload["checks"]}
                self.assertIn("provider.groq.key", check_ids)
            finally:
                if previous is not None:
                    os.environ["OPENMINION_TEST_GROQ_KEY"] = previous

    def test_doctor_ollama_provider_no_key_ok(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config_path = Path(tmp) / "config.json"
            config = OpenMinionConfig()
            _csc_install_default_agent(config, provider="ollama")
            config.runtime.log_level = "ERROR"
            config.providers.ollama.api_key = ""
            config.providers.ollama.api_key_env = "OPENMINION_TEST_OLLAMA_KEY"
            config.storage.path = str(Path(tmp) / "state" / "doctor.db")
            save_config(config, str(config_path))

            previous = os.environ.pop("OPENMINION_TEST_OLLAMA_KEY", None)
            try:
                args = Namespace(
                    config=str(config_path),
                    check_turn=False,
                    message="doctor ping",
                    target="doctor",
                    channel=None,
                    json=True,
                )
                buf = io.StringIO()
                with redirect_stdout(buf):
                    code = run_doctor(args)

                payload = json.loads(buf.getvalue())
                check_ids = {check["id"] for check in payload["checks"]}
                self.assertEqual(code, 0)
                self.assertNotIn("provider.ollama.key", check_ids)
                self.assertIn("provider.supported", check_ids)
            finally:
                if previous is not None:
                    os.environ["OPENMINION_TEST_OLLAMA_KEY"] = previous

    def test_doctor_cortensor_provider_no_key_ok(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config_path = Path(tmp) / "config.json"
            config = OpenMinionConfig()
            _csc_install_default_agent(config, provider="cortensor")
            config.runtime.log_level = "ERROR"
            config.providers.cortensor.api_key = ""
            config.providers.cortensor.api_key_env = "OPENMINION_TEST_CORTENSOR_KEY"
            config.storage.path = str(Path(tmp) / "state" / "doctor.db")
            save_config(config, str(config_path))

            previous = os.environ.pop("OPENMINION_TEST_CORTENSOR_KEY", None)
            try:
                args = Namespace(
                    config=str(config_path),
                    check_turn=False,
                    message="doctor ping",
                    target="doctor",
                    channel=None,
                    json=True,
                )
                buf = io.StringIO()
                with redirect_stdout(buf):
                    code = run_doctor(args)

                payload = json.loads(buf.getvalue())
                check_by_id = {check["id"]: check for check in payload["checks"]}
                self.assertEqual(code, 0)
                self.assertIn("provider.cortensor.key", check_by_id)
                self.assertEqual(
                    check_by_id["provider.cortensor.key"]["status"], "warn"
                )
                self.assertIn("provider.supported", check_by_id)
            finally:
                if previous is not None:
                    os.environ["OPENMINION_TEST_CORTENSOR_KEY"] = previous

    def test_doctor_cortensor_completion_mode_requires_session_id(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config_path = Path(tmp) / "config.json"
            config = OpenMinionConfig()
            _csc_install_default_agent(config, provider="cortensor")
            config.runtime.log_level = "ERROR"
            config.providers.cortensor.api_mode = "cortensor_completion"
            config.providers.cortensor.session_id = 0
            config.storage.path = str(Path(tmp) / "state" / "doctor.db")
            save_config(config, str(config_path))

            args = Namespace(
                config=str(config_path),
                check_turn=False,
                message="doctor ping",
                target="doctor",
                channel=None,
                json=True,
            )
            buf = io.StringIO()
            with redirect_stdout(buf):
                code = run_doctor(args)

            payload = json.loads(buf.getvalue())
            check_by_id = {check["id"]: check for check in payload["checks"]}
            self.assertEqual(code, 1)
            self.assertIn("provider.cortensor.session_id", check_by_id)
            self.assertEqual(
                check_by_id["provider.cortensor.session_id"]["status"], "fail"
            )

    def test_doctor_cortensor_completion_mode_accepts_session_ids_list(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config_path = Path(tmp) / "config.json"
            config = OpenMinionConfig()
            _csc_install_default_agent(config, provider="cortensor")
            config.runtime.log_level = "ERROR"
            config.providers.cortensor.api_mode = "cortensor_completion"
            config.providers.cortensor.session_id = 0
            config.providers.cortensor.session_ids = [45]
            config.storage.path = str(Path(tmp) / "state" / "doctor.db")
            save_config(config, str(config_path))

            args = Namespace(
                config=str(config_path),
                check_turn=False,
                message="doctor ping",
                target="doctor",
                channel=None,
                json=True,
            )
            buf = io.StringIO()
            with redirect_stdout(buf):
                code = run_doctor(args)

            payload = json.loads(buf.getvalue())
            check_by_id = {check["id"]: check for check in payload["checks"]}
            self.assertEqual(code, 0)
            self.assertIn("provider.cortensor.session_id", check_by_id)
            self.assertEqual(
                check_by_id["provider.cortensor.session_id"]["status"], "ok"
            )

    def test_doctor_cortensor_completion_mode_accepts_dedicated_session_ids_list(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config_path = Path(tmp) / "config.json"
            config = OpenMinionConfig()
            _csc_install_default_agent(config, provider="cortensor")
            config.runtime.log_level = "ERROR"
            config.providers.cortensor.api_mode = "cortensor_completion"
            config.providers.cortensor.session_id = 0
            config.providers.cortensor.dedicated_session_ids = [47]
            config.storage.path = str(Path(tmp) / "state" / "doctor.db")
            save_config(config, str(config_path))

            args = Namespace(
                config=str(config_path),
                check_turn=False,
                message="doctor ping",
                target="doctor",
                channel=None,
                json=True,
            )
            buf = io.StringIO()
            with redirect_stdout(buf):
                code = run_doctor(args)

            payload = json.loads(buf.getvalue())
            check_by_id = {check["id"]: check for check in payload["checks"]}
            self.assertEqual(code, 0)
            self.assertIn("provider.cortensor.session_id", check_by_id)
            self.assertEqual(
                check_by_id["provider.cortensor.session_id"]["status"], "ok"
            )

    def test_doctor_security_summary_warns_for_external_gateway(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config_path = Path(tmp) / "config.json"
            config = OpenMinionConfig()
            _csc_install_default_agent(config, provider="echo")
            config.runtime.log_level = "ERROR"
            config.gateway.host = "0.0.0.0"
            config.storage.path = str(Path(tmp) / "state" / "doctor.db")
            save_config(config, str(config_path))

            args = Namespace(
                config=str(config_path),
                check_turn=False,
                message="doctor ping",
                target="doctor",
                channel=None,
                json=True,
            )
            buf = io.StringIO()
            with redirect_stdout(buf):
                code = run_doctor(args)

            payload = json.loads(buf.getvalue())
            check_by_id = {check["id"]: check for check in payload["checks"]}
            self.assertEqual(code, 0)
            self.assertIn("security.validate.summary", check_by_id)
            self.assertEqual(check_by_id["security.validate.summary"]["status"], "warn")
            self.assertIn("security.gateway.bind_posture", check_by_id)
            self.assertEqual(
                check_by_id["security.gateway.bind_posture"]["status"], "warn"
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
                responsibilities=["Run doctor checks consistently."],
                hard_constraints=["Avoid unverifiable claims."],
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
