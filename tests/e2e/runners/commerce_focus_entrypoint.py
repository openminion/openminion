from __future__ import annotations

import json
import os
from pathlib import Path

from tests.helpers.runtime_roots import isolate_runtime_roots

if not os.environ.get("OPENMINION_HOME"):
    isolate_runtime_roots(prefix="openminion-commerce-focus-entrypoint-")

from openminion.base.config.env import EnvironmentConfig
from openminion.cli.commands.interactive import run_interactive
from openminion.cli.main import _prepare_runtime_roots
from openminion.cli.parser.base import build_parser
from openminion.modules.commerce.models import CommerceLifecycleState
from openminion.api.queries.cron import resolve_cron_store
from openminion.services.runtime.cron.executor import CronTurnExecutor
from openminion.tools.task.constants import WATCH_PAYLOAD_KEY
from tests.helpers.commerce_runtime import build_fixture_api_runtime


def _phase() -> int:
    value = int(os.environ.get("OPENMINION_COMMERCE_TEST_PHASE", "5"))
    if value not in {0, 2, 3, 5}:
        raise ValueError("OPENMINION_COMMERCE_TEST_PHASE must be 0, 2, 3, or 5")
    return value


def _write_oracle(runtime, phase: int) -> None:
    path_value = os.environ.get("OPENMINION_COMMERCE_ORACLE_PATH", "").strip()
    if not path_value or runtime is None:
        return
    provider = runtime._commerce_fixture_provider
    payload = {
        "schema_version": "commerce-focus-oracle-v1",
        "phase": phase,
        "inventory": sorted(
            name for name in runtime.tools.list() if name.startswith("commerce.")
        ),
        "fixture_ledger": [item.__dict__ for item in provider.ledger],
        "task_runs": getattr(runtime, "_commerce_task_runs", []),
    }
    path = Path(path_value)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
    temporary.replace(path)


def _configure_scenario(runtime) -> None:
    scenario = os.environ.get("OPENMINION_COMMERCE_TEST_SCENARIO", "inventory")
    provider = runtime._commerce_fixture_provider
    if scenario == "inventory":
        return
    if scenario == "order-care":
        provider.set_next_action_state("pending")
        return
    if scenario == "unsupported-action":
        preparation = runtime.commerce_runtime.prepare_public(
            {
                "items": [
                    {
                        "offer_id": "offer-1",
                        "variant_id": "standard",
                        "quantity": 1,
                    }
                ]
            }
        )
        runtime.commerce_runtime.place_public(
            {
                "preparation_ref": preparation.preparation_ref,
                "preparation": preparation.model_dump(mode="json"),
                "preparation_digest": preparation.preparation_digest,
            },
            authorization_hash="f" * 64,
        )
        provider.set_next_action_handoff("unsupported_action")
        return
    raise ValueError(f"unknown commerce Focus scenario: {scenario}")


def _exercise_order_watch(runtime) -> list[dict[str, object]]:
    if os.environ.get("OPENMINION_COMMERCE_TEST_SCENARIO") != "order-care":
        return []
    store = resolve_cron_store(runtime)
    try:
        jobs = store.list_cron_jobs(limit=10)
        matching = []
        for job in jobs:
            routine = (
                (job.get("payload") or {}).get(WATCH_PAYLOAD_KEY, {}).get("routine")
            )
            if (
                isinstance(routine, dict)
                and routine.get("routine_kind") == "commerce_order"
            ):
                matching.append(job)
        if len(matching) != 1:
            raise RuntimeError("commerce Focus expected one commerce_order watch")
        runtime._commerce_fixture_provider.set_order_inspection_lifecycles(
            (
                CommerceLifecycleState(
                    order="accepted",
                    fulfillment="unfulfilled",
                    payment="authorized",
                    shipments={"shipment-1": "label_created"},
                ),
                CommerceLifecycleState(
                    order="accepted",
                    fulfillment="unfulfilled",
                    payment="authorized",
                    shipments={"shipment-1": "label_created"},
                ),
                CommerceLifecycleState(
                    order="accepted",
                    fulfillment="partial",
                    payment="authorized",
                    shipments={"shipment-1": "in_transit"},
                ),
            )
        )
        executor = CronTurnExecutor(
            runtime=runtime,
            cron_store=store,
            request_builder=lambda payload, agent_id: (payload, agent_id),
            timeout_s=10,
            max_attempts=1,
        )
        task_id = str(matching[0]["job_id"])
        task_runs = []
        for index in range(3):
            job = store.get_cron_job(task_id)
            result = executor.execute(job, {"run_id": f"commerce-focus-{index + 1}"})
            persisted = store.get_cron_job(task_id)
            routine = persisted["payload"][WATCH_PAYLOAD_KEY]["routine"]
            task_runs.append(
                {
                    "task_id": task_id,
                    "material_cursor": routine["cursor"]["material_cursor"],
                    "delivery_requested": result["output"]["watch_delivery_requested"],
                }
            )
        return task_runs
    finally:
        store.close()


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    if getattr(args, "command", None):
        parser.error("commerce Focus fixture accepts only a bare interactive launch")
    if bool(getattr(args, "allow_unsandboxed_exec", False)):
        from openminion.services.runtime.env import apply_runtime_environment
        from openminion.tools.exec.constants import EXEC_ENABLE_HOST_EXEC_ENV

        apply_runtime_environment({EXEC_ENABLE_HOST_EXEC_ENV: "1"}, overwrite=True)
    home_root, data_root, _, _, _ = _prepare_runtime_roots(
        args,
        EnvironmentConfig.from_sources(),
    )
    phase = _phase()
    holder: dict[str, object] = {}

    def factory(config_path, **kwargs):
        runtime = build_fixture_api_runtime(
            config_path,
            phase=phase,
            exposure_session_id=str(getattr(args, "session", "") or ""),
            **kwargs,
        )
        _configure_scenario(runtime)
        original_close = runtime.close

        def close_with_task_evidence() -> None:
            runtime._commerce_task_runs = _exercise_order_watch(runtime)
            original_close()

        runtime.close = close_with_task_evidence
        holder["runtime"] = runtime
        return runtime

    args.home_root = home_root or None
    args.data_root = data_root or None
    args.onboarding_checked = True
    try:
        return run_interactive(args, runtime_factory=factory)
    finally:
        _write_oracle(holder.get("runtime"), phase)


if __name__ == "__main__":
    raise SystemExit(main())
