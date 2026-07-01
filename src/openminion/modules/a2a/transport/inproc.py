from openminion.modules.a2a.models import Envelope, JobRecord
from openminion.modules.a2a.runtime import A2ARuntime
from openminion.modules.a2a.transport.base import Transport


class InProcTransport(Transport):
    def __init__(self, runtime: A2ARuntime) -> None:
        self.runtime = runtime

    def call(self, envelope: Envelope) -> Envelope:
        return self.runtime.call(envelope)

    def job_start(self, envelope: Envelope) -> str:
        return self.runtime.job_start(envelope)

    def job_status(self, task_id: str) -> JobRecord:
        return self.runtime.job_status(task_id)

    def job_cancel(self, task_id: str) -> JobRecord:
        return self.runtime.job_cancel(task_id)
