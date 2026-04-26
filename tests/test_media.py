from __future__ import annotations

import json
import subprocess
from pathlib import Path

import numpy as np
import pytest
from fastapi import HTTPException, status
from PIL import Image

from app import media
from app.media import (
    compose_overlay,
    extract_video_frame,
    inspect_image,
    jpeg_bytes_from_frame,
    jpeg_bytes_from_pil,
    make_preview,
    parse_fps,
    run_ffprobe,
    save_base64_image,
)
from app.models import FitMode
from conftest import make_png_file, png_data_url


def test_parse_fps_handles_plain_fractional_and_zero_denominator_values() -> None:
    assert parse_fps("30") == 30.0
    assert parse_fps("30000/1001") == pytest.approx(29.970, rel=1e-3)
    assert parse_fps("24/0") == 0.0


def test_run_ffprobe_builds_metadata_from_subprocess_json(monkeypatch, test_settings, tmp_path: Path) -> None:
    video_path = tmp_path / "clip.mp4"
    captured = {}

    class Result:
        stdout = json.dumps(
            {
                "streams": [{"width": 640, "height": 360, "r_frame_rate": "25/1"}],
                "format": {"duration": "7.5"},
            }
        )

    def fake_run(command, capture_output, check, text):
        captured["command"] = command
        assert capture_output is True
        assert check is True
        assert text is True
        return Result()

    monkeypatch.setattr(media.subprocess, "run", fake_run)

    meta = run_ffprobe(test_settings, video_path)

    assert captured["command"][0] == "ffprobe"
    assert meta.path == str(video_path)
    assert meta.duration_sec == 7.5
    assert meta.width == 640
    assert meta.height == 360
    assert meta.fps == 25.0


def test_run_ffprobe_maps_subprocess_errors_to_http_exceptions(monkeypatch, test_settings) -> None:
    monkeypatch.setattr(
        media.subprocess,
        "run",
        lambda *args, **kwargs: (_ for _ in ()).throw(FileNotFoundError()),
    )

    with pytest.raises(HTTPException) as missing:
        run_ffprobe(test_settings, Path("clip.mp4"))
    assert missing.value.status_code == status.HTTP_500_INTERNAL_SERVER_ERROR

    error = subprocess.CalledProcessError(1, ["ffprobe"], stderr="bad input")
    monkeypatch.setattr(
        media.subprocess,
        "run",
        lambda *args, **kwargs: (_ for _ in ()).throw(error),
    )

    with pytest.raises(HTTPException) as failed:
        run_ffprobe(test_settings, Path("clip.mp4"))
    assert failed.value.status_code == status.HTTP_400_BAD_REQUEST
    assert "bad input" in failed.value.detail


def test_inspect_image_returns_dimensions_and_deletes_invalid_upload(tmp_path: Path) -> None:
    valid = make_png_file(tmp_path / "valid.png", size=(9, 5))

    assert inspect_image(valid) == (9, 5)

    invalid = tmp_path / "invalid.png"
    invalid.write_text("not an image")

    with pytest.raises(HTTPException) as exc:
        inspect_image(invalid)

    assert exc.value.status_code == status.HTTP_400_BAD_REQUEST
    assert not invalid.exists()


def test_save_base64_image_accepts_data_urls_and_rejects_bad_input(test_settings) -> None:
    path = save_base64_image(png_data_url(size=(3, 3)), test_settings.upload_image_dir, "img_1")

    assert path.name == "img_1.png"
    assert Image.open(path).size == (3, 3)

    with pytest.raises(HTTPException) as exc:
        save_base64_image("abc", test_settings.upload_image_dir, "img_bad")
    assert exc.value.status_code == status.HTTP_400_BAD_REQUEST


def test_extract_video_frame_uses_non_negative_timestamp(monkeypatch) -> None:
    calls = []

    class FakeCapture:
        def __init__(self, path: str) -> None:
            calls.append(("path", path))

        def isOpened(self) -> bool:
            return True

        def set(self, prop, value) -> None:
            calls.append(("set", prop, value))

        def read(self):
            return True, "frame"

        def release(self) -> None:
            calls.append(("release",))

    monkeypatch.setattr(media.cv2, "VideoCapture", FakeCapture)

    ok, frame = extract_video_frame(Path("clip.mp4"), -4)

    assert ok is True
    assert frame == "frame"
    assert ("set", media.cv2.CAP_PROP_POS_MSEC, 0) in calls
    assert ("release",) in calls


def test_jpeg_bytes_helpers_return_jpeg_payload(tmp_path: Path) -> None:
    frame = np.zeros((4, 4, 3), dtype=np.uint8)

    assert jpeg_bytes_from_frame(frame).startswith(b"\xff\xd8")
    assert jpeg_bytes_from_pil(Image.new("RGB", (4, 4), "white")).startswith(b"\xff\xd8")


def test_compose_overlay_contains_or_covers_to_requested_size(tmp_path: Path) -> None:
    source = make_png_file(tmp_path / "overlay.png", size=(10, 2), color=(255, 0, 0))

    contained = compose_overlay(source, (8, 8), FitMode.contain)
    covered = compose_overlay(source, (8, 8), FitMode.cover)

    assert contained.size == (8, 8)
    assert covered.size == (8, 8)
    assert contained.getpixel((0, 0)) == (0, 0, 0)
    assert covered.getpixel((4, 4)) == (255, 0, 0)


def test_make_preview_pastes_overlay_on_extracted_frame(monkeypatch, tmp_path: Path) -> None:
    overlay = make_png_file(tmp_path / "overlay.png", size=(8, 4), color=(255, 0, 0))
    frame = np.zeros((10, 20, 3), dtype=np.uint8)

    monkeypatch.setattr(media, "extract_video_frame", lambda path, timestamp: (True, frame))

    payload = make_preview(Path("clip.mp4"), overlay, timestamp=2.0, fit=FitMode.cover)

    assert payload.startswith(b"\xff\xd8")
