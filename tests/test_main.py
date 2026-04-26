from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from fastapi import BackgroundTasks, HTTPException, WebSocketDisconnect, status

from app import main
from app.jobs import JobManager
from app.models import (
    CreateProjectRequest,
    JobState,
    PreviewOverlay,
    PreviewRequest,
    RenderRequest,
    Track,
    UpdateProjectRequest,
    VideoMeta,
)
from app.storage import get_image, get_project, list_images, list_videos, save_project
from conftest import png_data_url, sample_meta, seed_image, seed_project, seed_video


def test_health() -> None:
    assert main.health() == {"status": "ok"}


def test_upload_video_requires_file_or_path(test_settings) -> None:
    with pytest.raises(HTTPException) as exc:
        asyncio.run(main.upload_video(file=None, path=None, filename=None, settings=test_settings))

    assert exc.value.status_code == status.HTTP_400_BAD_REQUEST


def test_upload_video_registers_existing_path_and_saves_metadata(
    monkeypatch,
    test_settings,
    tmp_path: Path,
) -> None:
    source = tmp_path / "source.mp4"
    source.write_bytes(b"video")

    def fake_ffprobe(settings, path):
        return VideoMeta(path=str(path), duration_sec=4.0, width=320, height=180, fps=24.0)

    monkeypatch.setattr(main, "run_ffprobe", fake_ffprobe)

    record = asyncio.run(
        main.upload_video(
            file=None,
            path=str(source),
            filename="renamed.MOV",
            settings=test_settings,
        )
    )

    assert record.video_id.startswith("vid_")
    assert record.filename == "renamed.MOV"
    assert Path(record.path).suffix == ".mov"
    assert record.meta.path == record.path
    assert list_videos(test_settings) == [record]


def test_video_thumbnail_returns_jpeg_or_400(monkeypatch, test_settings) -> None:
    video = seed_video(test_settings)

    monkeypatch.setattr(main, "extract_video_frame", lambda path, timestamp: (True, "frame"))
    monkeypatch.setattr(main, "jpeg_bytes_from_frame", lambda frame: b"jpeg")

    response = main.video_thumbnail(video.video_id, t=1.5, settings=test_settings)

    assert response.media_type == "image/jpeg"
    assert response.body == b"jpeg"

    monkeypatch.setattr(main, "extract_video_frame", lambda path, timestamp: (False, None))

    with pytest.raises(HTTPException) as exc:
        main.video_thumbnail(video.video_id, settings=test_settings)
    assert exc.value.status_code == status.HTTP_400_BAD_REQUEST


def test_upload_image_accepts_base64_lists_and_soft_deletes(test_settings) -> None:
    with pytest.raises(HTTPException) as exc:
        asyncio.run(main.upload_image(file=None, base64_image=None, settings=test_settings))
    assert exc.value.status_code == status.HTTP_400_BAD_REQUEST

    record = asyncio.run(
        main.upload_image(file=None, base64_image=png_data_url(size=(5, 4)), settings=test_settings)
    )

    assert record.image_id.startswith("img_")
    assert record.width == 5
    assert record.height == 4
    assert main.images(test_settings) == [record]

    assert main.delete_image(record.image_id, settings=test_settings) == {"status": "deleted"}
    assert list_images(test_settings) == []
    assert get_image(test_settings, record.image_id, include_deleted=True).deleted is True


def test_delete_image_returns_404_for_missing_image(test_settings) -> None:
    with pytest.raises(HTTPException) as exc:
        main.delete_image("missing", settings=test_settings)

    assert exc.value.status_code == status.HTTP_404_NOT_FOUND


def test_create_and_update_project_validate_video_images_and_duration(test_settings) -> None:
    video = seed_video(test_settings, duration=8.0)
    image = seed_image(test_settings)

    project = main.create_project(
        CreateProjectRequest(video_id=video.video_id, name="Episode"),
        settings=test_settings,
    )

    assert project.video_id == video.video_id
    assert get_project(test_settings, project.project_id) == project

    updated = main.update_project(
        project.project_id,
        UpdateProjectRequest(
            name="Renamed",
            tracks=[
                Track(
                    id="trk_1",
                    image_id=image.image_id,
                    start_sec=0.0,
                    end_sec=4.0,
                )
            ],
        ),
        settings=test_settings,
    )

    assert updated.name == "Renamed"
    assert updated.tracks[0].image_id == image.image_id

    with pytest.raises(HTTPException) as exc:
        main.update_project(
            project.project_id,
            UpdateProjectRequest(
                tracks=[
                    Track(
                        id="too_long",
                        image_id=image.image_id,
                        start_sec=0.0,
                        end_sec=9.0,
                    )
                ]
            ),
            settings=test_settings,
        )
    assert exc.value.status_code == 422


def test_project_cover_upload_get_and_delete(test_settings) -> None:
    project = seed_project(test_settings)

    with pytest.raises(HTTPException) as exc:
        asyncio.run(
            main.upload_project_cover(
                project.project_id,
                file=None,
                base64_image=None,
                settings=test_settings,
            )
        )
    assert exc.value.status_code == status.HTTP_400_BAD_REQUEST

    updated = asyncio.run(
        main.upload_project_cover(
            project.project_id,
            file=None,
            base64_image=png_data_url(size=(6, 3)),
            settings=test_settings,
        )
    )

    assert updated.cover is not None
    cover_path = Path(updated.cover.path)
    assert cover_path.exists()
    assert main.project_cover_file(project.project_id, settings=test_settings).path == str(cover_path)

    without_cover = main.delete_project_cover(project.project_id, settings=test_settings)

    assert without_cover.cover is None
    assert not cover_path.exists()

    with pytest.raises(HTTPException) as missing:
        main.project_cover_file(project.project_id, settings=test_settings)
    assert missing.value.status_code == status.HTTP_404_NOT_FOUND


def test_preview_uses_stored_video_and_image(monkeypatch, test_settings) -> None:
    video = seed_video(test_settings)
    image = seed_image(test_settings)
    captured = {}

    def fake_preview(video_path, image_path, timestamp, fit):
        captured["video_path"] = video_path
        captured["image_path"] = image_path
        captured["timestamp"] = timestamp
        captured["fit"] = fit
        return b"jpeg"

    monkeypatch.setattr(main, "make_preview", fake_preview)

    response = main.preview(
        PreviewRequest(
            video_id=video.video_id,
            timestamp=1.25,
            overlay=PreviewOverlay(image_id=image.image_id),
        ),
        settings=test_settings,
    )

    assert response.media_type == "image/jpeg"
    assert response.body == b"jpeg"
    assert captured["video_path"] == Path(video.path)
    assert captured["image_path"] == Path(image.path)


def test_start_render_schedules_background_task(monkeypatch, test_settings) -> None:
    project = seed_project(test_settings)
    background_tasks = BackgroundTasks()

    class FakeJobManager:
        async def create(self, project_id: str) -> JobState:
            return JobState(job_id="job_test", project_id=project_id)

    fake_manager = FakeJobManager()
    monkeypatch.setattr(main, "job_manager", fake_manager)

    result = asyncio.run(
        main.start_render(
            RenderRequest(project_id=project.project_id),
            background_tasks,
            settings=test_settings,
        )
    )

    assert result == {"job_id": "job_test", "status": "queued"}
    assert len(background_tasks.tasks) == 1


def test_render_status_and_download(monkeypatch, test_settings) -> None:
    output = test_settings.output_dir / "job_test.mp4"
    job = JobState(job_id="job_test", project_id="prj_1", logs=["a", "b"])

    class FakeJobManager:
        def get(self, job_id: str):
            return job if job_id == "job_test" else None

    monkeypatch.setattr(main, "job_manager", FakeJobManager())

    assert main.render_status("job_test")["tail_log"] == ["a", "b"]

    with pytest.raises(HTTPException) as not_ready:
        main.render_download("job_test")
    assert not_ready.value.status_code == status.HTTP_404_NOT_FOUND

    job.output_path = str(output)
    output.write_bytes(b"video")

    response = main.render_download("job_test")

    assert response.path == str(output)
    assert response.filename == "job_test.mp4"

    with pytest.raises(HTTPException) as missing:
        main.render_status("missing")
    assert missing.value.status_code == status.HTTP_404_NOT_FOUND


def test_render_ws_closes_unknown_job(monkeypatch) -> None:
    class FakeJobManager:
        def get(self, job_id: str):
            return None

    class FakeWebSocket:
        def __init__(self) -> None:
            self.closed_code = None

        async def close(self, code: int) -> None:
            self.closed_code = code

    websocket = FakeWebSocket()
    monkeypatch.setattr(main, "job_manager", FakeJobManager())

    asyncio.run(main.render_ws(websocket, "missing"))

    assert websocket.closed_code == status.WS_1008_POLICY_VIOLATION


def test_render_ws_detaches_on_disconnect(monkeypatch) -> None:
    class FakeJobManager:
        def __init__(self) -> None:
            self.detached = False

        def get(self, job_id: str):
            return object()

        async def attach(self, job_id: str, websocket) -> None:
            self.attached = (job_id, websocket)

        def detach(self, job_id: str, websocket) -> None:
            self.detached = True

    class FakeWebSocket:
        async def receive_text(self) -> str:
            raise WebSocketDisconnect()

    fake_manager = FakeJobManager()
    websocket = FakeWebSocket()
    monkeypatch.setattr(main, "job_manager", fake_manager)

    asyncio.run(main.render_ws(websocket, "job_1"))

    assert fake_manager.attached == ("job_1", websocket)
    assert fake_manager.detached is True


def test_endpoint_inventory_contains_documented_routes() -> None:
    paths = {route.path for route in main.app.routes}

    assert "/health" in paths
    assert "/api/videos" in paths
    assert "/api/images" in paths
    assert "/api/projects" in paths
    assert "/api/preview" in paths
    assert "/api/render" in paths
