"""Controlplane metrics derived from canonical audit events."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable, Mapping

from .audit import AuditEvent

_ALLOWED_LABELS = frozenset({"channel", "dimension", "outcome", "status", "table"})
_FORBIDDEN_LABEL_PARTS = ("chat", "session", "user", "subject", "prompt", "token", "error")


def _labels_key(labels: Mapping[str, str] | None) -> tuple[tuple[str, str], ...]:
    labels = labels or {}
    for key, value in labels.items():
        _validate_label(key, value)
    return tuple(sorted((str(k), str(v)) for k, v in labels.items()))


def _validate_label(key: str, value: str) -> None:
    normalized = str(key).strip().lower()
    if normalized not in _ALLOWED_LABELS:
        raise ValueError(f"Unsupported controlplane metric label: {key}")
    if any(part in normalized for part in _FORBIDDEN_LABEL_PARTS):
        raise ValueError(f"Forbidden high-cardinality label: {key}")
    text = str(value)
    if len(text) > 64 or "\n" in text:
        raise ValueError(f"Unsafe controlplane metric label value for {key}")


@dataclass
class MetricsRegistry:
    counters: dict[tuple[str, tuple[tuple[str, str], ...]], float] = field(
        default_factory=lambda: defaultdict(float)
    )
    gauges: dict[tuple[str, tuple[tuple[str, str], ...]], float] = field(
        default_factory=dict
    )
    histograms: dict[tuple[str, tuple[tuple[str, str], ...]], list[float]] = field(
        default_factory=lambda: defaultdict(list)
    )

    def inc(
        self, name: str, *, labels: Mapping[str, str] | None = None, amount: float = 1.0
    ) -> None:
        self.counters[(name, _labels_key(labels))] += amount

    def set_gauge(
        self, name: str, value: float, *, labels: Mapping[str, str] | None = None
    ) -> None:
        self.gauges[(name, _labels_key(labels))] = value

    def observe(
        self, name: str, value: float, *, labels: Mapping[str, str] | None = None
    ) -> None:
        self.histograms[(name, _labels_key(labels))].append(float(value))

    def counter_value(
        self, name: str, *, labels: Mapping[str, str] | None = None
    ) -> float:
        return self.counters.get((name, _labels_key(labels)), 0.0)

    def gauge_value(
        self, name: str, *, labels: Mapping[str, str] | None = None
    ) -> float:
        return self.gauges.get((name, _labels_key(labels)), 0.0)

    def render_prometheus(self) -> bytes:
        lines: list[str] = []
        for name, labels, value in self._iter_samples(self.counters.items()):
            lines.append(f"{name}{_format_labels(labels)} {value:g}")
        for name, labels, value in self._iter_samples(self.gauges.items()):
            lines.append(f"{name}{_format_labels(labels)} {value:g}")
        for (name, labels), values in sorted(self.histograms.items()):
            lines.append(f"{name}_count{_format_labels(labels)} {len(values):g}")
            lines.append(f"{name}_sum{_format_labels(labels)} {sum(values):g}")
        return ("\n".join(lines) + ("\n" if lines else "")).encode("utf-8")

    def summary(self) -> dict[str, Any]:
        return {
            "counters": len(self.counters),
            "gauges": len(self.gauges),
            "histograms": len(self.histograms),
        }

    @staticmethod
    def _iter_samples(
        samples: Iterable[tuple[tuple[str, tuple[tuple[str, str], ...]], float]]
    ) -> list[tuple[str, tuple[tuple[str, str], ...], float]]:
        return [(name, labels, value) for (name, labels), value in sorted(samples)]


class MetricsAuditSink:
    def __init__(self, registry: MetricsRegistry) -> None:
        self.registry = registry

    def observe(self, event: AuditEvent) -> None:
        details = dict(event.details or {})
        event_type = event.event_type
        channel = _safe_detail(details, "channel", "unknown")
        if event_type in {"inbound.received", "channel.message.received"}:
            self.registry.inc(
                "controlplane_inbound_total",
                labels={"channel": channel, "outcome": "accepted"},
            )
        elif event_type == "cp.access.allow":
            self.registry.inc(
                "controlplane_inbound_total",
                labels={"channel": channel, "outcome": "accepted"},
            )
        elif event_type == "cp.access.deny":
            self.registry.inc(
                "controlplane_inbound_total",
                labels={"channel": channel, "outcome": "deny"},
            )
        elif event_type == "cp.outbox.enqueued":
            self.registry.inc(
                "controlplane_outbox_enqueued_total", labels={"channel": channel}
            )
        elif event_type.startswith("cp.delivery."):
            self.registry.inc(
                "controlplane_delivery_total",
                labels={"channel": channel, "status": event_type.rsplit(".", 1)[-1]},
            )
        elif event_type == "cp.outbox.deadletter":
            self.registry.inc(
                "controlplane_delivery_total",
                labels={"channel": channel, "status": "dead"},
            )
        elif event_type == "cp.rate_limit.exceeded":
            self.registry.inc(
                "controlplane_rate_limit_exceeded_total",
                labels={
                    "channel": channel,
                    "dimension": _safe_detail(details, "dimension", "unknown"),
                },
            )
        elif event_type.startswith("cp.pairing.token."):
            self.registry.inc(
                "controlplane_pairing_total",
                labels={"channel": channel, "outcome": event_type.rsplit(".", 1)[-1]},
            )
        elif event_type in {"cp.wizard.step.failure", "cp.wizard.step.failed"}:
            self.registry.inc("controlplane_wizard_step_failures_total")
        elif event_type == "cp.janitor.cycle.completed":
            for table, count in dict(details.get("deleted") or {}).items():
                self.registry.inc(
                    "controlplane_janitor_deleted_total",
                    labels={"table": str(table)},
                    amount=float(count),
                )

    def set_outbox_pending(self, channel: str, count: int) -> None:
        self.registry.set_gauge(
            "controlplane_outbox_pending_count",
            float(count),
            labels={"channel": str(channel or "unknown")},
        )

    def set_audit_sink_failures(self, count: int) -> None:
        self.registry.set_gauge("controlplane_audit_sink_failures", float(count))


def compose_audit_sinks(*sinks: Callable[[AuditEvent], None] | None) -> Callable[[AuditEvent], None]:
    active = [sink for sink in sinks if sink is not None]

    def emit(event: AuditEvent) -> None:
        errors: list[BaseException] = []
        for sink in active:
            try:
                sink(event)
            except (RuntimeError, ValueError, TypeError, OSError) as exc:
                errors.append(exc)
        if len(errors) == len(active) and errors:
            raise RuntimeError(f"all audit sinks failed: {errors[-1]}")

    return emit


def _safe_detail(details: Mapping[str, Any], key: str, default: str) -> str:
    raw = details.get(key, default)
    text = str(raw or default).strip().lower()
    return text[:64] or default


def _format_labels(labels: tuple[tuple[str, str], ...]) -> str:
    if not labels:
        return ""
    payload = ",".join(f'{key}="{value}"' for key, value in labels)
    return "{" + payload + "}"


__all__ = [
    "MetricsAuditSink",
    "MetricsRegistry",
    "compose_audit_sinks",
]
