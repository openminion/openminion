import io
import json
import unittest
from argparse import Namespace
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

from openminion.cli.commands.verify import _run_smoke_subprocess, run_verify


class VerifyCommandTests(unittest.TestCase):
    def test_verify_all_dispatches_unit_smoke_and_skills(self) -> None:
        args = Namespace(
            suite="all",
            pattern="test_*.py",
            message="verify ping",
            target="verify",
            channel=None,
            verbose=False,
            json=False,
            config=None,
        )

        with mock.patch(
            "openminion.cli.commands.verify._run_unit_tests", return_value=0
        ) as unit_mock:
            with mock.patch(
                "openminion.cli.commands.verify._run_smoke_checks", return_value=0
            ) as smoke_mock:
                with mock.patch(
                    "openminion.cli.commands.verify._run_skill_checks", return_value=0
                ) as skills_mock:
                    buf = io.StringIO()
                    with redirect_stdout(buf):
                        code = run_verify(args)
        self.assertEqual(code, 0)
        unit_mock.assert_called_once()
        smoke_mock.assert_called_once()
        skills_mock.assert_called_once()

    def test_verify_unit_only(self) -> None:
        args = Namespace(
            suite="unit",
            pattern="test_*.py",
            message="verify ping",
            target="verify",
            channel=None,
            verbose=False,
            json=False,
            config=None,
        )

        with mock.patch(
            "openminion.cli.commands.verify._run_unit_tests", return_value=0
        ) as unit_mock:
            with mock.patch(
                "openminion.cli.commands.verify._run_smoke_checks", return_value=0
            ) as smoke_mock:
                with redirect_stdout(io.StringIO()):
                    code = run_verify(args)
        self.assertEqual(code, 0)
        unit_mock.assert_called_once()
        smoke_mock.assert_not_called()

    def test_verify_skills_only(self) -> None:
        args = Namespace(
            suite="skills",
            pattern="test_*.py",
            message="verify ping",
            target="verify",
            channel=None,
            verbose=False,
            json=False,
            config=None,
        )

        with mock.patch(
            "openminion.cli.commands.verify._run_unit_tests", return_value=0
        ) as unit_mock:
            with mock.patch(
                "openminion.cli.commands.verify._run_smoke_checks", return_value=0
            ) as smoke_mock:
                with mock.patch(
                    "openminion.cli.commands.verify._run_skill_checks",
                    return_value=0,
                ) as skills_mock:
                    with redirect_stdout(io.StringIO()):
                        code = run_verify(args)
        self.assertEqual(code, 0)
        unit_mock.assert_not_called()
        smoke_mock.assert_not_called()
        skills_mock.assert_called_once()

    def test_verify_returns_failure_when_any_suite_fails(self) -> None:
        args = Namespace(
            suite="all",
            pattern="test_*.py",
            message="verify ping",
            target="verify",
            channel=None,
            verbose=False,
            json=False,
            config=None,
        )

        with mock.patch(
            "openminion.cli.commands.verify._run_unit_tests", return_value=1
        ):
            with mock.patch(
                "openminion.cli.commands.verify._run_smoke_checks", return_value=0
            ):
                with mock.patch(
                    "openminion.cli.commands.verify._run_skill_checks", return_value=0
                ):
                    with redirect_stdout(io.StringIO()):
                        code = run_verify(args)
        self.assertEqual(code, 1)

    def test_verify_json_output_uses_shared_printer_shape(self) -> None:
        args = Namespace(
            suite="all",
            pattern="test_*.py",
            message="verify ping",
            target="verify",
            channel=None,
            verbose=False,
            json=True,
            config=None,
        )

        with mock.patch(
            "openminion.cli.commands.verify._run_unit_tests", return_value=0
        ):
            with mock.patch(
                "openminion.cli.commands.verify._run_smoke_checks", return_value=1
            ):
                with mock.patch(
                    "openminion.cli.commands.verify._run_skill_checks", return_value=0
                ):
                    buf = io.StringIO()
                    with redirect_stdout(buf):
                        code = run_verify(args)

        self.assertEqual(code, 1)
        self.assertEqual(
            json.loads(buf.getvalue()),
            {
                "ok": False,
                "suite": "all",
                "results": [
                    {"name": "unit", "ok": True, "exit_code": 0},
                    {"name": "smoke", "ok": False, "exit_code": 1},
                    {"name": "skills", "ok": True, "exit_code": 0},
                ],
            },
        )

    def test_verify_unit_tests_missing_dir_fails(self) -> None:
        args = Namespace(
            suite="unit",
            pattern="test_*.py",
            message="verify ping",
            target="verify",
            channel=None,
            verbose=False,
            json=False,
            config=None,
        )
        with mock.patch(
            "openminion.cli.commands.verify._resolve_project_root",
            return_value=Path("/tmp/does-not-exist"),
        ):
            with redirect_stdout(io.StringIO()):
                code = run_verify(args)
        self.assertEqual(code, 1)

    def test_run_smoke_subprocess_omits_config_flag_when_config_is_none(self) -> None:
        args = Namespace(config=None)
        completed = mock.Mock(returncode=0, stdout="ok", stderr="")

        with mock.patch(
            "openminion.cli.commands.verify.subprocess.run", return_value=completed
        ) as run_mock:
            code, output = _run_smoke_subprocess(
                args=args,
                command_args=["doctor", "--json"],
            )

        self.assertEqual(code, 0)
        self.assertEqual(output, "ok")
        run_mock.assert_called_once()
        called_cmd = run_mock.call_args.args[0]
        self.assertEqual(called_cmd[:3], [mock.ANY, "-m", "openminion"])
        self.assertEqual(called_cmd[3:], ["doctor", "--json"])

    def test_run_smoke_subprocess_preserves_explicit_config_flag(self) -> None:
        args = Namespace(config="config/custom.json")
        completed = mock.Mock(returncode=0, stdout="", stderr="")

        with mock.patch(
            "openminion.cli.commands.verify.subprocess.run", return_value=completed
        ) as run_mock:
            _run_smoke_subprocess(
                args=args,
                command_args=["agent-check", "--message", "ping"],
            )

        called_cmd = run_mock.call_args.args[0]
        self.assertEqual(
            called_cmd[3:],
            ["--config", "config/custom.json", "agent-check", "--message", "ping"],
        )
