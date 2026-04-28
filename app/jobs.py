from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

from fastapi import WebSocket

from app.ids import new_id
from app.models import JobState, JobStatus

if TYPE_CHECKING:
    from app.models import Project


class JobManager:
    def __init__(self) -> None:
        self.jobs: dict[str, JobState] = {}
        self.websockets: dict[str, set[WebSocket]] = {}
        self.lock = asyncio.Lock()

    async def create(self, project: "Project") -> JobState:
        job = JobState(job_id=new_id("job"), project=project)
        async with self.lock:
            self.jobs[job.job_id] = job
        return job

    def get(self, job_id: str) -> JobState | None:
        return self.jobs.get(job_id)

    async def set_status(self, job_id: str, status: JobStatus) -> None:
        job = self.jobs[job_id]
        job.status = status
        if status == JobStatus.running:
            job.started_at = datetime.now(timezone.utc)
        if status in {JobStatus.done, JobStatus.error}:
            job.finished_at = datetime.now(timezone.utc)
            if job.started_at:
                job.duration_ms = int((job.finished_at - job.started_at).total_seconds() * 1000)

    async def log(self, job_id: str, line: str, level: str = "info") -> None:
        job = self.jobs[job_id]
        line = line.rstrip()
        if not line:
            return
        job.logs.append(line)
        job.logs = job.logs[-500:]
        await self.broadcast(job_id, {"type": "log", "line": line, "level": level})

    async def progress(self, job_id: str, percent: float, eta_sec: float | None = None) -> None:
        job = self.jobs[job_id]
        job.progress = max(0.0, min(100.0, percent))
        job.eta_sec = eta_sec
        await self.broadcast(
            job_id, {"type": "progress", "percent": job.progress, "eta_sec": eta_sec}
        )

    async def done(self, job_id: str, output_path: str, output_url: str) -> None:
        job = self.jobs[job_id]
        job.output_path = output_path
        job.output_url = output_url
        await self.progress(job_id, 100.0, 0)
        await self.set_status(job_id, JobStatus.done)
        await self.broadcast(
            job_id,
            {"type": "done", "output_url": output_url, "duration_ms": job.duration_ms or 0},
        )

    async def fail(self, job_id: str, message: str) -> None:
        job = self.jobs[job_id]
        job.error = message
        await self.set_status(job_id, JobStatus.error)
        await self.broadcast(
            job_id,
            {"type": "error", "message": message, "ffmpeg_tail": "\n".join(job.logs[-40:])},
        )

    async def attach(self, job_id: str, websocket: WebSocket) -> None:
        await websocket.accept()
        self.websockets.setdefault(job_id, set()).add(websocket)
        job = self.jobs.get(job_id)
        if job:
            await websocket.send_json({"type": "status", **job.public()})

    def detach(self, job_id: str, websocket: WebSocket) -> None:
        sockets = self.websockets.get(job_id)
        if not sockets:
            return
        sockets.discard(websocket)
        if not sockets:
            self.websockets.pop(job_id, None)

    async def broadcast(self, job_id: str, payload: dict[str, Any]) -> None:
        sockets = list(self.websockets.get(job_id, set()))
        for websocket in sockets:
            try:
                await websocket.send_json(payload)
            except Exception:
                self.detach(job_id, websocket)


job_manager = JobManager()

