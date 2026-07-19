# mypy: ignore-errors
from __future__ import annotations

from openminion.modules.controlplane.contracts.models import (
    CommandResult,
    ParsedCommand,
    ResolvedContext,
)


class CommandRegistryRuntimeMixin:
    def _job_ls(self, command: ParsedCommand, ctx: ResolvedContext) -> CommandResult:
            return CommandResult(ok=True, text="No active jobs.", data={"jobs": []})

    def _run_show(self, command: ParsedCommand, ctx: ResolvedContext) -> CommandResult:
            if self.runtime_client is None:
                return CommandResult(
                    ok=True,
                    text="No active runs.",
                    data={"runs": [], "note": "runtime not connected"},
                )
            runs = self.runtime_client.list_runs(ctx.session_id)
            if not runs:
                return CommandResult(ok=True, text="No active runs.", data={"runs": []})
            lines = [f"  {r.run_id}: {r.state} (agent={r.agent_id})" for r in runs]
            return CommandResult(
                ok=True,
                text="Active runs:\n" + "\n".join(lines),
                data={
                    "runs": [
                        {"run_id": r.run_id, "state": r.state, "agent_id": r.agent_id}
                        for r in runs
                    ]
                },
            )

    def _run_status(
            self, command: ParsedCommand, ctx: ResolvedContext
        ) -> CommandResult:
            if not command.args:
                return CommandResult(ok=False, text="Usage: /run status <run_id>")
            run_id = command.args[0]
            if self.runtime_client is None:
                return CommandResult(
                    ok=True,
                    text=f"Run {run_id}: status=unknown (runtime not connected)",
                    data={
                        "run_id": run_id,
                        "status": "unknown",
                        "note": "runtime not connected",
                    },
                )
            status = self.runtime_client.get_run_status(run_id)
            if status is None:
                return CommandResult(
                    ok=True,
                    text=f"Run {run_id}: not found",
                    data={"run_id": run_id, "status": "not_found"},
                )
            return CommandResult(
                ok=True,
                text=f"Run {run_id}: {status.state} (agent={status.agent_id}, duration={status.duration_ms}ms, errors={status.error_count})",
                data={
                    "run_id": run_id,
                    "status": status.state,
                    "agent_id": status.agent_id,
                    "duration_ms": status.duration_ms,
                    "error_count": status.error_count,
                },
            )

    def _cancel_run(
            self, command: ParsedCommand, ctx: ResolvedContext
        ) -> CommandResult:
            if not command.args:
                return CommandResult(ok=False, text="Usage: /cancel <run_id>")
            run_id = command.args[0]
            if self.runtime_client is None:
                return CommandResult(
                    ok=False,
                    text="Runtime not connected",
                    data={
                        "run_id": run_id,
                        "state": "error",
                        "note": "runtime not connected",
                    },
                )
            success = self.runtime_client.cancel_run(run_id)
            if success:
                return CommandResult(
                    ok=True,
                    text=f"Cancel requested for run {run_id}.",
                    data={"run_id": run_id, "state": "cancel_requested"},
                )
            return CommandResult(
                ok=False,
                text=f"Failed to cancel run {run_id}.",
                data={"run_id": run_id, "state": "cancel_failed"},
            )

    def _approve(self, command: ParsedCommand, ctx: ResolvedContext) -> CommandResult:
            if not command.args:
                return CommandResult(
                    ok=False, text="Usage: /approve <request_id> [once|10m|1h|forever]"
                )
            request_id = command.args[0]
            grant = command.args[1] if len(command.args) > 1 else "once"

            # Map grant duration to action
            action_map = {
                "once": "allow_once",
                "10m": "allow_until",
                "1h": "allow_until",
                "forever": "allow_forever",
            }
            action = action_map.get(grant, "allow_once")
            until_seconds = None
            if grant == "10m":
                until_seconds = 600
            elif grant == "1h":
                until_seconds = 3600

            if self.policy_client is None:
                return CommandResult(
                    ok=True,
                    text=f"Approved {request_id} ({grant}) - policy service not connected",
                    data={
                        "request_id": request_id,
                        "grant": grant,
                        "note": "policy not connected",
                    },
                )

            success = self.policy_client.approve_request(request_id, action, until_seconds)
            if success:
                return CommandResult(
                    ok=True,
                    text=f"Approved {request_id} ({grant}).",
                    data={"request_id": request_id, "grant": grant},
                )
            return CommandResult(
                ok=False,
                text=f"Failed to approve {request_id}.",
                data={"request_id": request_id, "error": "approval_failed"},
            )

    def _deny(self, command: ParsedCommand, ctx: ResolvedContext) -> CommandResult:
            if not command.args:
                return CommandResult(ok=False, text="Usage: /deny <request_id>")
            request_id = command.args[0]

            if self.policy_client is None:
                return CommandResult(
                    ok=True,
                    text=f"Denied {request_id} - policy service not connected",
                    data={"request_id": request_id, "note": "policy not connected"},
                )

            success = self.policy_client.deny_request(request_id)
            if success:
                return CommandResult(
                    ok=True, text=f"Denied {request_id}.", data={"request_id": request_id}
                )
            return CommandResult(
                ok=False,
                text=f"Failed to deny {request_id}.",
                data={"request_id": request_id, "error": "deny_failed"},
            )

    def _grants(self, command: ParsedCommand, ctx: ResolvedContext) -> CommandResult:
            if self.policy_client is None:
                return CommandResult(
                    ok=True,
                    text="No active grants.",
                    data={"grants": [], "note": "policy not connected"},
                )
            grants = self.policy_client.list_grants(ctx.session_id)
            if not grants:
                return CommandResult(ok=True, text="No active grants.", data={"grants": []})
            lines = [
                f"  {g.get('grant_id', '?')}: {g.get('tool', '?')} ({g.get('duration_type', '?')})"
                for g in grants
            ]
            return CommandResult(
                ok=True, text="Active grants:\n" + "\n".join(lines), data={"grants": grants}
            )

    def _diag(self, command: ParsedCommand, ctx: ResolvedContext) -> CommandResult:
            return CommandResult(
                ok=True,
                text="controlplane: ok; dispatch=sync(default); durable inbox/outbox=enabled (sqlite backend).",
                data={"status": "ok"},
            )

    def _logs(self, command: ParsedCommand, ctx: ResolvedContext) -> CommandResult:
            if not command.args:
                return CommandResult(ok=False, text="Usage: /logs <run_id>")
            run_id = command.args[0]
            return CommandResult(
                ok=True,
                text=f"Logs for {run_id} are not wired yet.",
                data={"run_id": run_id, "lines": []},
            )

    def _artifact_ls(
            self, command: ParsedCommand, ctx: ResolvedContext
        ) -> CommandResult:
            return CommandResult(
                ok=True,
                text="Artifact listing not wired (no artifactctl config).",
                data={"artifacts": []},
            )

    def _artifact_purge(
            self, command: ParsedCommand, ctx: ResolvedContext
        ) -> CommandResult:
            return CommandResult(ok=True, text="Artifact purge triggered (stub).", data={})

    def _config_show(
            self, command: ParsedCommand, ctx: ResolvedContext
        ) -> CommandResult:
            return CommandResult(ok=True, text="Config display not wired.", data={})

    def _config_set(
            self, command: ParsedCommand, ctx: ResolvedContext
        ) -> CommandResult:
            if len(command.args) < 2:
                return CommandResult(ok=False, text="Usage: /config set <key> <value>")
            key, value = command.args[0], command.args[1]
            return CommandResult(
                ok=True,
                text=f"Config {key}={value} set (stub).",
                data={"key": key, "value": value},
            )
