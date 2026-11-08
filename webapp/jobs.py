"""Background calculation jobs with progress, cancellation and per-visitor ownership."""
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
import logging
import secrets
import threading
import time

from bclc.control import Cancelled

log = logging.getLogger("bclc.web.jobs")
ACTIVE = ("queued", "running")


@dataclass
class Job:
    id: str
    owner: str
    kind: str
    created: float
    status: str = "queued"
    progress: float = 0.0
    stage: str = "Waiting for a free worker"
    result: dict | None = None
    error: str | None = None
    finished: float | None = None
    cancel_event: threading.Event = field(default_factory=threading.Event)

    def update(self, fraction: float | None = None, stage: str | None = None) -> None:
        if fraction is not None:
            self.progress = max(0.0, min(1.0, float(fraction)))
        if stage is not None:
            self.stage = stage

    def should_stop(self) -> bool:
        return self.cancel_event.is_set()

    def public(self) -> dict:
        return {"id": self.id, "kind": self.kind, "status": self.status, "progress": round(self.progress, 4),
                "stage": self.stage, "error": self.error,
                "result": self.result if self.status == "done" else None}


class JobManager:
    def __init__(self, workers: int = 2, per_owner: int = 2, max_active: int = 20, keep_seconds: int = 3600):
        self.executor = ThreadPoolExecutor(max_workers=workers, thread_name_prefix="bclc-job")
        self.per_owner = per_owner
        self.max_active = max_active
        self.keep = keep_seconds
        self._jobs: dict[str, Job] = {}
        self._lock = threading.Lock()

    def submit(self, owner: str, kind: str, work) -> Job:
        """work(job) returns a JSON-ready dict. ValueError messages are shown to the visitor."""
        self.purge()
        with self._lock:
            active = [j for j in self._jobs.values() if j.status in ACTIVE]
            if sum(j.owner == owner for j in active) >= self.per_owner:
                raise OverflowError("You already have calculations running. Wait for one to finish or cancel it.")
            if len(active) >= self.max_active:
                raise OverflowError("The server is busy. Please try again in a minute.")
            job = Job(secrets.token_urlsafe(16), owner, kind, time.time())
            self._jobs[job.id] = job
        self.executor.submit(self._run, job, work)
        return job

    def _run(self, job: Job, work) -> None:
        if job.should_stop():
            self._finish(job, "cancelled", stage="Cancelled before it started")
            return
        job.status, job.stage = "running", "Starting"
        try:
            result = work(job)
        except Cancelled:
            self._finish(job, "cancelled", stage="Cancelled. No partial result is shown.")
        except (ValueError, OverflowError) as exc:
            self._finish(job, "error", error=str(exc), stage="Stopped with an input problem")
        except Exception:  # pragma: no cover - unexpected bugs are logged, not shown in detail
            log.exception("Job %s (%s) failed", job.id, job.kind)
            self._finish(job, "error", error="Unexpected server error. Please try again.", stage="Failed")
        else:
            job.result = result
            job.update(1.0)
            self._finish(job, "done", stage="Finished")

    @staticmethod
    def _finish(job: Job, status: str, error: str | None = None, stage: str | None = None) -> None:
        job.status, job.error, job.finished = status, error, time.time()
        if stage:
            job.stage = stage

    def get(self, owner: str, job_id: str) -> Job | None:
        with self._lock:
            job = self._jobs.get(job_id)
        return job if job and job.owner == owner else None

    def cancel(self, owner: str, job_id: str) -> Job | None:
        job = self.get(owner, job_id)
        if job and job.status in ACTIVE:
            job.cancel_event.set()
            job.stage = "Cancelling"
        return job

    def drop_owner(self, owner: str) -> None:
        with self._lock:
            for job in [j for j in self._jobs.values() if j.owner == owner]:
                job.cancel_event.set()
                del self._jobs[job.id]

    def purge(self) -> None:
        now = time.time()
        with self._lock:
            for job_id in [j.id for j in self._jobs.values()
                           if j.finished and now - j.finished > self.keep]:
                del self._jobs[job_id]

    def shutdown(self) -> None:
        with self._lock:
            for job in self._jobs.values():
                job.cancel_event.set()
        self.executor.shutdown(wait=False, cancel_futures=True)
