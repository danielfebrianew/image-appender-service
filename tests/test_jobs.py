from __future__ import annotations

import asyncio

from app.jobs import JobManager
from app.models import JobStatus


class FakeWebSocket:
    def __init__(self) -> None:
        self.accepted = False
        self.sent = []

    async def accept(self) -> None:
        self.accepted = True

    async def send_json(self, payload) -> None:
        self.sent.append(payload)


class BrokenWebSocket:
    async def send_json(self, payload) -> None:
        raise RuntimeError("client disconnected")


def test_job_manager_lifecycle_progress_logs_and_broadcasts() -> None:
    async def exercise() -> None:
        manager = JobManager()
        websocket = FakeWebSocket()

        job = await manager.create("prj_1")
        await manager.attach(job.job_id, websocket)
        await manager.set_status(job.job_id, JobStatus.running)
        await manager.progress(job.job_id, 150.0, eta_sec=2.0)
        await manager.log(job.job_id, "hello")
        await manager.done(job.job_id, "/tmp/out.mp4", "/download")

        assert manager.get(job.job_id) is job
        assert websocket.accepted is True
        assert websocket.sent[0]["type"] == "status"
        assert websocket.sent[-1]["type"] == "done"
        assert job.progress == 100.0
        assert job.eta_sec == 0
        assert job.status == JobStatus.done
        assert job.output_url == "/download"
        assert job.duration_ms is not None

        manager.detach(job.job_id, websocket)
        assert job.job_id not in manager.websockets

    asyncio.run(exercise())


def test_job_manager_trims_logs_and_ignores_blank_lines() -> None:
    async def exercise() -> None:
        manager = JobManager()
        job = await manager.create("prj_1")

        await manager.log(job.job_id, "   ")
        for i in range(505):
            await manager.log(job.job_id, f"line {i}")

        assert len(job.logs) == 500
        assert job.logs[0] == "line 5"
        assert job.logs[-1] == "line 504"

    asyncio.run(exercise())


def test_job_manager_fail_sets_error_and_removes_broken_websockets() -> None:
    async def exercise() -> None:
        manager = JobManager()
        job = await manager.create("prj_1")
        manager.websockets[job.job_id] = {BrokenWebSocket()}

        await manager.fail(job.job_id, "render failed")

        assert job.status == JobStatus.error
        assert job.error == "render failed"
        assert job.job_id not in manager.websockets

    asyncio.run(exercise())
