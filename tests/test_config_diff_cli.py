from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO_SRC = str(Path(__file__).resolve().parents[1] / "src")


def _secret(tag: str) -> str:
    return "sk-" + tag + "-" + "probe"


def _url(tag: str, host: str = "localhost") -> str:
    return "postgresql://user:" + tag + "@" + host + "/db"


def _run_cli(root: Path, *args: str) -> subprocess.CompletedProcess[str]:
    """Run the real CLI entry point in a subprocess, without constructing a runtime."""
    env = {
        "PATH": os.environ.get("PATH", ""),
        "PYTHONPATH": REPO_SRC,
        "HOME": str(root / "home"),
        "OPENMINION_HOME": "",
        "OPENMINION_DATA_ROOT": "",
        "NO_COLOR": "1",
    }
    return subprocess.run(
        [sys.executable, "-m", "openminion", *args],
        cwd=root,
        env=env,
        capture_output=True,
        text=True,
        timeout=180,
    )


class ConfigDiffCliTests(unittest.TestCase):
    def _seed(self, root: Path) -> Path:
        config_path = root / "cfg" / "config.json"
        init = _run_cli(
            root, "--config", str(config_path), "config", "init", "--provider", "echo"
        )
        self.assertEqual(init.returncode, 0, init.stderr)
        return config_path

    def test_environment_secret_is_not_disclosed_by_the_cli(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config_path = self._seed(root)
            candidate = root / "candidate.yaml"
            candidate.write_text(
                "runtime:\n  env:\n    OPENAI_API_KEY: " + _secret("cli") + "\n",
                encoding="utf-8",
            )

            result = _run_cli(
                root,
                "--config",
                str(config_path),
                "config",
                "diff",
                str(candidate),
                "--json",
            )

            self.assertEqual(result.returncode, 1, result.stderr)
            self.assertNotIn(_secret("cli"), result.stdout + result.stderr)
            payload = json.loads(result.stdout)
            change = {item["path"]: item for item in payload["changes"]}[
                "runtime.env.OPENAI_API_KEY"
            ]
            self.assertTrue(change["redacted"])

    def test_url_password_is_masked_by_the_cli(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config_path = self._seed(root)
            payload = json.loads(config_path.read_text(encoding="utf-8"))
            payload["storage"]["postgres_url"] = _url("stored", "host-a")
            config_path.write_text(
                json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8"
            )
            candidate = root / "candidate.yaml"
            candidate.write_text(
                "storage:\n  postgres_url: " + _url("staged", "host-b") + "\n",
                encoding="utf-8",
            )

            result = _run_cli(
                root, "--config", str(config_path), "config", "diff", str(candidate)
            )

            self.assertEqual(result.returncode, 1, result.stderr)
            self.assertNotIn("stored@", result.stdout)
            self.assertNotIn("staged@", result.stdout)
            self.assertIn("user:***@host-a/db", result.stdout)
            self.assertIn("user:***@host-b/db", result.stdout)

    def test_missing_input_is_a_documented_error_without_side_effects(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config_path = self._seed(root)
            before = hashlib.sha256(config_path.read_bytes()).hexdigest()

            result = _run_cli(
                root,
                "--config",
                str(config_path),
                "config",
                "diff",
                str(root / "absent.yaml"),
            )

            self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
            self.assertEqual(result.stdout, "")
            self.assertIn("not found", result.stderr)
            self.assertNotIn("Traceback", result.stderr)
            after = hashlib.sha256(config_path.read_bytes()).hexdigest()
            self.assertEqual(before, after)

    def test_export_masks_url_credentials_and_removes_credential_fields(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config_path = self._seed(root)
            payload = json.loads(config_path.read_text(encoding="utf-8"))
            payload["storage"]["postgres_url"] = "postgresql://u:export-pass@host/db"
            payload.setdefault("providers", {}).setdefault("openai", {})["api_key"] = (
                _secret("export")
            )
            config_path.write_text(
                json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8"
            )

            result = _run_cli(root, "--config", str(config_path), "config", "export")

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertNotIn("export-pass", result.stdout)
            self.assertNotIn(_secret("export"), result.stdout)
            self.assertIn(":***@", result.stdout)
            self.assertIn("URL credentials masked", result.stdout)

    def test_dotted_header_key_is_redacted_and_plain_neighbor_kept(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config_path = self._seed(root)
            candidate = root / "candidate.yaml"
            candidate.write_text(
                "runtime:\n  telemetry_exporter:\n    headers:\n"
                "      X.Custom-Secret: " + _secret("dotted") + "\n"
                "      X.Custom-Trace: trace-visible\n",
                encoding="utf-8",
            )

            result = _run_cli(
                root,
                "--config",
                str(config_path),
                "config",
                "diff",
                str(candidate),
                "--json",
            )

            self.assertEqual(result.returncode, 1, result.stderr)
            self.assertNotIn(_secret("dotted"), result.stdout)
            self.assertIn("trace-visible", result.stdout)
            changes = {
                item["path"]: item for item in json.loads(result.stdout)["changes"]
            }
            secret_change = changes[
                "runtime.telemetry_exporter.headers.X.Custom-Secret"
            ]
            self.assertTrue(secret_change["redacted"])
            self.assertEqual(secret_change["after"], "<redacted>")
            trace_change = changes["runtime.telemetry_exporter.headers.X.Custom-Trace"]
            self.assertFalse(trace_change.get("redacted"))

    def test_added_container_is_marked_and_keeps_plain_values(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config_path = self._seed(root)
            candidate = root / "candidate.yaml"
            candidate.write_text(
                "channels:\n  slack:\n    workspace: w-visible\n"
                "    bot_token: " + _secret("container") + "\n",
                encoding="utf-8",
            )

            result = _run_cli(
                root,
                "--config",
                str(config_path),
                "config",
                "diff",
                str(candidate),
                "--json",
            )

            self.assertEqual(result.returncode, 1, result.stderr)
            self.assertNotIn(_secret("container"), result.stdout)
            changes = {
                item["path"]: item for item in json.loads(result.stdout)["changes"]
            }
            added = changes["channels.slack"]
            self.assertEqual(added["kind"], "added")
            self.assertTrue(added.get("redacted"))
            self.assertEqual(added["after"].get("workspace"), "w-visible")
            self.assertNotIn("bot_token", added["after"])
