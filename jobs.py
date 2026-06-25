"""In-memory job tracking for async datamosh runs."""

from __future__ import annotations

import threading
import uuid
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Dict, Optional

from mosh import MoshOptions, mosh_single, mosh_transition, mosh_two_clip
from util import cleanup_temp_files


def _blog(msg: str) -> None:
    print(f"[backend] {msg}")


class JobStatus(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETE = "complete"
    ERROR = "error"


ProgressCallback = Callable[[int, str], None]


@dataclass
class JobState:
    job_id: str
    status: JobStatus = JobStatus.QUEUED
    progress: int = 0
    stage: str = "Queued"
    error: Optional[str] = None
    result: Optional[Dict[str, Any]] = None


@dataclass
class JobStore:
    _jobs: Dict[str, JobState] = field(default_factory=dict)
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def create(self) -> JobState:
        job = JobState(job_id=uuid.uuid4().hex)
        with self._lock:
            self._jobs[job.job_id] = job
        return job

    def get(self, job_id: str) -> Optional[JobState]:
        with self._lock:
            return self._jobs.get(job_id)

    def update(self, job_id: str, *, progress: int, stage: str) -> None:
        with self._lock:
            job = self._jobs[job_id]
            job.progress = max(0, min(100, progress))
            job.stage = stage
            job.status = JobStatus.RUNNING

    def complete(self, job_id: str, result: Dict[str, Any]) -> None:
        with self._lock:
            job = self._jobs[job_id]
            job.status = JobStatus.COMPLETE
            job.progress = 100
            job.stage = "Complete"
            job.result = result

    def fail(self, job_id: str, message: str) -> None:
        with self._lock:
            job = self._jobs[job_id]
            job.status = JobStatus.ERROR
            job.error = message
            job.stage = "Failed"


JOB_STORE = JobStore()


def _make_progress(job_id: str) -> ProgressCallback:
    def report(percent: int, stage: str) -> None:
        _blog(f"job={job_id} progress={percent}% stage={stage}")
        JOB_STORE.update(job_id, progress=percent, stage=stage)

    return report


def run_job(
    job_id: str,
    *,
    mode: str,
    source_path: Path,
    output_path: Path,
    options: MoshOptions,
    reference_path: Optional[Path] = None,
) -> None:
    _blog(f"job={job_id} starting mode={mode} source={source_path.name}")
    progress = _make_progress(job_id)

    try:
        if mode == "two":
            if reference_path is None:
                raise ValueError("Reference video is required for two-clip mode.")
            result = mosh_two_clip(
                reference_path,
                source_path,
                output_path,
                options=options,
                on_progress=progress,
            )
            mode_label = "two-clip"
        elif mode == "transition":
            if reference_path is None:
                raise ValueError("Video 2 is required for transition mode.")
            result = mosh_transition(
                source_path,
                reference_path,
                output_path,
                options=options,
                on_progress=progress,
            )
            mode_label = "transition"
        else:
            result = mosh_single(
                source_path,
                output_path,
                options=options,
                on_progress=progress,
            )
            mode_label = "single-clip"
    except Exception as exc:  # noqa: BLE001 - surfaced to client as job error
        _blog(f"job={job_id} failed: {exc}")
        JOB_STORE.fail(job_id, str(exc))
        return
    finally:
        cleanup_temp_files(output_path)

    _blog(f"job={job_id} complete output={output_path.name}")
    JOB_STORE.complete(
        job_id,
        {
            "mode": mode_label,
            "filename": output_path.name,
            "output_url": f"/outputs/{output_path.name}",
            "idr_before": result.idr_before,
            "idr_after": result.idr_after,
            "idr_auto_kept": result.idr_auto_kept,
            "mosh_start_seconds": result.mosh_start_seconds,
            "mosh_start_vcl_index": result.mosh_start_vcl_index,
            "mosh_end_seconds": result.mosh_end_seconds,
            "mosh_end_vcl_index": result.mosh_end_vcl_index,
            "transition_duration_seconds": result.transition_duration_seconds,
            "transition_duration_vcl_count": result.transition_duration_vcl_count,
        },
    )


def start_job(
    *,
    mode: str,
    source_path: Path,
    output_path: Path,
    options: MoshOptions,
    reference_path: Optional[Path] = None,
) -> JobState:
    job = JOB_STORE.create()
    thread = threading.Thread(
        target=run_job,
        kwargs={
            "job_id": job.job_id,
            "mode": mode,
            "source_path": source_path,
            "output_path": output_path,
            "options": options,
            "reference_path": reference_path,
        },
        daemon=True,
    )
    thread.start()
    return job
