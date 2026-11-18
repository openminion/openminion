"""The nine `time.*` operations resolve on the HTTP tool route while `time` is visible."""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from openminion.api.runtime import APIRuntime
from openminion.api.server import dispatch_request
from tests.test_api_v1_runtime import _write_echo_config

# Registered in the runtime registry but absent from the model-facing inventory:
# a name the direct path must not admit.
_RUNTIME_ONLY_NAME = "fetch.get"

TIME_OPERATIONS = (
    "time.now",
    "time.in_zone",
    "time.convert",
    "time.parse_iso",
    "time.diff",
    "time.format",
    "time.start_of_day",
    "time.end_of_day",
    "time.next_cron",
)


def _config(tmp: str) -> Path:
    os.environ["OPENMINION_DATA_ROOT"] = str(Path(tmp) / ".openminion")
    return _write_echo_config(Path(tmp))


class TimeDirectToolRouteTests(unittest.TestCase):
    """The route-level admission: both handlers read the same schema lookup."""

    def test_each_operation_resolves_on_the_schema_route(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config_path = _config(tmp)
            for name in TIME_OPERATIONS:
                with self.subTest(tool=name):
                    status, payload = dispatch_request(
                        "GET", f"/v1/tools/{name}/schema", str(config_path)
                    )
                    self.assertEqual(int(status), 200, payload)
                    tool = payload["tool"]
                    self.assertEqual(tool["name"], name)
                    self.assertEqual(tool["source"], "runtime_direct")
                    self.assertIn("properties", tool["parameters"])

    def test_the_run_route_admits_each_direct_operation(self) -> None:
        """The 404 is gone for all nine; execution keeps its existing gate."""

        cases = {
            "time.now": ({}, 200),
            "time.in_zone": ({"timezone": "Europe/Helsinki"}, 400),
            "time.diff": (
                {"a": "2026-03-11T08:00:00Z", "b": "2026-03-11T07:00:00Z"},
                400,
            ),
        }
        with tempfile.TemporaryDirectory() as tmp:
            config_path = _config(tmp)
            for name, (arguments, expected) in cases.items():
                with self.subTest(tool=name):
                    status, payload = dispatch_request(
                        "POST",
                        f"/v1/tools/{name}/run",
                        str(config_path),
                        body={"arguments": arguments, "session_id": "time-direct"},
                    )
                    self.assertEqual(int(status), expected, payload)
                    self.assertEqual(payload["tool"]["name"], name)
                    if expected == 200:
                        self.assertTrue(payload["ok"], payload)
                    else:
                        # Admission changed; the exposure/activation owner did not.
                        self.assertEqual(
                            payload["tool"]["data"]["error_code"],
                            "tool_exposure_denied",
                        )

    def test_unrelated_runtime_only_names_stay_refused(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config_path = _config(tmp)
            schema_status, schema_payload = dispatch_request(
                "GET", f"/v1/tools/{_RUNTIME_ONLY_NAME}/schema", str(config_path)
            )
            self.assertEqual(int(schema_status), 404, schema_payload)
            self.assertEqual(schema_payload["error"]["code"], "tool_not_found")

            run_status, run_payload = dispatch_request(
                "POST",
                f"/v1/tools/{_RUNTIME_ONLY_NAME}/run",
                str(config_path),
                body={"arguments": {}},
            )
            self.assertEqual(int(run_status), 404, run_payload)
            self.assertEqual(run_payload["error"]["code"], "tool_not_found")


class TimeDirectToolOwnerTests(unittest.TestCase):
    """The admission predicate itself, driven through the runtime owner."""

    def test_a_missing_visible_parent_closes_direct_admission(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runtime = APIRuntime.from_config_path(str(_config(tmp)))
            try:
                with mock.patch(
                    "openminion.api.queries.runtime_reports.build_tool_inventory_report",
                    return_value=[{"name": "weather"}],
                ):
                    for name in TIME_OPERATIONS:
                        with self.subTest(tool=name):
                            self.assertIsNone(
                                runtime.tool_schema_report(tool_name=name)
                            )
            finally:
                runtime.close()

    def test_unregistering_one_operation_keeps_its_neighbours(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runtime = APIRuntime.from_config_path(str(_config(tmp)))
            try:
                runtime.tools.unregister("time.in_zone")

                self.assertIsNone(runtime.tool_schema_report(tool_name="time.in_zone"))
                for name in TIME_OPERATIONS:
                    if name == "time.in_zone":
                        continue
                    with self.subTest(tool=name):
                        self.assertIsNotNone(runtime.tool_schema_report(tool_name=name))
                self.assertIsNone(
                    runtime.tool_schema_report(tool_name=_RUNTIME_ONLY_NAME)
                )
            finally:
                runtime.close()
