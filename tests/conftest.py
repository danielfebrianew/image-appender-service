from __future__ import annotations

import base64
from io import BytesIO
from pathlib import Path

import pytest
from PIL import Image

from app.config import Settings
from app.models import ImageRecord, Project, VideoMeta, VideoRecord
from app.storage import list_images, save_images, save_project, save_videos


@pytest.fixture
def test_settings(tmp_path: Path) -> Settings:
    settings = Settings(
        CONTEXTCLIPPER_DATA_DIR=tmp_path / "data",
        CONTEXTCLIPPER_FFMPEG_PATH="ffmpeg",
        CONTEXTCLIPPER_FFPROBE_PATH="ffprobe",
        CONTEXTCLIPPER_VIDEO_CODEC="libx264",
        CONTEXTCLIPPER_CLICK_DEFAULT=tmp_path / "click.mp3",
        CONTEXTCLIPPER_CORS_ORIGINS="http://localhost:3000, http://127.0.0.1:3000",
    )
    settings.ensure_dirs()
    return settings


def make_png_bytes(size: tuple[int, int] = (4, 2), color: tuple[int, int, int] = (10, 20, 30)) -> bytes:
    buf = BytesIO()
    Image.new("RGB", size, color).save(buf, format="PNG")
    return buf.getvalue()


def make_png_file(
    path: Path,
    size: tuple[int, int] = (4, 2),
    color: tuple[int, int, int] = (10, 20, 30),
) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(make_png_bytes(size=size, color=color))
    return path


def png_data_url(size: tuple[int, int] = (4, 2), color: tuple[int, int, int] = (10, 20, 30)) -> str:
    encoded = base64.b64encode(make_png_bytes(size=size, color=color)).decode("ascii")
    return f"data:image/png;base64,{encoded}"


def sample_meta(path: Path, duration: float = 10.0) -> VideoMeta:
    return VideoMeta(
        path=str(path),
        duration_sec=duration,
        width=640,
        height=360,
        fps=30.0,
    )


def seed_video(
    settings: Settings,
    video_id: str = "vid_test",
    filename: str = "clip.mp4",
    duration: float = 10.0,
) -> VideoRecord:
    path = settings.upload_video_dir / filename
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"not a real video")
    record = VideoRecord(
        video_id=video_id,
        filename=filename,
        path=str(path),
        meta=sample_meta(path, duration=duration),
    )
    save_videos(settings, [record])
    return record


def seed_image(
    settings: Settings,
    image_id: str = "img_test",
    filename: str = "context.png",
    deleted: bool = False,
    size: tuple[int, int] = (4, 2),
) -> ImageRecord:
    path = make_png_file(settings.upload_image_dir / filename, size=size)
    record = ImageRecord(
        image_id=image_id,
        filename=filename,
        path=str(path),
        url=f"/api/images/{image_id}",
        width=size[0],
        height=size[1],
        deleted=deleted,
    )
    records = list_images(settings, include_deleted=True)
    records.append(record)
    save_images(settings, records)
    return record


def seed_project(
    settings: Settings,
    project_id: str = "prj_test",
    video_id: str = "vid_test",
    duration: float = 10.0,
) -> Project:
    video_path = settings.upload_video_dir / "clip.mp4"
    video_path.parent.mkdir(parents=True, exist_ok=True)
    video_path.write_bytes(b"not a real video")
    project = Project(
        project_id=project_id,
        name="Test Project",
        video_id=video_id,
        video_meta=sample_meta(video_path, duration=duration),
    )
    save_project(settings, project)
    return project
