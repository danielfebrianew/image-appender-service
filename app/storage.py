import shutil
from pathlib import Path

import aiofiles
from fastapi import HTTPException, UploadFile, status
from sqlalchemy.orm import Session

from app.ids import new_id
from app.models import ImageRecord, Project, VideoRecord
from app.orm import ImageRow, ProjectRow, VideoRow


def safe_suffix(filename: str | None, default: str = ".bin") -> str:
    if not filename:
        return default
    suffix = Path(filename).suffix.lower()
    return suffix or default


# ── Images ────────────────────────────────────────────────────────────────────

def list_images(db: Session, include_deleted: bool = False) -> list[ImageRecord]:
    query = db.query(ImageRow)
    if not include_deleted:
        query = query.filter(ImageRow.deleted == False)  # noqa: E712
    return [_image_from_row(row) for row in query.order_by(ImageRow.created_at).all()]


def get_image(db: Session, image_id: str, include_deleted: bool = False) -> ImageRecord:
    query = db.query(ImageRow).filter(ImageRow.image_id == image_id)
    if not include_deleted:
        query = query.filter(ImageRow.deleted == False)  # noqa: E712
    row = query.first()
    if not row:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="image not found")
    return _image_from_row(row)


def save_image(db: Session, record: ImageRecord) -> None:
    row = db.get(ImageRow, record.image_id)
    if row is None:
        row = ImageRow(
            image_id=record.image_id,
            filename=record.filename,
            path=record.path,
            url=record.url,
            width=record.width,
            height=record.height,
            deleted=record.deleted,
            created_at=record.created_at,
        )
        db.add(row)
    else:
        row.filename = record.filename
        row.path = record.path
        row.url = record.url
        row.width = record.width
        row.height = record.height
        row.deleted = record.deleted
    db.commit()


def _image_from_row(row: ImageRow) -> ImageRecord:
    return ImageRecord(
        image_id=row.image_id,
        filename=row.filename,
        path=row.path,
        url=row.url,
        width=row.width,
        height=row.height,
        deleted=row.deleted,
        created_at=row.created_at,
    )


# ── Videos ────────────────────────────────────────────────────────────────────

def list_videos(db: Session) -> list[VideoRecord]:
    return [_video_from_row(row) for row in db.query(VideoRow).order_by(VideoRow.created_at).all()]


def get_video(db: Session, video_id: str) -> VideoRecord:
    row = db.get(VideoRow, video_id)
    if not row:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="video not found")
    return _video_from_row(row)


def save_video(db: Session, record: VideoRecord) -> None:
    row = db.get(VideoRow, record.video_id)
    if row is None:
        row = VideoRow(
            video_id=record.video_id,
            filename=record.filename,
            path=record.path,
            meta=record.meta.model_dump(mode="json"),
            created_at=record.created_at,
        )
        db.add(row)
    else:
        row.filename = record.filename
        row.path = record.path
        row.meta = record.meta.model_dump(mode="json")
    db.commit()


def delete_video_row(db: Session, video_id: str) -> None:
    row = db.get(VideoRow, video_id)
    if row:
        db.delete(row)
        db.commit()


def _video_from_row(row: VideoRow) -> VideoRecord:
    return VideoRecord.model_validate(
        {"video_id": row.video_id, "filename": row.filename, "path": row.path, "meta": row.meta, "created_at": row.created_at}
    )


# ── Projects ──────────────────────────────────────────────────────────────────

def list_projects(db: Session) -> list[Project]:
    return [_project_from_row(row) for row in db.query(ProjectRow).order_by(ProjectRow.created_at).all()]


def get_project(db: Session, project_id: str) -> Project:
    row = db.get(ProjectRow, project_id)
    if not row:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="project not found")
    return _project_from_row(row)


def save_project(db: Session, project: Project) -> None:
    row = db.get(ProjectRow, project.project_id)
    data = project.model_dump(mode="json")
    if row is None:
        row = ProjectRow(
            project_id=data["project_id"],
            name=data["name"],
            video_id=data["video_id"],
            video_meta=data["video_meta"],
            layout=data["layout"],
            click_sound=data["click_sound"],
            tracks=data["tracks"],
            cover=data.get("cover"),
            created_at=project.created_at,
            updated_at=project.updated_at,
        )
        db.add(row)
    else:
        row.name = data["name"]
        row.video_id = data["video_id"]
        row.video_meta = data["video_meta"]
        row.layout = data["layout"]
        row.click_sound = data["click_sound"]
        row.tracks = data["tracks"]
        row.cover = data.get("cover")
        row.updated_at = project.updated_at
    db.commit()


def _project_from_row(row: ProjectRow) -> Project:
    return Project.model_validate(
        {
            "project_id": row.project_id,
            "name": row.name,
            "video_id": row.video_id,
            "video_meta": row.video_meta,
            "layout": row.layout,
            "click_sound": row.click_sound,
            "tracks": row.tracks,
            "cover": row.cover,
            "created_at": row.created_at,
            "updated_at": row.updated_at,
        }
    )


# ── File helpers ──────────────────────────────────────────────────────────────

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
