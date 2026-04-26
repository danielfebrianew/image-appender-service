from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator, model_validator


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class FitMode(str, Enum):
    cover = "cover"
    contain = "contain"


class VideoMeta(BaseModel):
    path: str
    duration_sec: float
    width: int
    height: int
    fps: float


class VideoRecord(BaseModel):
    video_id: str
    filename: str
    path: str
    meta: VideoMeta
    created_at: datetime = Field(default_factory=utc_now)


class ImageRecord(BaseModel):
    image_id: str
    filename: str
    path: str
    url: str
    width: int
    height: int
    deleted: bool = False
    created_at: datetime = Field(default_factory=utc_now)


class Layout(BaseModel):
    image_area_ratio: float = Field(default=0.30, ge=0.05, le=0.90)
    image_position: Literal["bottom"] = "bottom"
    image_fit: FitMode = FitMode.cover
    background_color: str = "#000000"

    @field_validator("background_color")
    @classmethod
    def validate_hex_color(cls, value: str) -> str:
        if len(value) != 7 or not value.startswith("#"):
            raise ValueError("background_color must be #RRGGBB")
        int(value[1:], 16)
        return value


class ClickSound(BaseModel):
    enabled: bool = True
    asset: str = "click_default"
    volume: float = Field(default=0.6, ge=0.0, le=2.0)
    trigger: Literal["on_image_start"] = "on_image_start"


class Track(BaseModel):
    id: str
    image_id: str | None = None
    video_id: str | None = None
    start_sec: float = Field(ge=0)
    end_sec: float = Field(gt=0)
    fit_override: FitMode | None = None

    @model_validator(mode="after")
    def validate_track(self) -> "Track":
        if self.end_sec <= self.start_sec:
            raise ValueError("end_sec must be greater than start_sec")
        if self.image_id is None and self.video_id is None:
            raise ValueError("track must have either image_id or video_id")
        if self.image_id is not None and self.video_id is not None:
            raise ValueError("track cannot have both image_id and video_id")
        return self


class Cover(BaseModel):
    path: str
    filename: str
    width: int
    height: int
    duration_sec: float = 0.5


class Project(BaseModel):
    project_id: str
    name: str
    video_id: str
    video_meta: VideoMeta
    layout: Layout = Field(default_factory=Layout)
    click_sound: ClickSound = Field(default_factory=ClickSound)
    tracks: list[Track] = Field(default_factory=list)
    cover: Cover | None = None
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)


class CreateProjectRequest(BaseModel):
    video_id: str
    name: str


class UpdateProjectRequest(BaseModel):
    name: str | None = None
    video_id: str | None = None
    layout: Layout | None = None
    click_sound: ClickSound | None = None
    tracks: list[Track] | None = None
    cover: Cover | None = None


class AddTrackRequest(BaseModel):
    image_id: str | None = None
    video_id: str | None = None
    start_sec: float = Field(ge=0)
    end_sec: float = Field(gt=0)
    fit_override: FitMode | None = None

    @model_validator(mode="after")
    def validate_source(self) -> "AddTrackRequest":
        if self.image_id is None and self.video_id is None:
            raise ValueError("must provide either image_id or video_id")
        if self.image_id is not None and self.video_id is not None:
            raise ValueError("cannot provide both image_id and video_id")
        if self.end_sec <= self.start_sec:
            raise ValueError("end_sec must be greater than start_sec")
        return self


class RegisterVideoRequest(BaseModel):
    path: str
    filename: str | None = None

    @field_validator("path")
    @classmethod
    def path_must_exist(cls, value: str) -> str:
        if not Path(value).expanduser().exists():
            raise ValueError("path does not exist")
        return value


class PreviewOverlay(BaseModel):
    image_id: str
    fit: FitMode = FitMode.cover


class PreviewRequest(BaseModel):
    video_id: str
    timestamp: float = Field(ge=0)
    overlay: PreviewOverlay


class RenderRequest(BaseModel):
    project_id: str


class JobStatus(str, Enum):
    queued = "queued"
    running = "running"
    done = "done"
    error = "error"


class JobState(BaseModel):
    job_id: str
    project_id: str
    status: JobStatus = JobStatus.queued
    progress: float = 0.0
    eta_sec: float | None = None
    output_path: str | None = None
    output_url: str | None = None
    error: str | None = None
    logs: list[str] = Field(default_factory=list)
    started_at: datetime | None = None
    finished_at: datetime | None = None
    duration_ms: int | None = None

    def public(self) -> dict[str, Any]:
        data = self.model_dump(mode="json")
        data["tail_log"] = self.logs[-80:]
        data.pop("logs", None)
        return data

