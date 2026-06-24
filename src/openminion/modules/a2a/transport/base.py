from __future__ import annotations

from typing import Protocol

from openminion.modules.a2a.models import Envelope, JobRecord


class Transport(Protocol):
    def call(self, envelope: Envelope) -> Envelope: ...

    def job_start(self, envelope: Envelope) -> str: ...

    def job_status(self, task_id: str) -> JobRecord: ...

    def job_cancel(self, task_id: str) -> JobRecord: ...
