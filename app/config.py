from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    data_dir: Path = Field(default=Path("./data"), alias="CONTEXTCLIPPER_DATA_DIR")
    ffmpeg_path: str = Field(default="ffmpeg", alias="CONTEXTCLIPPER_FFMPEG_PATH")
    ffprobe_path: str = Field(default="ffprobe", alias="CONTEXTCLIPPER_FFPROBE_PATH")
    video_codec: str = Field(default="h264_videotoolbox", alias="CONTEXTCLIPPER_VIDEO_CODEC")
    max_render_concurrent: int = Field(default=1, alias="CONTEXTCLIPPER_MAX_RENDER_CONCURRENT")
    click_default: Path | None = Field(default=None, alias="CONTEXTCLIPPER_CLICK_DEFAULT")
    cors_origins: str = Field(
        default="http://localhost:3000", alias="CONTEXTCLIPPER_CORS_ORIGINS"
    )

    @property
    def click_asset(self) -> Path:
        if self.click_default is not None:
            return self.click_default
        return Path(__file__).parent / "assets" / "click.mp3"

    @property
    def upload_video_dir(self) -> Path:
        return self.data_dir / "uploads" / "videos"

    @property
    def upload_image_dir(self) -> Path:
        return self.data_dir / "uploads" / "images"

    @property
    def upload_cover_dir(self) -> Path:
        return self.data_dir / "uploads" / "covers"

    @property
    def project_dir(self) -> Path:
        return self.data_dir / "projects"

    @property
    def output_dir(self) -> Path:
        return self.data_dir / "outputs"

    @property
    def tmp_dir(self) -> Path:
        return self.data_dir / "tmp"

    @property
    def cors_origin_list(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]

    def ensure_dirs(self) -> None:
        for path in [
            self.upload_video_dir,
            self.upload_image_dir,
            self.upload_cover_dir,
            self.project_dir,
            self.output_dir,
            self.tmp_dir,
        ]:
            path.mkdir(parents=True, exist_ok=True)


@lru_cache
def get_settings() -> Settings:
    settings = Settings()
    settings.ensure_dirs()
    return settings

