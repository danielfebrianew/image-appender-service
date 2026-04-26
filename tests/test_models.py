from pathlib import Path

import pytest
from pydantic import ValidationError

from app.models import (
    JobState,
    Layout,
    RegisterVideoRequest,
    Track,
)


def test_layout_validates_hex_background_color() -> None:
    assert Layout(background_color="#ABC123").background_color == "#ABC123"

    with pytest.raises(ValidationError):
        Layout(background_color="ABC123")

    with pytest.raises(ValidationError):
        Layout(background_color="#GGGGGG")


def test_layout_validates_image_area_ratio_bounds() -> None:
    with pytest.raises(ValidationError):
        Layout(image_area_ratio=0.01)

    with pytest.raises(ValidationError):
        Layout(image_area_ratio=0.95)


def test_track_requires_positive_ordered_time_range() -> None:
    track = Track(id="trk_1", image_id="img_1", start_sec=1.5, end_sec=2.5)

    assert track.start_sec == 1.5
    assert track.end_sec == 2.5

    with pytest.raises(ValidationError):
        Track(id="trk_2", image_id="img_1", start_sec=2.5, end_sec=2.5)

    with pytest.raises(ValidationError):
        Track(id="trk_3", image_id="img_1", start_sec=-1, end_sec=2.5)


def test_register_video_request_requires_existing_path(tmp_path: Path) -> None:
    source = tmp_path / "clip.mp4"
    source.write_bytes(b"video")

    assert RegisterVideoRequest(path=str(source)).path == str(source)

    with pytest.raises(ValidationError):
        RegisterVideoRequest(path=str(tmp_path / "missing.mp4"))


def test_job_state_public_exposes_tail_log_without_full_logs() -> None:
    job = JobState(job_id="job_1", project_id="prj_1", logs=[f"line {i}" for i in range(100)])

    public = job.public()

    assert "logs" not in public
    assert public["tail_log"] == [f"line {i}" for i in range(20, 100)]
