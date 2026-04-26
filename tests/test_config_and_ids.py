from pathlib import Path
import re

from app import config
from app.config import Settings
from app.ids import new_id


def test_settings_computed_paths_and_cors_list(test_settings: Settings) -> None:
    assert test_settings.upload_video_dir == test_settings.data_dir / "uploads" / "videos"
    assert test_settings.upload_image_dir == test_settings.data_dir / "uploads" / "images"
    assert test_settings.upload_cover_dir == test_settings.data_dir / "uploads" / "covers"
    assert test_settings.project_dir == test_settings.data_dir / "projects"
    assert test_settings.output_dir == test_settings.data_dir / "outputs"
    assert test_settings.tmp_dir == test_settings.data_dir / "tmp"
    assert test_settings.cors_origin_list == [
        "http://localhost:3000",
        "http://127.0.0.1:3000",
    ]

    for path in [
        test_settings.upload_video_dir,
        test_settings.upload_image_dir,
        test_settings.upload_cover_dir,
        test_settings.project_dir,
        test_settings.output_dir,
        test_settings.tmp_dir,
    ]:
        assert path.exists()


def test_get_settings_is_cached_and_creates_directories(monkeypatch, tmp_path: Path) -> None:
    config.get_settings.cache_clear()
    monkeypatch.setenv("CONTEXTCLIPPER_DATA_DIR", str(tmp_path / "cached-data"))

    first = config.get_settings()
    second = config.get_settings()

    assert first is second
    assert first.output_dir.exists()
    config.get_settings.cache_clear()


def test_new_id_uses_prefix_and_12_hex_chars() -> None:
    identifier = new_id("img")

    assert re.fullmatch(r"img_[0-9a-f]{12}", identifier)
