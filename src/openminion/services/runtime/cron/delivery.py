from dataclasses import asdict, is_dataclass
from typing import Any

from openminion.base.logging import format_structured_event, get_logger
from openminion.modules.controlplane import deliver_cron_result

_CRON_LOGGER = get_logger("modules.cron")


class CronDeliveryBridge:
    """Route cron delivery results onto session/event surfaces."""

    def __init__(self, *, runtime: Any) -> None:
        self._runtime = runtime

    def deliver(
        self,
        mode: str,
        to_value: str,
        job: dict[str, Any],
        run: dict[str, Any],
        result: Any,
    ) -> None:
        result_dict = self._result_dict(result)
        origin = self._origin_from_job(job)
        resolved_to_value = str(to_value or "").strip()
        if mode == "announce" and resolved_to_value == "last":
            origin_session_id = origin.get("session_id", "")
            if origin_session_id:
                resolved_to_value = f"session:{origin_session_id}"

        def _outbound(payload: dict[str, Any]) -> None:
            if not isinstance(payload, dict):
                return
            if str(payload.get("type", "")).strip() != "cron.announce":
                return
            target = str(payload.get("target", "") or "").strip()
            session_id = ""
            if target.startswith("session:"):
                session_id = target.split(":", 1)[1].strip()
            elif target == "last":
                session_id = origin.get("session_id", "")
            if not session_id:
                _CRON_LOGGER.info(
                    format_structured_event(
                        "cron.announce.unroutable",
                        job_id=job.get("job_id"),
                        run_id=run.get("run_id"),
                        target=target,
                    )
                )
                return

            body = str(payload.get("text", "") or payload.get("summary", "")).strip()
            if not body:
                body = str(result_dict.get("summary", "") or "").strip()
            if not body:
                body = "Scheduled task completed."

            metadata: dict[str, str] = {
                "cron_announce": "true",
                "cron_job_id": str(job.get("job_id", "") or "").strip(),
                "cron_run_id": str(run.get("run_id", "") or "").strip(),
                "scheduled_for": str(run.get("due_at", "") or "").strip(),
                "source": "openminion.cron",
            }
            for key, value in origin.items():
                if value:
                    metadata[f"origin_{key}"] = value

            try:
                self._runtime.sessions.append_message(
                    session_id=session_id,
                    conversation_id=origin.get("conversation_id") or None,
                    thread_id=origin.get("thread_id") or None,
                    attach_id=origin.get("attach_id") or None,
                    role="outbound",
                    body=body,
                    metadata=metadata,
                )
                self._runtime.sessions.append_event(
                    session_id=session_id,
                    event_type="cron.announce",
                    payload={
                        "cron_job_id": metadata["cron_job_id"],
                        "cron_run_id": metadata["cron_run_id"],
                        "scheduled_for": metadata["scheduled_for"],
                        "summary": body,
                        "source": metadata["source"],
                    },
                )
            except Exception as exc:  # noqa: BLE001
                _CRON_LOGGER.warning(
                    format_structured_event(
                        "cron.announce.write_failed",
                        session_id=session_id,
                        job_id=job.get("job_id"),
                        run_id=run.get("run_id"),
                        error=exc,
                    )
                )

        deliver_cron_result(
            mode,
            resolved_to_value,
            job,
            run,
            result_dict,
            outbound=_outbound,
        )

    def _origin_from_job(self, job: dict[str, Any]) -> dict[str, str]:
        payload = job.get("payload", {})
        if not isinstance(payload, dict):
            return {}
        raw_origin = payload.get("_openminion_origin")
        if not isinstance(raw_origin, dict):
            return {}
        origin: dict[str, str] = {}
        for key in (
            "session_id",
            "channel",
            "target",
            "conversation_id",
            "thread_id",
            "attach_id",
        ):
            token = str(raw_origin.get(key, "") or "").strip()
            if token:
                origin[key] = token
        return origin

    def _result_dict(self, result: Any) -> dict[str, Any]:
        if is_dataclass(result):
            return asdict(result)
        if isinstance(result, dict):
            return dict(result)
        return {"summary": str(result or "").strip()}
