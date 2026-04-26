from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from fastapi import HTTPException, status

from app.models import ImageRecord, Project, VideoRecord
from app.storage import (
    copy_registered_file,
    get_image,
    get_project,
    get_video,
    list_images,
    list_projects,
    list_videos,
    read_json,
    safe_suffix,
    save_images,
    save_project,
    save_upload,
    save_videos,
    write_json,
)
from conftest import sample_meta, seed_image, seed_project, seed_video


class ChunkedUpload:
    filename = "payload.TXT"

    def __init__(self, data: bytes) -> None:
        self.data = data
        self.offset = 0

    async def read(self, size: int) -> bytes:
        if self.offset >= len(self.data):
            return b""
        chunk = self.data[self.offset : self.offset + size]
        self.offset += size
        return chunk


def test_safe_suffix_normalizes_or_defaults() -> None:
    assert safe_suffix("Photo.PNG") == ".png"
    assert safe_suffix("archive") == ".bin"
    assert safe_suffix(None, default=".dat") == ".dat"


def test_read_json_returns_default_and_write_json_creates_parent(tmp_path: Path) -> None:
    path = tmp_path / "nested" / "payload.json"

    assert read_json(path, {"empty": True}) == {"empty": True}

    write_json(path, {"name": "ContextClipper", "items": [1, 2]})

    assert read_json(path, {}) == {"name": "ContextClipper", "items": [1, 2]}
    assert not path.with_suffix(".json.tmp").exists()


def test_video_record_crud(test_settings) -> None:
    video = seed_video(test_settings, video_id="vid_1")

    assert list_videos(test_settings) == [video]
    assert get_video(test_settings, "vid_1") == video

    with pytest.raises(HTTPException) as exc:
        get_video(test_settings, "missing")
    assert exc.value.status_code == status.HTTP_404_NOT_FOUND


def test_image_record_filters_soft_deleted_items(test_settings) -> None:
    active = seed_image(test_settings, image_id="img_active")
    deleted = seed_image(test_settings, image_id="img_deleted", deleted=True)

    assert list_images(test_settings) == [active]
    assert list_images(test_settings, include_deleted=True) == [active, deleted]
    assert get_image(test_settings, "img_active") == active

    with pytest.raises(HTTPException) as exc:
        get_image(test_settings, "img_deleted")
    assert exc.value.status_code == status.HTTP_404_NOT_FOUND

    assert get_image(test_settings, "img_deleted", include_deleted=True) == deleted


def test_project_crud(test_settings) -> None:
    first = seed_project(test_settings, project_id="prj_b")
    second = Project(
        project_id="prj_a",
        name="Earlier",
        video_id="vid_1",
        video_meta=sample_meta(test_settings.upload_video_dir / "clip.mp4"),
    )
    save_project(test_settings, second)

    assert list_projects(test_settings) == [second, first]
    assert get_project(test_settings, "prj_a") == second

    with pytest.raises(HTTPException) as exc:
        get_project(test_settings, "missing")
    assert exc.value.status_code == status.HTTP_404_NOT_FOUND


def test_save_records_round_trip_models(test_settings) -> None:
    video = VideoRecord(
        video_id="vid_round",
        filename="clip.mp4",
        path="clip.mp4",
        meta=sample_meta(Path("clip.mp4")),
    )
    image = ImageRecord(
        image_id="img_round",
        filename="image.png",
        path="image.png",
        url="/api/images/img_round",
        width=1,
        height=1,
    )

    save_videos(test_settings, [video])
    save_images(test_settings, [image])

    assert list_videos(test_settings) == [video]
    assert list_images(test_settings) == [image]


def test_save_upload_streams_to_destination(test_settings) -> None:
    upload = ChunkedUpload(b"hello world")

    item_id, destination = asyncio.run(save_upload(upload, test_settings.upload_image_dir, "img"))

    assert item_id.startswith("img_")
    assert destination.suffix == ".txt"
    assert destination.read_bytes() == b"hello world"


def test_copy_registered_file_uses_requested_filename_suffix(test_settings, tmp_path: Path) -> None:
    source = tmp_path / "source.mp4"
    source.write_bytes(b"video")

    item_id, destination = copy_registered_file(
        source,
        test_settings.upload_video_dir,
        "vid",
        filename="renamed.MOV",
    )

    assert item_id.startswith("vid_")
    assert destination.suffix == ".mov"
    assert destination.read_bytes() == b"video"
