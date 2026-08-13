from openminion.tools.ops.specialized import make_handler

from .args import AnsibleCheckArgs, ConfigTargetArgs, SaltJobArgs, SaltTestArgs

_h_ansible_check = make_handler("config_mgmt", "ansible_check", AnsibleCheckArgs)
_h_ansible_facts = make_handler("config_mgmt", "ansible_facts", ConfigTargetArgs)
_h_salt_test = make_handler("config_mgmt", "salt_test", SaltTestArgs)
_h_salt_job_status = make_handler("config_mgmt", "salt_job_status", SaltJobArgs)
