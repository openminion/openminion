from openminion.tools.ops.specialized import make_handler

from .args import GitOpsAppArgs

_h_app_status = make_handler("gitops", "app_status", GitOpsAppArgs)
_h_app_diff = make_handler("gitops", "app_diff", GitOpsAppArgs)
_h_source_revision = make_handler("gitops", "source_revision", GitOpsAppArgs)
_h_drift_inspect = make_handler("gitops", "drift_inspect", GitOpsAppArgs)
