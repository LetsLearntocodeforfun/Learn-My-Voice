"""Background work that takes longer than a web request: installing the transcriber, transcribing a video.

Jobs live in memory only. A job that was running when the app quit is simply gone;
nothing half-finished is written, because a job stores its result only at the end.
Jobs of the same ``lane`` run one at a time, in the order they were started, so two
videos are never transcribed at once.
"""
from __future__ import annotations

import threading
import time
import traceback
import uuid
from typing import Any, Callable, Dict, List, Optional

QUEUED = "queued"
MAX_FINISHED = 50      # finished jobs kept for the page to read their outcome

Progress = Callable[[str, Optional[float]], None]


class JobError(Exception):
    """Raised by a job to fail with a code and messages (English, and optionally Chinese) that can be shown to the creator."""

    def __init__(self, code: str, message: str = "", message_zh: str = "") -> None:
        super().__init__(message or code)
        self.code, self.message, self.message_zh = code, message or code, message_zh or ""


class Jobs:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._jobs: Dict[str, Dict[str, Any]] = {}
        self._lanes: Dict[str, threading.Lock] = {}

    @staticmethod
    def _public(job: Dict[str, Any]) -> Dict[str, Any]:
        return {key: job[key] for key in ("id", "kind", "label", "status", "stage", "fraction", "result", "error",
                                          "started_at", "finished_at")}

    def start(self, kind: str, work: Callable[[Progress], Any], *, label: str = "", lane: Optional[str] = None) -> Dict[str, Any]:
        """Run ``work(progress)`` on a thread. ``progress(stage, fraction)`` updates what the page sees."""
        job: Dict[str, Any] = {
            "id": uuid.uuid4().hex[:12], "kind": kind, "label": label, "status": "running", "stage": QUEUED,
            "fraction": None, "result": None, "error": None, "started_at": time.time(), "finished_at": None,
        }
        with self._lock:
            self._jobs[job["id"]] = job
            lane_lock = self._lanes.setdefault(lane or kind, threading.Lock())
            finished = sorted((j for j in self._jobs.values() if j["status"] != "running"), key=lambda j: j["finished_at"])
            for old in finished[:-MAX_FINISHED]:
                del self._jobs[old["id"]]

        def progress(stage: str, fraction: Optional[float] = None) -> None:
            with self._lock:
                job["stage"] = str(stage)
                job["fraction"] = None if fraction is None else max(0.0, min(1.0, float(fraction)))

        def run() -> None:
            with lane_lock:
                progress("starting")
                try:
                    result = work(progress)
                    outcome: Dict[str, Any] = {"status": "done", "result": result}
                except JobError as exc:
                    outcome = {"status": "failed", "error": {"code": exc.code, "message": exc.message, "message_zh": exc.message_zh}}
                except Exception as exc:  # noqa: BLE001 - a job must never take the server down
                    traceback.print_exc()
                    code = getattr(exc, "code", None)   # an error written to be shown, such as the transcriber's
                    outcome = {"status": "failed", "error": {
                        "code": str(code or "failed"), "message": str(exc) if code else "internal error",
                        "message_zh": str(getattr(exc, "message_zh", "") or "") if code else ""}}
                with self._lock:
                    job.update(outcome, finished_at=time.time())

        threading.Thread(target=run, daemon=True).start()
        return self._public(job)

    def get(self, job_id: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            job = self._jobs.get(job_id)
            return self._public(job) if job else None

    def latest(self, kind: str) -> Optional[Dict[str, Any]]:
        """The most recently started job of a kind, running or not."""
        with self._lock:
            jobs = [j for j in self._jobs.values() if j["kind"] == kind]
            return self._public(max(jobs, key=lambda j: j["started_at"])) if jobs else None

    def running(self, kind: str) -> List[Dict[str, Any]]:
        with self._lock:
            return [self._public(j) for j in self._jobs.values() if j["kind"] == kind and j["status"] == "running"]

    def wait(self, job_id: str, timeout: float = 30.0) -> Optional[Dict[str, Any]]:
        """Block until a job finishes (for tests)."""
        deadline = time.time() + timeout
        while time.time() < deadline:
            job = self.get(job_id)
            if job is None or job["status"] != "running":
                return job
            time.sleep(0.01)
        return self.get(job_id)
