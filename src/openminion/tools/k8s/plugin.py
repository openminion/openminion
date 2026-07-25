from openminion.tools.ops.specialized import make_handler

from .args import (
    WorkloadGetArgs,
    WorkloadListArgs,
    K8sEventsArgs,
    K8sLogsArgs,
    RolloutStatusArgs,
)

_h_workload_get = make_handler("k8s", "workload_get", WorkloadGetArgs)
_h_workload_list = make_handler("k8s", "workload_list", WorkloadListArgs)
_h_events_list = make_handler("k8s", "events_list", K8sEventsArgs)
_h_logs_get = make_handler("k8s", "logs_get", K8sLogsArgs)
_h_rollout_status = make_handler("k8s", "rollout_status", RolloutStatusArgs)
