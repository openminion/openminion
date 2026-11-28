"""The declared `time.*` operations resolve and execute through the direct runtime lanes."""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from openminion.api.operations.tools import execute_tool_run
from openminion.api.runtime import APIRuntime
from openminion.api.server import dispatch_request
from openminion.modules.llm.providers.base import ProviderToolCall
from openminion.modules.policy.adapters.composition import (
    SEAM_API_TOOLS,
    build_default_composition_boundary_adapter,
)
from openminion.modules.tool.base import ToolExecutionContext
from openminion.modules.tool.contracts.runtime_ids import RUNTIME_TIME_NOW
from openminion.modules.tool.errors import ToolRuntimeError
from openminion.modules.tool.executor import execute_single_call
from openminion.modules.tool.exposure import ToolExposureProfile
from openminion.modules.tool.runtime.policy import Policy
from openminion.modules.tool.runtime.policy_checks import run_policy_preflight
from openminion.tools.time.constants import TIME_RUNTIME_TOOL_NAMES
from openminion.tools.time.plugin import _time_tool_specs
from openminion.tools.time.registrar import REGISTRAR
from tests.test_api_v1_runtime import _write_echo_config

# Registered in the runtime registry but absent from the model-facing inventory:
# a name the direct path must not admit.
_RUNTIME_ONLY_NAME = "fetch.get"

_OPERATION_PARAMETERS = {
    "time.now": {"timezone", "location"},
    "time.in_zone": {"timezone"},
    "time.convert": {"iso", "to_timezone"},
    "time.parse_iso": {"iso", "timezone_hint"},
    "time.diff": {"a", "b", "unit", "abs"},
    "time.format": {"iso", "timezone", "format", "custom"},
    "time.start_of_day": {"iso", "timezone"},
    "time.end_of_day": {"iso", "timezone"},
    "time.next_cron": {"cron", "timezone", "from_iso", "count"},
}

_OPERATION_REQUIRED = {
    "time.now": set(),
    "time.in_zone": {"timezone"},
    "time.convert": {"iso", "to_timezone"},
    "time.parse_iso": {"iso"},
    "time.diff": {"a", "b"},
    "time.format": {"iso"},
    "time.start_of_day": set(),
    "time.end_of_day": set(),
    "time.next_cron": {"cron", "timezone"},
}

_OPERATION_ARGUMENTS = {
    "time.now": {},
    "time.in_zone": {"timezone": "Europe/Helsinki"},
    "time.convert": {
        "iso": "2026-03-11T08:00:00Z",
        "to_timezone": "Europe/Helsinki",
    },
    "time.parse_iso": {"iso": "2026-03-11T08:00:00Z"},
    "time.diff": {
        "a": "2026-03-11T08:00:00Z",
        "b": "2026-03-11T07:00:00Z",
    },
    "time.format": {
        "iso": "2026-03-11T08:00:00Z",
        "timezone": "Europe/Helsinki",
        "format": "date",
    },
    "time.start_of_day": {
        "iso": "2026-03-11T08:00:00Z",
        "timezone": "Europe/Helsinki",
    },
    "time.end_of_day": {
        "iso": "2026-03-11T08:00:00Z",
        "timezone": "Europe/Helsinki",
    },
    "time.next_cron": {
        "cron": "0 9 * * 1-5",
        "timezone": "Europe/Helsinki",
        "from_iso": "2026-03-11T00:00:00Z",
        "count": 2,
    },
}


def _config(tmp: str) -> Path:
    os.environ["OPENMINION_DATA_ROOT"] = str(Path(tmp) / ".openminion")
    return _write_echo_config(Path(tmp))


def _time_profile() -> ToolExposureProfile:
    return ToolExposureProfile(
        profile_id="time_only",
        title="Time only",
        summary="Owns the time family for the direct-lane denial controls.",
        tool_names=frozenset({"time"}),
    )


def _direct_call(
    runtime: APIRuntime,
    *,
    tool_name: str,
    arguments: dict,
    session_id: str = "time-direct",
):
    return execute_single_call(
        runtime.tools,
        call=ProviderToolCall(name=tool_name, arguments=arguments, source="inproc"),
        context=ToolExecutionContext(
            channel="console",
            target="cli-tools",
            session_id=session_id,
            metadata={"session_id": session_id},
        ),
        available_tool_names=tuple(runtime.tools._tools),
        runtime_binding_policies=None,
    )


def _model_call(runtime: APIRuntime, *, tool_name: str, arguments: dict):
    return execute_single_call(
        runtime.tools,
        call=ProviderToolCall(name=tool_name, arguments=arguments, source="model"),
        context=ToolExecutionContext(
            channel="console",
            target="brain",
            session_id="time-direct",
            metadata={"session_id": "time-direct", "tool_call_origin": "model"},
        ),
        available_tool_names=tuple(runtime.tools._tools),
        runtime_binding_policies=None,
    )


def _execution_context(*, session_id: str = "time-direct") -> ToolExecutionContext:
    return ToolExecutionContext(
        channel="console",
        target="cli-tools",
        session_id=session_id,
        metadata={"session_id": session_id},
    )


class _DenyingCompositionBoundary:
    """Controlled composition-boundary denial; it changes no policy semantics."""

    def __init__(self) -> None:
        self.seen: list[str] = []

    def step(self, tool_spec: object) -> None:
        self.seen.append(str(getattr(tool_spec, "name", "") or ""))
        raise ToolRuntimeError(
            "CONFIRM_REQUIRED",
            "confirmation required at the composition boundary",
        )


class TimeDirectToolRouteTests(unittest.TestCase):
    """The route-level admission: both handlers read the same schema lookup."""

    def test_each_operation_resolves_with_its_own_parameters(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config_path = _config(tmp)
            for name in TIME_RUNTIME_TOOL_NAMES:
                with self.subTest(tool=name):
                    status, payload = dispatch_request(
                        "GET", f"/v1/tools/{name}/schema", str(config_path)
                    )
                    self.assertEqual(int(status), 200, payload)
                    tool = payload["tool"]
                    self.assertEqual(tool["name"], name)
                    self.assertEqual(tool["source"], "runtime_direct")
                    self.assertEqual(
                        set(tool["parameters"]["properties"]),
                        _OPERATION_PARAMETERS[name],
                    )
                    self.assertEqual(
                        set(tool["parameters"].get("required", [])),
                        _OPERATION_REQUIRED[name],
                    )

    def test_the_run_route_executes_each_direct_operation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config_path = _config(tmp)
            for name in TIME_RUNTIME_TOOL_NAMES:
                with self.subTest(tool=name):
                    status, payload = dispatch_request(
                        "POST",
                        f"/v1/tools/{name}/run",
                        str(config_path),
                        body={
                            "arguments": _OPERATION_ARGUMENTS[name],
                            "session_id": "time-direct",
                        },
                    )
                    self.assertEqual(int(status), 200, payload)
                    self.assertTrue(payload["ok"], payload)
                    self.assertEqual(payload["tool"]["name"], name)
                    self.assertNotIn("error_code", payload["tool"]["data"], payload)

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

    def test_unknown_direct_time_names_stay_refused(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config_path = _config(tmp)
            for name in ("time.unknown", "time.now.extra"):
                with self.subTest(tool=name):
                    status, payload = dispatch_request(
                        "GET", f"/v1/tools/{name}/schema", str(config_path)
                    )
                    self.assertEqual(int(status), 404, payload)
                    self.assertEqual(payload["error"]["code"], "tool_not_found")


class TimeDirectToolOwnerTests(unittest.TestCase):
    """The admission predicate itself, driven through the runtime owner."""

    def test_unregistering_one_operation_keeps_its_neighbours(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runtime = APIRuntime.from_config_path(str(_config(tmp)))
            try:
                runtime.tools.unregister("time.in_zone")

                self.assertIsNone(runtime.tool_schema_report(tool_name="time.in_zone"))
                for name in TIME_RUNTIME_TOOL_NAMES:
                    if name == "time.in_zone":
                        continue
                    with self.subTest(tool=name):
                        self.assertIsNotNone(runtime.tool_schema_report(tool_name=name))
                self.assertIsNone(
                    runtime.tool_schema_report(tool_name=_RUNTIME_ONLY_NAME)
                )
            finally:
                runtime.close()

    def test_unregistering_the_canonical_operation_keeps_direct_neighbours(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config_path = _config(tmp)
            runtime = APIRuntime.from_config_path(str(config_path))
            try:
                runtime.tools.unregister("time.now")

                # The canonical model-facing `time` matches base: no schema and no
                # execution, and never a quiet substitution of `time.in_zone`.
                self.assertIsNone(runtime.tool_schema_report(tool_name="time"))
                schema_status, schema_payload = dispatch_request(
                    "GET", "/v1/tools/time/schema", str(config_path), runtime=runtime
                )
                self.assertEqual(int(schema_status), 404, schema_payload)
                self.assertEqual(schema_payload["error"]["code"], "tool_not_found")

                run_status, run_payload = dispatch_request(
                    "POST",
                    "/v1/tools/time/run",
                    str(config_path),
                    body={"arguments": {"timezone": "UTC"}},
                    runtime=runtime,
                )
                self.assertEqual(int(run_status), 404, run_payload)
                self.assertEqual(run_payload["error"]["code"], "tool_not_found")

                status, payload, _ = execute_tool_run(
                    runtime=runtime,
                    tool_name="time",
                    arguments={"timezone": "UTC"},
                    request_id="time-direct",
                    channel="console",
                    target="cli-tools",
                    requested_session_id="time-direct",
                )
                self.assertEqual(int(status), 400, payload)
                self.assertFalse(payload["ok"])
                self.assertEqual(payload["tool"]["name"], "time")
                data = payload["tool"]["data"]
                self.assertEqual(data["error_code"], "unknown_tool_name")
                self.assertEqual(
                    data["runtime_resolution_source"], "binding_unavailable"
                )
                self.assertEqual(data["runtime_tool_name"], "")

                # The CLI `tools run` gate reads the same model provider specs.
                self.assertIsNone(runtime.tools.provider_spec_for_name("time"))

                # Live explicit neighbours stay reportable and executable.
                for name in TIME_RUNTIME_TOOL_NAMES:
                    if name == "time.now":
                        continue
                    with self.subTest(tool=name):
                        self.assertIsNotNone(runtime.tool_schema_report(tool_name=name))
                in_zone_status, in_zone_payload = dispatch_request(
                    "GET",
                    "/v1/tools/time.in_zone/schema",
                    str(config_path),
                    runtime=runtime,
                )
                self.assertEqual(int(in_zone_status), 200, in_zone_payload)
                self.assertEqual(in_zone_payload["tool"]["name"], "time.in_zone")
                self.assertEqual(in_zone_payload["tool"]["source"], "runtime_direct")
                in_zone_run_status, in_zone_run_payload = dispatch_request(
                    "POST",
                    "/v1/tools/time.in_zone/run",
                    str(config_path),
                    body={"arguments": {"timezone": "Europe/Helsinki"}},
                    runtime=runtime,
                )
                self.assertEqual(int(in_zone_run_status), 200, in_zone_run_payload)
                self.assertTrue(in_zone_run_payload["ok"], in_zone_run_payload)
                self.assertEqual(in_zone_run_payload["tool"]["name"], "time.in_zone")

                # The model-origin lanes keep refusing both names.
                model_direct = _model_call(
                    runtime,
                    tool_name="time.in_zone",
                    arguments={"timezone": "Europe/Helsinki"},
                )
                self.assertFalse(model_direct.ok)
                self.assertEqual(model_direct.data["error_code"], "unknown_tool_name")
                model_canonical = _model_call(runtime, tool_name="time", arguments={})
                self.assertFalse(model_canonical.ok)
                self.assertEqual(
                    model_canonical.data["error_code"], "unknown_tool_name"
                )
                self.assertEqual(
                    model_canonical.data["runtime_resolution_source"],
                    "binding_unavailable",
                )

                # Unrelated canonical fallback is untouched.
                self.assertEqual(
                    runtime.tools.model_to_runtime_tool_map()["web.fetch"],
                    "fetch.get",
                )
                web_schema = runtime.tool_schema_report(tool_name="web.fetch")
                self.assertIsNotNone(web_schema)
                self.assertEqual(web_schema["runtime_tool_name"], "fetch.get")
            finally:
                runtime.close()

    def test_unregistered_requested_name_is_refused_without_fallback(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runtime = APIRuntime.from_config_path(str(_config(tmp)))
            try:
                runtime.tools.unregister("time.in_zone")
                result = _direct_call(
                    runtime,
                    tool_name="time.in_zone",
                    arguments={"timezone": "Europe/Helsinki"},
                )
                self.assertFalse(result.ok)
                self.assertEqual(result.data["error_code"], "unknown_tool_name")
                self.assertEqual(result.tool_name, "time.in_zone")
                self.assertNotIn("runtime_tool_name", result.data)
            finally:
                runtime.close()

    def test_inactive_profile_hides_the_parent_and_denies_execution(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runtime = APIRuntime.from_config_path(str(_config(tmp)))
            try:
                runtime.tools.exposure_service.register_profiles((_time_profile(),))
                for name in TIME_RUNTIME_TOOL_NAMES:
                    with self.subTest(tool=name):
                        self.assertIsNone(runtime.tool_schema_report(tool_name=name))
                result = _direct_call(
                    runtime,
                    tool_name="time.in_zone",
                    arguments={"timezone": "Europe/Helsinki"},
                )
                self.assertFalse(result.ok)
                self.assertEqual(result.data["error_code"], "tool_exposure_denied")
                self.assertEqual(result.data["reason_code"], "profile_inactive")
            finally:
                runtime.close()

    def test_inactive_time_profile_keeps_explicit_neighbours_hidden(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config_path = _config(tmp)
            runtime = APIRuntime.from_config_path(str(config_path))
            try:
                runtime.tools.exposure_service.register_profiles((_time_profile(),))
                runtime.tools.unregister("time.now")
                for name in ("time", "time.in_zone"):
                    with self.subTest(tool=name):
                        self.assertIsNone(runtime.tool_schema_report(tool_name=name))
                        status, payload = dispatch_request(
                            "GET",
                            f"/v1/tools/{name}/schema",
                            str(config_path),
                            runtime=runtime,
                        )
                        self.assertEqual(int(status), 404, payload)
                        self.assertEqual(payload["error"]["code"], "tool_not_found")

                run_status, run_payload = dispatch_request(
                    "POST",
                    "/v1/tools/time.in_zone/run",
                    str(config_path),
                    body={"arguments": {"timezone": "Europe/Helsinki"}},
                    runtime=runtime,
                )
                self.assertEqual(int(run_status), 404, run_payload)
                self.assertEqual(run_payload["error"]["code"], "tool_not_found")

                direct = _direct_call(
                    runtime,
                    tool_name="time.in_zone",
                    arguments={"timezone": "Europe/Helsinki"},
                )
                self.assertFalse(direct.ok)
                self.assertEqual(direct.data["error_code"], "tool_exposure_denied")
                self.assertEqual(direct.data["reason_code"], "profile_inactive")
            finally:
                runtime.close()

    def test_same_process_activation_executes_but_the_schema_stays_blind(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config_path = _config(tmp)
            runtime = APIRuntime.from_config_path(str(config_path))
            try:
                runtime.tools.exposure_service.register_profiles((_time_profile(),))
                runtime.tools.exposure_service.activate(
                    "time_only", session_id="activation-session"
                )
                activated = _direct_call(
                    runtime,
                    tool_name="time.in_zone",
                    arguments={"timezone": "Europe/Helsinki"},
                    session_id="activation-session",
                )
                self.assertTrue(activated.ok, activated.data)
                # The HTTP schema route carries no session, so an activated
                # non-default profile is still invisible there.
                self.assertIsNone(runtime.tool_schema_report(tool_name="time.in_zone"))
            finally:
                runtime.close()

            # A fresh invocation builds a fresh exposure service; activations are
            # process-local and are not persisted or shared across runtimes.
            separate = APIRuntime.from_config_path(str(config_path))
            try:
                separate.tools.exposure_service.register_profiles((_time_profile(),))
                denied = _direct_call(
                    separate,
                    tool_name="time.in_zone",
                    arguments={"timezone": "Europe/Helsinki"},
                    session_id="activation-session",
                )
                self.assertFalse(denied.ok)
                self.assertEqual(denied.data["error_code"], "tool_exposure_denied")
                self.assertEqual(denied.data["reason_code"], "profile_inactive")
            finally:
                separate.close()

    def test_canonical_model_facing_time_behavior_is_unchanged(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runtime = APIRuntime.from_config_path(str(_config(tmp)))
            try:
                model_time = _model_call(runtime, tool_name="time", arguments={})
                self.assertTrue(model_time.ok, model_time.data)
                self.assertEqual(model_time.tool_name, "time.now")

                model_direct = _model_call(
                    runtime,
                    tool_name="time.in_zone",
                    arguments={"timezone": "Europe/Helsinki"},
                )
                self.assertFalse(model_direct.ok)
                self.assertEqual(model_direct.data["error_code"], "unknown_tool_name")
            finally:
                runtime.close()

    def test_permission_and_confirmation_paths_are_unchanged(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runtime = APIRuntime.from_config_path(str(_config(tmp)))
            try:
                # The default read-only treatment of the declared specs is intact.
                direct = _direct_call(
                    runtime,
                    tool_name="time.in_zone",
                    arguments={"timezone": "Europe/Helsinki"},
                )
                self.assertTrue(direct.ok, direct.data)
                self.assertEqual(direct.data["tool_min_scope"], "READ_ONLY")

                # A policy that requires confirmation still denies the same spec
                # without `confirm`, and the direct admission does not bypass it.
                policy = Policy(
                    raw={
                        "version": 1,
                        "scope": "READ_ONLY",
                        "workspace_root": tmp,
                        "tools": {"allow_prefix": ["time"]},
                        "paths": {
                            "read_allow": [tmp],
                            "write_allow": [tmp],
                            "deny": [],
                        },
                        "confirm": {"required_tools": ["time.in_zone"]},
                    }
                )
                spec = runtime.tools._tools["time.in_zone"]
                with self.assertRaises(ToolRuntimeError) as raised:
                    run_policy_preflight(
                        policy=policy,
                        tool_spec=spec,
                        tool_name="time.in_zone",
                        args={"timezone": "Europe/Helsinki"},
                        effective_scope="READ_ONLY",
                        confirm=False,
                        workspace=Path(tmp),
                    )
                self.assertEqual(raised.exception.code, "CONFIRM_REQUIRED")
                run_policy_preflight(
                    policy=policy,
                    tool_spec=spec,
                    tool_name="time.in_zone",
                    args={"timezone": "Europe/Helsinki"},
                    effective_scope="READ_ONLY",
                    confirm=True,
                    workspace=Path(tmp),
                )
            finally:
                runtime.close()

    def test_time_direct_execution_crosses_the_composition_boundary(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runtime = APIRuntime.from_config_path(str(_config(tmp)))
            calls: list[str] = []

            def spy_handler(arguments: dict, ctx: object) -> dict:
                del arguments, ctx
                calls.append("ran")
                return {
                    "ok": True,
                    "content": "spy",
                    "data": {"tool_min_scope": "READ_ONLY"},
                }

            spec = runtime.tools._tools["time.in_zone"]
            call = ProviderToolCall(
                name="time.in_zone",
                arguments={"timezone": "Europe/Helsinki"},
                source="inproc",
            )
            try:
                with mock.patch.object(spec, "handler", spy_handler):
                    # This is a controlled boundary decision at the composition
                    # seam, not a policy-semantics change: the default adapter
                    # decides allowed composition, and the executor calls `step`
                    # before the handler. A denial must keep the handler at zero.
                    denial = _DenyingCompositionBoundary()
                    denied_context = _execution_context()
                    denied_context.blast_radius_adapter = denial
                    with self.assertRaises(ToolRuntimeError) as raised:
                        runtime.tools.execute_calls([call], context=denied_context)
                    self.assertEqual(raised.exception.code, "CONFIRM_REQUIRED")
                    self.assertEqual(denial.seen, ["time.in_zone"])
                    self.assertEqual(calls, [])

                    allow = build_default_composition_boundary_adapter(
                        seam_id=SEAM_API_TOOLS
                    )
                    allowed_context = _execution_context()
                    allowed_context.blast_radius_adapter = allow
                    allowed = runtime.tools.execute_calls(
                        [call], context=allowed_context
                    )
                result = allowed.results[0]
                self.assertTrue(result.ok, result.data)
                self.assertEqual(calls, ["ran"])
                self.assertEqual(
                    [profile.tool_name for profile in allow.prior_profiles],
                    ["time.in_zone"],
                )
            finally:
                runtime.close()

    def test_declaration_candidates_registered_specs_and_schema_agree(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runtime = APIRuntime.from_config_path(str(_config(tmp)))
            try:
                bindings = REGISTRAR.get_manifest(None).runtime_bindings
                self.assertEqual(len(bindings), 1)
                self.assertEqual(
                    tuple(bindings[0].runtime_candidates), TIME_RUNTIME_TOOL_NAMES
                )
                self.assertEqual(TIME_RUNTIME_TOOL_NAMES[0], "time.now")
                self.assertEqual(
                    tuple(spec.name for spec in _time_tool_specs()),
                    TIME_RUNTIME_TOOL_NAMES,
                )

                manager = runtime.tools._binding_manager()
                self.assertEqual(
                    manager.runtime_candidates(RUNTIME_TIME_NOW),
                    TIME_RUNTIME_TOOL_NAMES,
                )
                for name in TIME_RUNTIME_TOOL_NAMES:
                    with self.subTest(tool=name):
                        self.assertEqual(manager.normalize_raw_name(name), "time")
                        spec = runtime.tools._provider_spec_for_runtime_name(name)
                        self.assertIsNotNone(spec)
                        self.assertEqual(spec.name, name)

                model_time = next(
                    spec
                    for spec in runtime.tools.model_provider_specs()
                    if spec.name == "time"
                )
                self.assertEqual(
                    model_time.parameters,
                    runtime.tools._provider_spec_for_runtime_name(
                        "time.now"
                    ).parameters,
                )
            finally:
                runtime.close()
