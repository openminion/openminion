from dataclasses import dataclass
from typing import Any, Callable, Dict

from openminion.modules.tool.contracts.model_ids import (
    MODEL_EXEC_CLEAR,
    MODEL_EXEC_KILL,
    MODEL_EXEC_LIST,
    MODEL_EXEC_PASTE,
    MODEL_EXEC_POLL,
    MODEL_EXEC_RUN,
    MODEL_EXEC_SEND_KEYS,
    MODEL_EXEC_SUBMIT,
)
from openminion.modules.brain.runtime.escalation import ActionRiskTier
from openminion.modules.tool.family.events import emit_family_event as emit_family_event
from openminion.modules.tool.registry import ToolRegistry, ToolSpec
from openminion.modules.tool.runtime.context import RuntimeContext

from .schemas import (
    ExecRunArgs,
    ExecRunResult as ExecRunResult,
    ProcessClearArgs,
    ProcessKillArgs,
    ProcessListArgs,
    ProcessPollArgs,
    ProcessPasteArgs,
    ProcessSendKeysArgs,
    ProcessSubmitArgs,
)

import shutil as shutil

from . import handlers as _handlers
from . import policy as _policy
from . import results as _results
from . import sessions as _sessions
from .events import (
    _DECLARED_EXEC_RISK_TIERS as _DECLARED_EXEC_RISK_TIERS,
    _declared_exec_risk_tier as _declared_exec_risk_tier,
)
from .process import resolve_shell_family as resolve_shell_family
from .results import _artifactize_output as _artifactize_output


@dataclass(frozen=True)
class ExecToolDeclaration:
    name: str
    args_model: type[Any]
    handler: Callable[[Dict[str, Any], RuntimeContext], Dict[str, Any]]
    min_scope: str
    dangerous: bool = False
    idempotent: bool = True
    block_under_readonly: bool = False
    approval_risk_tier: ActionRiskTier = "silent"


def _sync_compat_globals() -> None:
    # Tests patch the public plugin module; mirror those hooks into the owners.
    setattr(_policy, "resolve_shell_family", resolve_shell_family)
    setattr(_policy, "shutil", shutil)
    setattr(_handlers, "emit_family_event", emit_family_event)
    setattr(_results, "emit_family_event", emit_family_event)
    setattr(_sessions, "emit_family_event", emit_family_event)


def _plugin_validate_command_against_policy(command: str, ctx: RuntimeContext) -> Any:
    _sync_compat_globals()
    return _policy._validate_command_against_policy(command, ctx)


def _plugin_validate_host_allowlist(command: str, ctx: RuntimeContext) -> Any:
    _sync_compat_globals()
    return _policy._validate_host_allowlist(command, ctx)


def _plugin_h_exec_run(args: Dict[str, Any], ctx: RuntimeContext) -> Dict[str, Any]:
    _sync_compat_globals()
    return _handlers._h_exec_run(args, ctx)


def _plugin_h_process_poll(args: Dict[str, Any], ctx: RuntimeContext) -> Dict[str, Any]:
    _sync_compat_globals()
    return _handlers._h_process_poll(args, ctx)


def _plugin_h_process_send_keys(
    args: Dict[str, Any], ctx: RuntimeContext
) -> Dict[str, Any]:
    _sync_compat_globals()
    return _handlers._h_process_send_keys(args, ctx)


def _plugin_h_process_submit(
    args: Dict[str, Any], ctx: RuntimeContext
) -> Dict[str, Any]:
    _sync_compat_globals()
    return _handlers._h_process_submit(args, ctx)


def _plugin_h_process_paste(
    args: Dict[str, Any], ctx: RuntimeContext
) -> Dict[str, Any]:
    _sync_compat_globals()
    return _handlers._h_process_paste(args, ctx)


def _plugin_h_process_kill(args: Dict[str, Any], ctx: RuntimeContext) -> Dict[str, Any]:
    _sync_compat_globals()
    return _handlers._h_process_kill(args, ctx)


def _plugin_h_process_clear(
    args: Dict[str, Any], ctx: RuntimeContext
) -> Dict[str, Any]:
    _sync_compat_globals()
    return _handlers._h_process_clear(args, ctx)


def _plugin_h_process_list(args: Dict[str, Any], ctx: RuntimeContext) -> Dict[str, Any]:
    _sync_compat_globals()
    return _handlers._h_process_list(args, ctx)


_validate_command_against_policy = _plugin_validate_command_against_policy
_validate_host_allowlist = _plugin_validate_host_allowlist
_h_exec_run = _plugin_h_exec_run
_h_process_poll = _plugin_h_process_poll
_h_process_send_keys = _plugin_h_process_send_keys
_h_process_submit = _plugin_h_process_submit
_h_process_paste = _plugin_h_process_paste
_h_process_kill = _plugin_h_process_kill
_h_process_clear = _plugin_h_process_clear
_h_process_list = _plugin_h_process_list


def _register_tool(
    registry: ToolRegistry,
    *,
    name: str,
    args_model: type[Any],
    handler: Callable[[Dict[str, Any], RuntimeContext], Dict[str, Any]],
    min_scope: str,
    dangerous: bool = False,
    idempotent: bool = True,
    block_under_readonly: bool = False,
) -> None:
    registry.add(
        ToolSpec(
            name=name,
            args_model=args_model,
            min_scope=min_scope,  # type: ignore[arg-type]
            handler=handler,
            dangerous=dangerous,
            idempotent=idempotent,
            tags=("plugin", "exec"),
            capabilities=("exec", "process", "pty"),
            block_under_readonly=block_under_readonly,
        )
    )


def register(registry: ToolRegistry) -> None:
    declarations = (
        ExecToolDeclaration(
            name=MODEL_EXEC_RUN,
            args_model=ExecRunArgs,
            handler=_h_exec_run,
            min_scope="WRITE_SAFE",
            dangerous=True,
            idempotent=False,
            block_under_readonly=True,
            approval_risk_tier=_DECLARED_EXEC_RISK_TIERS[MODEL_EXEC_RUN],
        ),
        ExecToolDeclaration(
            name=MODEL_EXEC_POLL,
            args_model=ProcessPollArgs,
            handler=_h_process_poll,
            min_scope="READ_ONLY",
            approval_risk_tier=_DECLARED_EXEC_RISK_TIERS[MODEL_EXEC_POLL],
        ),
        ExecToolDeclaration(
            name=MODEL_EXEC_SEND_KEYS,
            args_model=ProcessSendKeysArgs,
            handler=_h_process_send_keys,
            min_scope="WRITE_SAFE",
            idempotent=False,
            block_under_readonly=True,
            approval_risk_tier=_DECLARED_EXEC_RISK_TIERS[MODEL_EXEC_SEND_KEYS],
        ),
        ExecToolDeclaration(
            name=MODEL_EXEC_SUBMIT,
            args_model=ProcessSubmitArgs,
            handler=_h_process_submit,
            min_scope="WRITE_SAFE",
            idempotent=False,
            block_under_readonly=True,
            approval_risk_tier=_DECLARED_EXEC_RISK_TIERS[MODEL_EXEC_SUBMIT],
        ),
        ExecToolDeclaration(
            name=MODEL_EXEC_PASTE,
            args_model=ProcessPasteArgs,
            handler=_h_process_paste,
            min_scope="WRITE_SAFE",
            idempotent=False,
            block_under_readonly=True,
            approval_risk_tier=_DECLARED_EXEC_RISK_TIERS[MODEL_EXEC_PASTE],
        ),
        ExecToolDeclaration(
            name=MODEL_EXEC_KILL,
            args_model=ProcessKillArgs,
            handler=_h_process_kill,
            min_scope="WRITE_SAFE",
            dangerous=True,
            idempotent=False,
            block_under_readonly=True,
            approval_risk_tier=_DECLARED_EXEC_RISK_TIERS[MODEL_EXEC_KILL],
        ),
        ExecToolDeclaration(
            name=MODEL_EXEC_CLEAR,
            args_model=ProcessClearArgs,
            handler=_h_process_clear,
            min_scope="WRITE_SAFE",
            idempotent=False,
            approval_risk_tier=_DECLARED_EXEC_RISK_TIERS[MODEL_EXEC_CLEAR],
        ),
        ExecToolDeclaration(
            name=MODEL_EXEC_LIST,
            args_model=ProcessListArgs,
            handler=_h_process_list,
            min_scope="READ_ONLY",
            approval_risk_tier=_DECLARED_EXEC_RISK_TIERS[MODEL_EXEC_LIST],
        ),
    )
    for declaration in declarations:
        _register_tool(
            registry,
            name=declaration.name,
            args_model=declaration.args_model,
            handler=declaration.handler,
            min_scope=declaration.min_scope,
            dangerous=declaration.dangerous,
            idempotent=declaration.idempotent,
            block_under_readonly=declaration.block_under_readonly,
        )
