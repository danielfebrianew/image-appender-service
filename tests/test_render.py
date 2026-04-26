from __future__ import annotations

import asyncio
from pathlib import Path

from app import render
from app.jobs import JobManager
from app.models import ClickSound, FitMode, JobStatus, Layout, Project, Track
from app.render import (
    Segment,
    build_segments,
    concat_with_reencode,
    detect_codec,
    ffmpeg_time_escape,
    normalize_tracks,
    render_copy_segment,
    render_overlay_segment,
    run_render_job,
    validate_project_assets,
)
from app.storage import save_project
from conftest import sample_meta, seed_image, seed_project


def make_project(test_settings, tracks: list[Track] | None = None, duration: float = 10.0) -> Project:
    video_path = test_settings.upload_video_dir / "source.mp4"
    video_path.write_bytes(b"video")
    return Project(
        project_id="prj_1",
        name="Project",
        video_id="vid_1",
        video_meta=sample_meta(video_path, duration=duration),
        tracks=tracks or [],
    )


def test_ffmpeg_time_escape_trims_trailing_zeroes() -> None:
    assert ffmpeg_time_escape(1.23456) == "1.235"
    assert ffmpeg_time_escape(2.0) == "2"
    assert ffmpeg_time_escape(0.5) == "0.5"


def test_detect_codec_falls_back_from_videotoolbox_off_macos(monkeypatch, test_settings) -> None:
    test_settings.video_codec = "h264_videotoolbox"

    monkeypatch.setattr(render.platform, "system", lambda: "Linux")
    assert detect_codec(test_settings) == "libx264"

    monkeypatch.setattr(render.platform, "system", lambda: "Darwin")
    assert detect_codec(test_settings) == "h264_videotoolbox"


def test_normalize_tracks_sorts_truncates_and_skips_out_of_range_tracks(test_settings) -> None:
    project = make_project(
        test_settings,
        tracks=[
            Track(id="late", image_id="img_1", start_sec=11.0, end_sec=12.0),
            Track(id="truncated", image_id="img_1", start_sec=8.0, end_sec=12.0),
            Track(id="first", image_id="img_1", start_sec=1.0, end_sec=2.0),
        ],
        duration=10.0,
    )

    normalized = normalize_tracks(project)

    assert [track.id for track in normalized] == ["first", "truncated"]
    assert normalized[1].end_sec == 10.0


def test_build_segments_splits_copy_overlay_and_overlap_ranges() -> None:
    tracks = [
        Track(id="a", image_id="img_1", start_sec=1.0, end_sec=3.0),
        Track(id="b", image_id="img_2", start_sec=2.0, end_sec=4.0),
    ]

    segments = build_segments(tracks, duration=5.0)

    assert [(segment.start, segment.end, [track.id for track in segment.tracks]) for segment in segments] == [
        (0.0, 1.0, []),
        (1.0, 2.0, ["a"]),
        (2.0, 3.0, ["a", "b"]),
        (3.0, 4.0, ["b"]),
        (4.0, 5.0, []),
    ]


def test_render_copy_segment_delegates_expected_ffmpeg_args(monkeypatch, test_settings) -> None:
    captured = {}

    async def fake_run_ffmpeg(settings, args, manager, job_id, **kwargs):
        captured["args"] = args
        captured["kwargs"] = kwargs
        return 0

    monkeypatch.setattr(render, "run_ffmpeg", fake_run_ffmpeg)

    code = asyncio.run(
        render_copy_segment(
            test_settings,
            Path("source.mp4"),
            Segment(1.0, 3.5, []),
            Path("out.mp4"),
            JobManager(),
            "job_1",
            total_duration=10.0,
        )
    )

    assert code == 0
    assert captured["args"] == [
        "-ss",
        "1",
        "-t",
        "2.5",
        "-i",
        "source.mp4",
        "-map",
        "0",
        "-c",
        "copy",
        "-avoid_negative_ts",
        "make_zero",
        "out.mp4",
    ]
    assert captured["kwargs"] == {"weight": 0.25, "duration": 2.5}


def test_concat_with_reencode_builds_concat_filter(monkeypatch, test_settings) -> None:
    captured = {}

    async def fake_run_ffmpeg(settings, args, manager, job_id, **kwargs):
        captured["args"] = args
        captured["kwargs"] = kwargs
        return 0

    monkeypatch.setattr(render, "run_ffmpeg", fake_run_ffmpeg)

    code = asyncio.run(
        concat_with_reencode(
            test_settings,
            [Path("a.mp4"), Path("b.mp4")],
            Path("out.mp4"),
            JobManager(),
            "job_1",
            total_duration=12.0,
        )
    )

    assert code == 0
    assert captured["args"][:4] == ["-i", "a.mp4", "-i", "b.mp4"]
    assert any("concat=n=2:v=1:a=1[vout][aout]" in arg for arg in captured["args"])
    assert captured["kwargs"] == {"duration": 12.0}


def test_render_overlay_segment_builds_overlay_and_click_audio_filters(
    monkeypatch,
    test_settings,
) -> None:
    seed_image(test_settings, image_id="img_1")
    test_settings.click_default.write_bytes(b"click")
    captured = {}

    async def fake_run_ffmpeg(settings, args, manager, job_id, **kwargs):
        captured["args"] = args
        captured["kwargs"] = kwargs
        return 0

    monkeypatch.setattr(render, "run_ffmpeg", fake_run_ffmpeg)
    project = make_project(
        test_settings,
        tracks=[Track(id="trk_1", image_id="img_1", start_sec=1.0, end_sec=3.0)],
    ).model_copy(
        update={
            "layout": Layout(image_area_ratio=0.25, image_fit=FitMode.contain),
            "click_sound": ClickSound(enabled=True, volume=0.75),
        }
    )

    code = asyncio.run(
        render_overlay_segment(
            test_settings,
            Path("source.mp4"),
            Segment(1.0, 3.0, project.tracks),
            project,
            Path("out.mp4"),
            JobManager(),
            "job_1",
            total_duration=10.0,
        )
    )

    filter_complex = captured["args"][captured["args"].index("-filter_complex") + 1]

    assert code == 0
    assert str(test_settings.click_default) in captured["args"]
    assert "pad=640:90" in filter_complex
    assert "overlay=0:270" in filter_complex
    assert "amix=inputs=2" in filter_complex
    assert captured["args"][captured["args"].index("-map") + 1] == "[vout]"
    assert captured["kwargs"] == {"weight": 0.2, "duration": 2.0}


def test_validate_project_assets_logs_overlap_and_duration_warnings(test_settings) -> None:
    async def exercise() -> None:
        seed_image(test_settings, image_id="img_1")
        manager = JobManager()
        job = await manager.create("prj_1")
        project = make_project(
            test_settings,
            tracks=[
                Track(id="a", image_id="img_1", start_sec=0.0, end_sec=6.0),
                Track(id="b", image_id="img_1", start_sec=5.0, end_sec=12.0),
            ],
            duration=10.0,
        )

        await validate_project_assets(test_settings, project, manager, job.job_id)

        assert any("Track overlap detected" in line for line in job.logs)
        assert any("exceeds video duration" in line for line in job.logs)

    asyncio.run(exercise())


def test_run_render_job_no_tracks_marks_job_done_and_cleans_temp(monkeypatch, test_settings) -> None:
    async def exercise() -> None:
        project = seed_project(test_settings, project_id="prj_1")
        manager = JobManager()
        job = await manager.create(project.project_id)

        async def fake_run_ffmpeg(settings, args, manager, job_id, **kwargs):
            return 0

        monkeypatch.setattr(render, "run_ffmpeg", fake_run_ffmpeg)

        await run_render_job(test_settings, manager, job.job_id)

        assert job.status == JobStatus.done
        assert job.progress == 100.0
        assert job.output_url == f"/api/render/{job.job_id}/download"
        assert not (test_settings.tmp_dir / job.job_id).exists()

    asyncio.run(exercise())


def test_run_render_job_records_ffmpeg_failure(monkeypatch, test_settings) -> None:
    async def exercise() -> None:
        project = seed_project(test_settings, project_id="prj_1")
        manager = JobManager()
        job = await manager.create(project.project_id)

        async def fake_run_ffmpeg(settings, args, manager, job_id, **kwargs):
            return 1

        monkeypatch.setattr(render, "run_ffmpeg", fake_run_ffmpeg)

        await run_render_job(test_settings, manager, job.job_id)

        assert job.status == JobStatus.error
        assert job.error == "ffmpeg exited with code 1"

    asyncio.run(exercise())
