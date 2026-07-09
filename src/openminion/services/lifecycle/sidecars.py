import json
import logging
import os
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, List, Mapping, Protocol, Sequence, cast

from openminion.services.config import (
    resolve_services_env,
    resolve_services_path,
    resolve_services_roots,
)
from openminion.services.bootstrap.paths import (
    SERVICES_STATE_DIRNAME,
    SERVICES_TOOL_RUNTIME_SUBDIR,
)
from openminion.services.security.policy import (
    DECISION_ALLOW,
    DECISION_REQUIRE_APPROVAL,
    RISK_LOW,
    RISK_MEDIUM,
    SecurityPolicyAction,
    SecurityPolicyActor,
    SecurityPolicyCheck,
    SecurityPolicyContext,
    SecurityPolicyDecision,
    SecurityPolicyEngine,
    default_internal_actor,
)

from openminion.base.time import utc_now_iso as _iso_now


_PINCHTAB_PROMPT = (
    "OpenMinion can start the PinchTab browser service locally when needed.\n"
    "This launches a background process on your machine.\n"
    "Allow auto-start for PinchTab? [y/N]: "
)

_POLICY_PROMPT = (
    "Sidecar action requires approval.\nAllow {verb} for sidecar '{name}'? [y/N]: "
)


def _truthy(value: str | None) -> bool:
    raw = str(value or "").strip().lower()
    if not raw:
        return False
    return raw in {"1", "true", "yes", "on", "allow"}


def _parse_env_pairs(raw: str | None) -> dict[str, str]:
    if raw is None:
        return {}
    cleaned = str(raw).strip()
    if not cleaned:
        return {}
    env: dict[str, str] = {}
    for chunk in cleaned.split(","):
        chunk = chunk.strip()
        if not chunk or "=" not in chunk:
            continue
        key, value = chunk.split("=", 1)
        key = key.strip()
        if key:
            env[key] = value.strip()
    return env


@dataclass(frozen=True)
class SidecarConsent:
    name: str
    approved: bool
    approved_at: str
    scope: str


class SidecarConsentStore:
    def __init__(self, path: Path) -> None:
        self._path = path

    @property
    def path(self) -> Path:
        return self._path

    def load(self) -> dict[str, Any]:
        try:
            raw = self._path.read_text(encoding="utf-8").strip()
        except OSError:
            return {}
        if not raw:
            return {}
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            return {}
        return payload if isinstance(payload, dict) else {}

    def save(self, payload: Mapping[str, Any]) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text(
            json.dumps(dict(payload), indent=2, sort_keys=True),
            encoding="utf-8",
        )

    def get(self, name: str) -> SidecarConsent | None:
        payload = self.load()
        record = payload.get(name)
        if not isinstance(record, dict):
            return None
        approved = bool(record.get("approved", False))
        approved_at = str(record.get("approved_at", "") or "")
        scope = str(record.get("scope", "") or "persistent")
        return SidecarConsent(
            name=name, approved=approved, approved_at=approved_at, scope=scope
        )

    def set(self, consent: SidecarConsent) -> None:
        payload = self.load()
        payload[consent.name] = {
            "approved": bool(consent.approved),
            "approved_at": consent.approved_at,
            "scope": consent.scope,
        }
        self.save(payload)


def _consent_store(config_path: str | None) -> SidecarConsentStore:
    roots = resolve_services_roots(config_path=config_path)
    return SidecarConsentStore(
        resolve_services_path(
            Path(SERVICES_STATE_DIRNAME) / "sidecar-consent.json",
            roots=roots,
        )
    )


class SidecarExecutor(Protocol):
    def run(
        self,
        command: Sequence[str],
        *,
        env: Mapping[str, str] | None = None,
        cwd: Path | None = None,
        log_path: Path | None = None,
    ) -> dict[str, Any]: ...


class SubprocessExecutor:
    def run(
        self,
        command: Sequence[str],
        *,
        env: Mapping[str, str] | None = None,
        cwd: Path | None = None,
        log_path: Path | None = None,
    ) -> dict[str, Any]:
        merged_env = dict(os.environ)
        merged_env.update({str(k): str(v) for k, v in (env or {}).items()})
        stdout = None
        stderr = None
        if log_path is not None:
            log_path.parent.mkdir(parents=True, exist_ok=True)
            handle = log_path.open("a", encoding="utf-8")
            stdout = handle
            stderr = handle
        proc = subprocess.Popen(  # noqa: S603
            list(command),
            cwd=str(cwd) if cwd else None,
            env=merged_env,
            stdout=stdout,
            stderr=stderr,
            start_new_session=True,
        )
        return {"pid": proc.pid, "command": list(command)}


class ToolExecExecutor:
    """Optional executor that delegates to a tool-runtime callable."""

    def __init__(self, invoke: Callable[..., Any]) -> None:
        self._invoke = invoke

    def run(
        self,
        command: Sequence[str],
        *,
        env: Mapping[str, str] | None = None,
        cwd: Path | None = None,
        log_path: Path | None = None,
    ) -> dict[str, Any]:
        return cast(
            dict[str, Any],
            self._invoke(
                command=list(command),
                env=dict(env or {}),
                cwd=str(cwd) if cwd else None,
                log_path=str(log_path) if log_path else None,
            ),
        )


class SidecarAdapter(Protocol):
    def status(self) -> dict[str, Any]: ...
    def start(self) -> dict[str, Any]: ...
    def stop(self, *, kill: bool = False) -> dict[str, Any]: ...


@dataclass(frozen=True)
class SidecarSpec:
    name: str
    description: str
    autostart_env_key: str
    prompt: str
    adapter: SidecarAdapter


class SidecarManager:
    def __init__(
        self,
        *,
        specs: Sequence[SidecarSpec],
        config_path: str | None,
        runtime_env: Mapping[str, str] | None = None,
        policy: SecurityPolicyEngine | None = None,
        actor: SecurityPolicyActor | None = None,
        context: SecurityPolicyContext | None = None,
        event_sink: Callable[[str, dict[str, Any]], None] | None = None,
        logger: logging.Logger | None = None,
    ) -> None:
        self._specs = {spec.name: spec for spec in specs}
        self._config_path = config_path
        self._runtime_env = dict(runtime_env or {})
        self._process_env = os.environ
        self._logger = logger or logging.getLogger("openminion.sidecars")
        self._store = _consent_store(config_path)
        self._policy = policy
        self._actor = actor
        self._context = context or SecurityPolicyContext()
        self._event_sink = event_sink

    def list(self) -> list[str]:
        return sorted(self._specs.keys())

    def specs(self) -> List[SidecarSpec]:
        return [self._specs[name] for name in self.list()]

    def consent(self, name: str) -> SidecarConsent | None:
        return self._store.get(name)

    def approve(self, name: str, *, scope: str = "persistent") -> SidecarConsent:
        consent = SidecarConsent(
            name=name,
            approved=True,
            approved_at=_iso_now(),
            scope=scope,
        )
        self._store.set(consent)
        return consent

    def deny(self, name: str, *, scope: str = "denied") -> SidecarConsent:
        consent = SidecarConsent(
            name=name,
            approved=False,
            approved_at=_iso_now(),
            scope=scope,
        )
        self._store.set(consent)
        return consent

    def status(self, name: str) -> dict[str, Any]:
        spec = self._specs[name]
        status = spec.adapter.status()
        status["sidecar"] = name
        return status

    def ensure_autostart(
        self,
        *,
        name: str,
        interactive: bool,
        prompt_fn: Callable[[str], str] | None = None,
    ) -> dict[str, Any]:
        spec = self._specs[name]
        prompt_fn = prompt_fn or input

        env_val = self._process_env.get(spec.autostart_env_key)
        if _truthy(env_val):
            return {"enabled": True, "source": "env", "value": env_val}

        cfg_val = self._runtime_env.get(spec.autostart_env_key)
        if _truthy(cfg_val):
            self._process_env.update({spec.autostart_env_key: str(cfg_val)})
            return {"enabled": True, "source": "config.runtime.env", "value": cfg_val}

        consent = self._store.get(spec.name)
        if consent and consent.approved:
            self._process_env.update({spec.autostart_env_key: "1"})
            return {
                "enabled": True,
                "source": "consent_store",
                "approved_at": consent.approved_at,
            }

        if not interactive:
            return {"enabled": False, "reason": "non_interactive"}

        try:
            answer = prompt_fn(spec.prompt)
        except Exception as exc:  # noqa: BLE001
            self._logger.debug("sidecar prompt failed: %s", exc)
            return {"enabled": False, "reason": "prompt_failed"}

        if str(answer or "").strip().lower() not in {"y", "yes"}:
            self._store.set(
                SidecarConsent(
                    name=spec.name,
                    approved=False,
                    approved_at=_iso_now(),
                    scope="denied",
                )
            )
            return {"enabled": False, "reason": "declined"}

        self._store.set(
            SidecarConsent(
                name=spec.name,
                approved=True,
                approved_at=_iso_now(),
                scope="persistent",
            )
        )
        self._process_env.update({spec.autostart_env_key: "1"})
        return {"enabled": True, "source": "prompt"}

    def ensure_started(
        self,
        *,
        name: str,
        interactive: bool,
        prompt_fn: Callable[[str], str] | None = None,
    ) -> dict[str, Any]:
        policy = self._authorize(
            name=name,
            verb="start",
            risk=RISK_MEDIUM,
            interactive=interactive,
            prompt_fn=prompt_fn,
        )
        if not policy.get("allowed", False):
            self._emit_event(
                "sidecar.start.blocked",
                {"sidecar": name, "policy": policy},
            )
            return {"started": False, "policy": policy}
        autostart = self.ensure_autostart(
            name=name, interactive=interactive, prompt_fn=prompt_fn
        )
        if not autostart.get("enabled"):
            return {"started": False, "autostart": autostart}
        status = self.status(name)
        if bool(status.get("pid_alive")) or bool(status.get("ok")):
            return {"started": False, "status": status, "autostart": autostart}
        result = self._specs[name].adapter.start()
        self._emit_event(
            "sidecar.start.completed",
            {"sidecar": name, "result": result, "autostart": autostart},
        )
        return {"started": True, "status": result, "autostart": autostart}

    def stop(self, *, name: str, kill: bool = False) -> dict[str, Any]:
        policy = self._authorize(
            name=name,
            verb="stop",
            risk=RISK_LOW,
            interactive=False,
            prompt_fn=None,
        )
        if not policy.get("allowed", False):
            self._emit_event(
                "sidecar.stop.blocked",
                {"sidecar": name, "policy": policy},
            )
            return {"stopped": False, "policy": policy}
        spec = self._specs[name]
        result = spec.adapter.stop(kill=kill)
        self._emit_event(
            "sidecar.stop.completed",
            {"sidecar": name, "result": result},
        )
        return result

    def _emit_event(self, event: str, payload: dict[str, Any]) -> None:
        if self._event_sink is not None:
            self._event_sink(event, dict(payload))
        self._logger.info("sidecar event=%s payload=%s", event, payload)

    def _authorize(
        self,
        *,
        name: str,
        verb: str,
        risk: str,
        interactive: bool,
        prompt_fn: Callable[[str], str] | None,
    ) -> dict[str, Any]:
        if self._policy is None:
            return {"allowed": True, "decision": DECISION_ALLOW, "policy_version": None}

        actor = self._actor or default_internal_actor(
            agent_id="openminion", include_admin=False
        )
        action = SecurityPolicyAction(
            resource="sidecar", verb=verb, risk=risk, tool_name=name
        )
        decision = self._policy.evaluate(
            SecurityPolicyCheck(actor=actor, action=action, context=self._context)
        )
        payload = _decision_payload(decision)
        self._emit_event(
            "sidecar.policy.checked",
            {"sidecar": name, "verb": verb, "decision": payload},
        )
        if decision.decision == DECISION_ALLOW:
            return {"allowed": True, **payload}
        if decision.decision == DECISION_REQUIRE_APPROVAL:
            if not interactive:
                return {"allowed": False, **payload, "reason": "approval_required"}
            prompt_fn = prompt_fn or input
            try:
                answer = prompt_fn(_POLICY_PROMPT.format(verb=verb, name=name))
            except Exception as exc:  # noqa: BLE001
                self._logger.debug("policy prompt failed: %s", exc)
                return {"allowed": False, **payload, "reason": "prompt_failed"}
            if str(answer or "").strip().lower() in {"y", "yes"}:
                return {"allowed": True, **payload, "approved_via": "prompt"}
            return {"allowed": False, **payload, "reason": "declined"}
        return {"allowed": False, **payload}


class PinchTabSidecarAdapter:
    def __init__(
        self,
        *,
        config_path: str | None,
        runtime_env: Mapping[str, str] | None,
        logger: logging.Logger | None = None,
    ) -> None:
        self._config_path = config_path
        self._runtime_env = dict(runtime_env or {})
        self._logger = logger or logging.getLogger("openminion.sidecars")

    def _env(self, key: str, default: str = "") -> str:
        if key in self._runtime_env and str(self._runtime_env[key]).strip():
            return str(self._runtime_env[key]).strip()
        return resolve_services_env().get(key, default).strip()

    def _daemon_config(self) -> Any:
        from openminion.tools.browser.providers.pinchtab.daemon import (
            build_daemon_config,
        )

        base_url = self._env("PINCHTAB_URL", "http://127.0.0.1:9867")
        roots = resolve_services_roots(
            config_path=self._config_path,
            runtime_env=self._runtime_env,
        )
        runtime_dir = resolve_services_path(
            Path(SERVICES_TOOL_RUNTIME_SUBDIR) / "pinchtab",
            roots=roots,
        )

        launch_cmd = self._env("PINCHTAB_LAUNCH_CMD", "")
        launch_timeout = int(self._env("PINCHTAB_LAUNCH_TIMEOUT_SECONDS", "20") or "20")
        launch_env = _parse_env_pairs(self._env("PINCHTAB_LAUNCH_ENV", ""))
        return build_daemon_config(
            base_url=base_url,
            runtime_dir=runtime_dir,
            launch_cmd=launch_cmd or None,
            launch_timeout_s=launch_timeout,
            env=launch_env,
        )

    def status(self) -> dict[str, Any]:
        from openminion.tools.browser.providers.pinchtab.daemon import daemon_status

        cfg = self._daemon_config()
        return daemon_status(cfg)

    def start(self) -> dict[str, Any]:
        from openminion.tools.browser.providers.pinchtab.client import (
            PinchTabClient,
            RetryPolicy,
        )
        from openminion.tools.browser.providers.pinchtab.daemon import ensure_daemon

        cfg = self._daemon_config()
        token = self._env("PINCHTAB_TOKEN", "")
        timeout_seconds = int(self._env("PINCHTAB_TIMEOUT_SECONDS", "30") or "30")
        max_retries = int(self._env("PINCHTAB_MAX_RETRIES", "2") or "2")
        backoff_ms = int(self._env("PINCHTAB_RETRY_BACKOFF_MS", "250") or "250")
        return ensure_daemon(
            cfg,
            check_fn=lambda: PinchTabClient(
                base_url=cfg.base_url,
                token=str(token) if token else None,
                timeout_seconds=max(1, timeout_seconds),
                retry_policy=RetryPolicy(
                    max_retries=max(1, max_retries), backoff_ms=max(0, backoff_ms)
                ),
            ).health(),
        )

    def stop(self, *, kill: bool = False) -> dict[str, Any]:
        from openminion.tools.browser.providers.pinchtab.daemon import stop_daemon

        cfg = self._daemon_config()
        return stop_daemon(cfg, kill=kill)


def default_sidecar_manager(
    *,
    config_path: str | None,
    runtime_env: Mapping[str, str] | None,
    policy: SecurityPolicyEngine | None = None,
    actor: SecurityPolicyActor | None = None,
    context: SecurityPolicyContext | None = None,
    event_sink: Callable[[str, dict[str, Any]], None] | None = None,
    logger: logging.Logger | None = None,
) -> SidecarManager:
    adapter = PinchTabSidecarAdapter(
        config_path=config_path,
        runtime_env=runtime_env,
        logger=logger,
    )
    spec = SidecarSpec(
        name="pinchtab",
        description="PinchTab browser bridge daemon",
        autostart_env_key="PINCHTAB_AUTOSTART",
        prompt=_PINCHTAB_PROMPT,
        adapter=adapter,
    )
    return SidecarManager(
        specs=[spec],
        config_path=config_path,
        runtime_env=runtime_env,
        policy=policy,
        actor=actor,
        context=context,
        event_sink=event_sink,
        logger=logger,
    )


def ensure_pinchtab_autostart(
    *,
    config_path: str | None,
    runtime_env: Mapping[str, str] | None = None,
    interactive: bool = False,
    prompt_fn: Callable[[str], str] | None = None,
    policy: SecurityPolicyEngine | None = None,
    actor: SecurityPolicyActor | None = None,
    context: SecurityPolicyContext | None = None,
    logger: logging.Logger | None = None,
) -> dict[str, Any]:
    return ensure_sidecar_autostart(
        name="pinchtab",
        config_path=config_path,
        runtime_env=runtime_env,
        interactive=interactive,
        prompt_fn=prompt_fn,
        policy=policy,
        actor=actor,
        context=context,
        logger=logger,
    )


def ensure_sidecar_autostart(
    *,
    name: str,
    config_path: str | None,
    runtime_env: Mapping[str, str] | None = None,
    interactive: bool = False,
    prompt_fn: Callable[[str], str] | None = None,
    policy: SecurityPolicyEngine | None = None,
    actor: SecurityPolicyActor | None = None,
    context: SecurityPolicyContext | None = None,
    logger: logging.Logger | None = None,
) -> dict[str, Any]:
    manager = default_sidecar_manager(
        config_path=config_path,
        runtime_env=runtime_env,
        policy=policy,
        actor=actor,
        context=context,
        logger=logger,
    )
    status = manager.status(name)
    if bool(status.get("pid_alive")) or bool(status.get("ok")):
        return {"enabled": True, "source": "status", "status": status}
    autostart = manager.ensure_autostart(
        name=name,
        interactive=interactive,
        prompt_fn=prompt_fn,
    )
    return {
        "enabled": bool(autostart.get("enabled")),
        "autostart": autostart,
        "status": status,
    }


def ensure_sidecars_autostart(
    *,
    config_path: str | None,
    runtime_env: Mapping[str, str] | None,
    interactive: bool,
    prompt_fn: Callable[[str], str] | None = None,
    policy: SecurityPolicyEngine | None = None,
    actor: SecurityPolicyActor | None = None,
    context: SecurityPolicyContext | None = None,
    logger: logging.Logger | None = None,
) -> dict[str, Any]:
    manager = default_sidecar_manager(
        config_path=config_path,
        runtime_env=runtime_env,
        policy=policy,
        actor=actor,
        context=context,
        logger=logger,
    )
    results: dict[str, Any] = {}
    for name in manager.list():
        results[name] = manager.ensure_autostart(
            name=name,
            interactive=interactive,
            prompt_fn=prompt_fn,
        )
    return results


def _decision_payload(decision: SecurityPolicyDecision) -> dict[str, Any]:
    return {
        "decision": decision.decision,
        "reason_code": decision.reason_code,
        "policy_version": decision.policy_version,
        "required_approval_level": decision.required_approval_level,
        "details": dict(decision.details),
    }
