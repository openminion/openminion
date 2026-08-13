from openminion.tools.ops.specialized import make_handler

from .args import IacPlanArgs, IacPlanShowArgs, IacWorkspaceArgs

_h_validate = make_handler("iac", "validate", IacWorkspaceArgs)
_h_plan_create = make_handler("iac", "plan_create", IacPlanArgs)
_h_plan_show = make_handler("iac", "plan_show", IacPlanShowArgs)
_h_provider_facts = make_handler("iac", "provider_facts", IacWorkspaceArgs)
