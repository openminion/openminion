from typing import Any, cast

from openminion.tools.ops.contracts import KubernetesTarget
from openminion.tools.ops.service import OpsService
from openminion.tools.ops.specialized import make_live_handler
from openminion.tools.ops.transports import KubernetesTransport

from .args import (
    K8sEventsArgs,
    K8sLogsArgs,
    RolloutStatusArgs,
    WorkloadGetArgs,
    WorkloadListArgs,
)


def _live(operation: str):
    def handler(args: Any, ctx: Any) -> dict[str, Any]:
        service = getattr(ctx, "ops_service", None)
        if not isinstance(service, OpsService):
            raise RuntimeError("configured operations service is unavailable")
        target_id = str(args.target_id)
        target = cast(KubernetesTarget, service.inspect_target(target_id))
        transport = cast(
            KubernetesTransport,
            service.transport_for(target_id, expected_kind="kubernetes"),
        )
        return transport.inspect_resource(
            target,
            operation,
            args.model_dump(mode="json"),
        )

    return handler


_h_workload_get = make_live_handler(
    "k8s", "workload_get", WorkloadGetArgs, _live("workload_get")
)
_h_workload_list = make_live_handler(
    "k8s", "workload_list", WorkloadListArgs, _live("workload_list")
)
_h_events_list = make_live_handler(
    "k8s", "events_list", K8sEventsArgs, _live("events_list")
)
_h_logs_get = make_live_handler("k8s", "logs_get", K8sLogsArgs, _live("logs_get"))
_h_rollout_status = make_live_handler(
    "k8s", "rollout_status", RolloutStatusArgs, _live("rollout_status")
)
