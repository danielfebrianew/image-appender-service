import json
import shutil
from pathlib import Path
from collections.abc import Sequence
from typing import TypeVar

import aiofiles
from fastapi import HTTPException, UploadFile, status
from pydantic import BaseModel

from app.config import Settings
from app.ids import new_id
from app.models import ImageRecord, Project, VideoRecord

VIDEO_INDEX = "videos.json"
IMAGE_INDEX = "images.json"
T = TypeVar("T", bound=BaseModel)


def safe_suffix(filename: str | None, default: str = ".bin") -> str:
    if not filename:
        return default
    suffix = Path(filename).suffix.lower()
    return suffix or default


def read_json(path: Path, default):
    if not path.exists():
        return default
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def write_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    tmp.replace(path)


def load_records(settings: Settings, filename: str, model: type[T]) -> list[T]:
    return [model.model_validate(item) for item in read_json(settings.data_dir / filename, [])]


def save_records(settings: Settings, filename: str, records: Sequence[BaseModel]) -> None:
    write_json(settings.data_dir / filename, [record.model_dump(mode="json") for record in records])


def list_videos(settings: Settings) -> list[VideoRecord]:
    return load_records(settings, VIDEO_INDEX, VideoRecord)


def save_videos(settings: Settings, records: list[VideoRecord]) -> None:
    save_records(settings, VIDEO_INDEX, records)


def get_video(settings: Settings, video_id: str) -> VideoRecord:
    for record in list_videos(settings):
        if record.video_id == video_id:
            return record
    raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="video not found")


def list_images(settings: Settings, include_deleted: bool = False) -> list[ImageRecord]:
    records = load_records(settings, IMAGE_INDEX, ImageRecord)
    if include_deleted:
        return records
    return [record for record in records if not record.deleted]


def save_images(settings: Settings, records: list[ImageRecord]) -> None:
    save_records(settings, IMAGE_INDEX, records)


def get_image(settings: Settings, image_id: str, include_deleted: bool = False) -> ImageRecord:
    for record in list_images(settings, include_deleted=include_deleted):
        if record.image_id == image_id:
            return record
    raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="image not found")


def list_projects(settings: Settings) -> list[Project]:
    projects = []
    for path in sorted(settings.project_dir.glob("*.json")):
        projects.append(Project.model_validate(read_json(path, {})))
    return projects


def get_project(settings: Settings, project_id: str) -> Project:
    path = settings.project_dir / f"{project_id}.json"
    if not path.exists():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="project not found")
    return Project.model_validate(read_json(path, {}))


def save_project(settings: Settings, project: Project) -> None:
    write_json(settings.project_dir / f"{project.project_id}.json", project.model_dump(mode="json"))


async def save_upload(upload: UploadFile, directory: Path, prefix: str) -> tuple[str, Path]:
    item_id = new_id(prefix)
    suffix = safe_suffix(upload.filename)
    destination = directory / f"{item_id}{suffix}"
    async with aiofiles.open(destination, "wb") as out:
        while chunk := await upload.read(1024 * 1024):
            await out.write(chunk)
    return item_id, destination


def copy_registered_file(source: Path, directory: Path, prefix: str, filename: str | None) -> tuple[str, Path]:
    item_id = new_id(prefix)
    suffix = safe_suffix(filename or source.name)
    destination = directory / f"{item_id}{suffix}"
    shutil.copy2(source, destination)
    return item_id, destination
