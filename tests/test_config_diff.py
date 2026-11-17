from __future__ import annotations

import hashlib
import io
import json
import os
import tempfile
import unittest
from argparse import Namespace
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

from openminion.cli.commands.config import config_diff, config_init


def _secret(tag: str) -> str:
    return "sk-" + tag + "-" + "probe"


def _url(tag: str) -> str:
    return "postgresql://user:" + tag + "@localhost/db"


def _namespace(**overrides):
    base = {
        "config": None,
        "home_root": None,
        "data_root": None,
        "input": "",
        "replace": False,
        "json": False,
    }
    base.update(overrides)
    return Namespace(**base)


class ConfigDiffTests(unittest.TestCase):
    def _run(self, root: Path, **overrides) -> tuple[int, str]:
        buffer = io.StringIO()
        with (
            mock.patch.dict(
                os.environ, {"OPENMINION_HOME": "", "OPENMINION_DATA_ROOT": ""}
            ),
            mock.patch("pathlib.Path.cwd", return_value=root),
            redirect_stdout(buffer),
        ):
            code = config_diff(_namespace(**overrides))
        return code, buffer.getvalue()

    def _seed(self, root: Path, **runtime) -> Path:
        config_path = root / "cfg" / "config.json"
        config_init(
            Namespace(
                config=str(config_path),
                force=False,
                provider="openai",
                storage_location="config",
                storage_path=None,
            )
        )
        payload = json.loads(config_path.read_text(encoding="utf-8"))
        payload["runtime"].update({"log_level": "DEBUG", **runtime})
        config_path.write_text(
            json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8"
        )
        return config_path

    def _candidate(self, root: Path, body: str) -> Path:
        path = root / "candidate.yaml"
        path.write_text(body, encoding="utf-8")
        return path

    def test_identical_candidate_reports_no_changes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            active = self._seed(root)
            candidate = self._candidate(root, active.read_text(encoding="utf-8"))

            code, output = self._run(root, config=str(active), input=str(candidate))

            self.assertEqual(code, 0)
            self.assertIn("No changes", output)
            self.assertIn("environment overrides", output)

    def test_changed_value_is_reported_and_exit_code_is_one(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            active = self._seed(root)
            candidate = self._candidate(root, "runtime:\n  log_level: WARNING\n")

            code, output = self._run(root, config=str(active), input=str(candidate))

            self.assertEqual(code, 1)
            self.assertIn("runtime.log_level", output)
            self.assertIn("WARNING", output)

    def test_json_payload_lists_changes_and_contract_flags(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            active = self._seed(root)
            candidate = self._candidate(root, "runtime:\n  log_level: WARNING\n")

            code, output = self._run(
                root, config=str(active), input=str(candidate), json=True
            )

            payload = json.loads(output)
            self.assertEqual(code, 1)
            self.assertTrue(payload["secret_values_redacted"])
            self.assertTrue(payload["env_overrides_applied"])
            changes = {c["path"]: c for c in payload["changes"]}
            self.assertEqual(changes["runtime.log_level"]["before"], "DEBUG")
            self.assertEqual(changes["runtime.log_level"]["after"], "WARNING")

    def test_replace_mode_reports_removals_that_merge_keeps(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            active = self._seed(root)
            candidate = self._candidate(root, "runtime:\n  log_level: WARNING\n")

            merge_code, merge_out = self._run(
                root, config=str(active), input=str(candidate), json=True
            )
            repl_code, repl_out = self._run(
                root, config=str(active), input=str(candidate), replace=True, json=True
            )

            merge_changes = json.loads(merge_out)["changes"]
            replace_changes = json.loads(repl_out)["changes"]
            self.assertEqual((merge_code, repl_code), (1, 1))
            self.assertEqual([c["kind"] for c in merge_changes], ["changed"])
            self.assertIn("removed", {c["kind"] for c in replace_changes})
            self.assertEqual(json.loads(repl_out)["mode"], "replace")

    def test_secret_only_change_is_reported_without_values(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            active = self._seed(root)
            payload = json.loads(active.read_text(encoding="utf-8"))
            payload["providers"]["openai"]["api_key"] = _secret("active")
            active.write_text(
                json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8"
            )
            candidate = self._candidate(
                root,
                "providers:\n  openai:\n    api_key: " + _secret("candidate") + "\n",
            )

            code, output = self._run(
                root, config=str(active), input=str(candidate), json=True
            )

            changes = json.loads(output)["changes"]
            self.assertEqual(code, 1)
            self.assertEqual(len(changes), 1)
            self.assertEqual(changes[0]["path"], "providers.openai.api_key")
            self.assertTrue(changes[0]["redacted"])
            self.assertEqual(changes[0]["before"], changes[0]["after"])
            self.assertNotIn(_secret("active"), output)
            self.assertNotIn(_secret("candidate"), output)

    def test_url_credentials_are_masked_in_text_and_json(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            active = self._seed(root)
            payload = json.loads(active.read_text(encoding="utf-8"))
            payload["storage"]["postgres_url"] = _url("active")
            active.write_text(
                json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8"
            )
            candidate = self._candidate(
                root, "storage:\n  postgres_url: " + _url("candidate") + "\n"
            )

            text_code, text_out = self._run(
                root, config=str(active), input=str(candidate)
            )
            json_code, json_out = self._run(
                root, config=str(active), input=str(candidate), json=True
            )

            self.assertEqual((text_code, json_code), (1, 1))
            combined = text_out + json_out
            self.assertNotIn("active@", combined)
            self.assertNotIn("candidate@", combined)
            self.assertIn("user:***@localhost/db", combined)
            change = {c["path"]: c for c in json.loads(json_out)["changes"]}[
                "storage.postgres_url"
            ]
            self.assertTrue(change["redacted"])

    def test_environment_map_secret_is_redacted(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            active = self._seed(root)
            candidate = self._candidate(
                root,
                "runtime:\n  env:\n    OPENAI_API_KEY: " + _secret("env") + "\n",
            )

            code, output = self._run(
                root, config=str(active), input=str(candidate), json=True
            )

            self.assertEqual(code, 1)
            self.assertNotIn(_secret("env"), output)
            change = {c["path"]: c for c in json.loads(output)["changes"]}[
                "runtime.env.OPENAI_API_KEY"
            ]
            self.assertTrue(change["redacted"])
            self.assertEqual(change["after"], "<redacted>")

    def test_added_nested_header_config_is_redacted(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            active = self._seed(root)
            candidate = self._candidate(
                root,
                "runtime:\n  telemetry_exporter:\n    headers:\n"
                "      Authorization: " + _secret("header") + "\n",
            )

            code, output = self._run(
                root, config=str(active), input=str(candidate), json=True
            )

            changes = json.loads(output)["changes"]
            self.assertEqual(code, 1)
            self.assertNotIn(_secret("header"), output)
            header_change = {c["path"]: c for c in changes}[
                "runtime.telemetry_exporter.headers.Authorization"
            ]
            self.assertEqual(header_change["kind"], "added")
            self.assertTrue(header_change["redacted"])
            self.assertEqual(header_change["after"], "<redacted>")

    def test_credential_field_holding_url_text_is_fully_redacted(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            active = self._seed(root)
            candidate = self._candidate(
                root,
                "providers:\n  openai:\n    api_key: " + _url("urlkey") + "\n",
            )

            code, output = self._run(
                root, config=str(active), input=str(candidate), json=True
            )

            self.assertEqual(code, 1)
            self.assertNotIn("urlkey@", output)
            change = {c["path"]: c for c in json.loads(output)["changes"]}[
                "providers.openai.api_key"
            ]
            self.assertEqual(change["after"], "<redacted>")

    def test_env_override_masks_a_differing_stored_value(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            active = self._seed(root)
            payload = json.loads(active.read_text(encoding="utf-8"))
            payload["storage"]["postgres_url"] = "postgresql://stored-host/db"
            active.write_text(
                json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8"
            )
            candidate = self._candidate(
                root, "storage:\n  postgres_url: postgresql://other-host/db\n"
            )

            with mock.patch.dict(
                os.environ,
                {
                    "OPENMINION_HOME": "",
                    "OPENMINION_DATA_ROOT": "",
                    "OPENMINION_STORAGE_POSTGRES_URL": "postgresql://env-host/db",
                },
            ):
                code, output = self._run(
                    root, config=str(active), input=str(candidate), json=True
                )

            self.assertEqual(code, 0)
            self.assertEqual(json.loads(output)["changed"], 0)
            self.assertTrue(json.loads(output)["env_overrides_applied"])

    def test_active_config_bytes_are_preserved_on_success_and_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            active = self._seed(root)
            candidate = self._candidate(root, "runtime:\n  log_level: WARNING\n")
            before = hashlib.sha256(active.read_bytes()).hexdigest()

            self._run(root, config=str(active), input=str(candidate))
            with self.assertRaises(RuntimeError):
                self._run(root, config=str(active), input=str(root / "absent.yaml"))

            self.assertEqual(before, hashlib.sha256(active.read_bytes()).hexdigest())

    def test_missing_candidate_file_is_a_typed_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            active = self._seed(root)

            with self.assertRaises(RuntimeError) as raised:
                self._run(root, config=str(active), input=str(root / "absent.yaml"))

            self.assertIn("not found", str(raised.exception))

    def test_no_input_is_a_typed_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            active = self._seed(root)

            with self.assertRaises(RuntimeError) as raised:
                self._run(root, config=str(active))

            self.assertIn("requires a file path", str(raised.exception))
