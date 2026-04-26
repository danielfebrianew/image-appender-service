import asyncio
import platform
import shlex
from dataclasses import dataclass
from pathlib import Path

from app.config import Settings
from app.jobs import JobManager
from app.models import FitMode, JobStatus, Project, Track
from app.storage import get_image, get_project, get_video


def ffmpeg_time_escape(value: float) -> str:
    return f"{value:.3f}".rstrip("0").rstrip(".")


def detect_codec(settings: Settings) -> str:
    if settings.video_codec == "h264_videotoolbox" and platform.system() != "Darwin":
        return "libx264"
    return settings.video_codec


def normalize_tracks(project: Project) -> list[Track]:
    normalized: list[Track] = []
    last_end = -1.0
    for track in sorted(project.tracks, key=lambda t: t.start_sec):
        current = track.model_copy(deep=True)
        if current.start_sec >= project.video_meta.duration_sec:
            continue
        if current.end_sec > project.video_meta.duration_sec:
            current.end_sec = project.video_meta.duration_sec
        if current.end_sec > current.start_sec:
            normalized.append(current)
        last_end = max(last_end, current.end_sec)
    return normalized


@dataclass
class Segment:
    start: float
    end: float
    tracks: list[Track]  # empty = copy segment, non-empty = overlay segment


def build_segments(tracks: list[Track], duration: float) -> list[Segment]:
    """Split timeline into copy and overlay segments."""
    if not tracks:
        return [Segment(0.0, duration, [])]

    events: list[float] = sorted({0.0, duration} | {t.start_sec for t in tracks} | {t.end_sec for t in tracks})
    segments: list[Segment] = []

    for i in range(len(events) - 1):
        seg_start = events[i]
        seg_end = events[i + 1]
        if seg_end - seg_start < 0.001:
            continue
        active = [t for t in tracks if t.start_sec <= seg_start and t.end_sec >= seg_end]
        segments.append(Segment(seg_start, seg_end, active))

    return segments


async def concat_with_stream_copy(
    settings: Settings, inputs: list[Path], out: Path, tmp_dir: Path,
    manager: JobManager, job_id: str, total_duration: float,
) -> int:
    concat_list = tmp_dir / "concat_copy.txt"
    concat_list.write_text("\n".join(f"file '{p.resolve()}'" for p in inputs))
    return await run_ffmpeg(settings, [
        "-f", "concat", "-safe", "0",
        "-i", str(concat_list),
        "-c", "copy",
        "-movflags", "+faststart",
        str(out),
    ], manager, job_id, duration=total_duration)


async def concat_with_reencode(
    settings: Settings, inputs: list[Path], out: Path,
    manager: JobManager, job_id: str, total_duration: float,
) -> int:
    codec = detect_codec(settings)
    args: list[str] = []
    for src in inputs:
        args.extend(["-i", str(src)])
    streams = "".join(f"[{i}:v:0][{i}:a:0]" for i in range(len(inputs)))
    filter_complex = f"{streams}concat=n={len(inputs)}:v=1:a=1[vout][aout]"
    args.extend([
        "-filter_complex", filter_complex,
        "-map", "[vout]",
        "-map", "[aout]",
        "-c:v", codec,
        "-pix_fmt", "yuv420p",
        "-c:a", "aac",
        "-ar", "48000",
        "-ac", "2",
        "-movflags", "+faststart",
        str(out),
    ])
    return await run_ffmpeg(settings, args, manager, job_id, duration=total_duration)


async def render_cover_segment(
    settings: Settings, project: Project, out: Path,
    manager: JobManager, job_id: str, total_duration: float,
) -> int:
    assert project.cover is not None
    width = project.video_meta.width
    height = project.video_meta.height
    fps = project.video_meta.fps or 30.0
    dur = project.cover.duration_sec
    codec = detect_codec(settings)
    bg = project.layout.background_color

    filters = [
        f"[0:v]scale={width}:{height}:force_original_aspect_ratio=decrease,"
        f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:{bg},setsar=1,fps={fps}[vout]"
    ]

    args = [
        "-loop", "1", "-t", ffmpeg_time_escape(dur), "-i", str(project.cover.path),
        "-f", "lavfi", "-t", ffmpeg_time_escape(dur),
        "-i", "anullsrc=channel_layout=stereo:sample_rate=48000",
        "-filter_complex", ";".join(filters),
        "-map", "[vout]",
        "-map", "1:a",
        "-c:v", codec,
        "-pix_fmt", "yuv420p",
        "-r", f"{fps}",
        "-c:a", "aac",
        "-ar", "48000",
        "-ac", "2",
        "-shortest",
        "-movflags", "+faststart",
        str(out),
    ]

    return await run_ffmpeg(settings, args, manager, job_id, weight=dur / total_duration, duration=dur)


async def run_ffmpeg(settings: Settings, args: list[str], manager: JobManager, job_id: str, weight: float = 1.0, duration: float = 0.0) -> int:
    cmd = [settings.ffmpeg_path, "-y", "-hide_banner", "-progress", "pipe:1", "-nostats"] + args
    await manager.log(job_id, shlex.join(cmd))
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    assert proc.stdout and proc.stderr

    async def read_stdout() -> None:
        while line := await proc.stdout.readline():  # type: ignore[union-attr]
            text = line.decode(errors="replace").strip()
            if text.startswith("out_time_us=") and duration > 0:
                raw = text.split("=", 1)[1]
                if raw.strip().lstrip("-").isdigit():
                    value = int(raw)
                    await manager.progress(job_id, min(99, (value / 1_000_000) / duration * 100 * weight))
            elif text.startswith("progress="):
                await manager.log(job_id, text)

    async def read_stderr() -> None:
        while line := await proc.stderr.readline():  # type: ignore[union-attr]
            await manager.log(job_id, line.decode(errors="replace").strip())

    await asyncio.gather(read_stdout(), read_stderr())
    return await proc.wait()


async def render_copy_segment(
    settings: Settings, source: Path, seg: Segment, out: Path,
    manager: JobManager, job_id: str, total_duration: float,
) -> int:
    dur = seg.end - seg.start
    return await run_ffmpeg(settings, [
        "-ss", ffmpeg_time_escape(seg.start),
        "-t", ffmpeg_time_escape(dur),
        "-i", str(source),
        "-map", "0",
        "-c", "copy",
        "-avoid_negative_ts", "make_zero",
        str(out),
    ], manager, job_id, weight=dur / total_duration, duration=dur)


async def render_overlay_segment(
    settings: Settings, source: Path, seg: Segment, project: Project,
    out: Path, manager: JobManager, job_id: str, total_duration: float,
) -> int:
    width = project.video_meta.width
    height = project.video_meta.height
    area_h = int(height * project.layout.image_area_ratio)
    y = height - area_h
    dur = seg.end - seg.start
    codec = detect_codec(settings)

    click_path = settings.click_default
    use_click = project.click_sound.enabled and click_path.exists()
    if project.click_sound.enabled and not click_path.exists():
        await manager.log(
            job_id,
            f"click_sound enabled but file not found at {click_path}; rendering without click.",
            "warn",
        )

    args = [
        "-ss", ffmpeg_time_escape(seg.start),
        "-t", ffmpeg_time_escape(dur),
        "-i", str(source),
    ]

    for track in seg.tracks:
        if track.video_id is not None:
            video_overlay = get_video(settings, track.video_id)
            args.extend([
                "-ss", ffmpeg_time_escape(seg.start),
                "-t", ffmpeg_time_escape(dur),
                "-i", str(video_overlay.path),
            ])
        else:
            image = get_image(settings, track.image_id)  # type: ignore[arg-type]
            args.extend(["-loop", "1", "-t", ffmpeg_time_escape(dur), "-i", str(image.path)])

    if use_click:
        args.extend(["-i", str(click_path)])

    filters: list[str] = []
    for idx, track in enumerate(seg.tracks, start=1):
        fit = track.fit_override or project.layout.image_fit
        if fit == FitMode.contain:
            filters.append(
                f"[{idx}:v]scale={width}:{area_h}:force_original_aspect_ratio=decrease,"
                f"pad={width}:{area_h}:(ow-iw)/2:(oh-ih)/2:{project.layout.background_color},"
                f"setsar=1[i{idx}]"
            )
        else:
            filters.append(
                f"[{idx}:v]scale={width}:{area_h}:force_original_aspect_ratio=increase,"
                f"crop={width}:{area_h},setsar=1[i{idx}]"
            )

    # All tracks active for the full segment duration — no enable= needed
    current = "[0:v]"
    for idx in range(1, len(seg.tracks) + 1):
        out_label = "[vout]" if idx == len(seg.tracks) else f"[v{idx}]"
        filters.append(f"{current}[i{idx}]overlay=0:{y}{out_label}")
        current = out_label

    audio_output = "0:a?"
    if use_click:
        click_input_index = len(seg.tracks) + 1
        # Only play click at the very start of the segment (t=0 relative)
        click_vol = project.click_sound.volume
        split_labels = "".join(f"[c{i}]" for i in range(len(seg.tracks)))
        filters.append(
            f"[{click_input_index}:a]aformat=sample_rates=48000:channel_layouts=stereo,"
            f"asplit={len(seg.tracks)}{split_labels}"
        )
        delayed = []
        for idx in range(len(seg.tracks)):
            filters.append(f"[c{idx}]atrim=start=0:end=0.1,volume={click_vol:.3f}[d{idx}]")
            delayed.append(f"[d{idx}]")
        filters.append("[0:a]aformat=sample_rates=48000:channel_layouts=stereo[orig]")
        filters.append(
            f"[orig]{''.join(delayed)}"
            f"amix=inputs={len(seg.tracks) + 1}:duration=first:dropout_transition=0[aout]"
        )
        audio_output = "[aout]"

    args += [
        "-filter_complex", ";".join(filters),
        "-map", "[vout]",
        "-map", audio_output,
        "-c:v", codec,
        "-pix_fmt", "yuv420p",
        "-c:a", "aac",
        "-ar", "48000",
        "-ac", "2",
        "-movflags", "+faststart",
        str(out),
    ]

    return await run_ffmpeg(settings, args, manager, job_id, weight=dur / total_duration, duration=dur)


async def run_render_job(settings: Settings, manager: JobManager, job_id: str) -> None:
    job = manager.get(job_id)
    if not job:
        return
    await manager.set_status(job_id, JobStatus.running)
    tmp_dir = settings.tmp_dir / job_id
    tmp_dir.mkdir(parents=True, exist_ok=True)

    try:
        project = get_project(settings, job.project_id)
        await validate_project_assets(settings, project, manager, job_id)

        source = Path(project.video_meta.path)
        duration = project.video_meta.duration_sec
        output = settings.output_dir / f"{job_id}.mp4"
        tracks = normalize_tracks(project)
        cover_path: Path | None = None
        if project.cover is not None:
            cover_path = tmp_dir / "cover.mp4"
            await manager.log(job_id, f"Rendering cover ({project.cover.duration_sec}s)...")
            code = await render_cover_segment(settings, project, cover_path, manager, job_id, duration)
            if code != 0:
                await manager.fail(job_id, f"cover render failed with code {code}")
                return

        if not tracks:
            if cover_path is None:
                await manager.log(job_id, "No tracks; copying stream directly.")
                code = await run_ffmpeg(settings, [
                    "-i", str(source), "-map", "0", "-c", "copy",
                    "-movflags", "+faststart", str(output),
                ], manager, job_id, duration=duration)
                if code != 0:
                    await manager.fail(job_id, f"ffmpeg exited with code {code}")
                    return
                await manager.done(job_id, str(output), f"/api/render/{job_id}/download")
                return
            await manager.log(job_id, "No tracks; concatenating cover + source (stream copy fast path).")
            code = await concat_with_stream_copy(
                settings, [cover_path, source], output, tmp_dir, manager, job_id, duration,
            )
            if code != 0:
                await manager.log(
                    job_id,
                    f"fast concat failed (code {code}); falling back to re-encode.",
                    "warn",
                )
                code = await concat_with_reencode(
                    settings, [cover_path, source], output, manager, job_id, duration,
                )
                if code != 0:
                    await manager.fail(job_id, f"concat failed with code {code}")
                    return
            await manager.progress(job_id, 100)
            await manager.done(job_id, str(output), f"/api/render/{job_id}/download")
            return

        segments = build_segments(tracks, duration)
        seg_paths: list[Path] = []

        await manager.log(job_id, f"Rendering {len(segments)} segment(s) ({sum(1 for s in segments if not s.tracks)} copy, {sum(1 for s in segments if s.tracks)} overlay).")

        for i, seg in enumerate(segments):
            seg_out = tmp_dir / f"seg_{i:04d}.mp4"
            seg_paths.append(seg_out)
            await manager.log(job_id, f"Segment {i+1}/{len(segments)}: {seg.start:.2f}s–{seg.end:.2f}s ({'overlay' if seg.tracks else 'copy'})")

            if seg.tracks:
                code = await render_overlay_segment(settings, source, seg, project, seg_out, manager, job_id, duration)
            else:
                code = await render_copy_segment(settings, source, seg, seg_out, manager, job_id, duration)

            if code != 0:
                await manager.fail(job_id, f"ffmpeg failed on segment {i} with code {code}")
                return

        if cover_path is not None:
            seg_paths.insert(0, cover_path)

        if len(seg_paths) == 1:
            seg_paths[0].rename(output)
        else:
            await manager.log(job_id, "Concatenating segments (stream copy fast path)...")
            code = await concat_with_stream_copy(
                settings, seg_paths, output, tmp_dir, manager, job_id, duration,
            )
            if code != 0 and cover_path is not None:
                await manager.log(
                    job_id,
                    f"fast concat failed (code {code}); falling back to re-encode.",
                    "warn",
                )
                code = await concat_with_reencode(
                    settings, seg_paths, output, manager, job_id, duration,
                )
            if code != 0:
                await manager.fail(job_id, f"concat failed with code {code}")
                return

        await manager.progress(job_id, 100)
        await manager.done(job_id, str(output), f"/api/render/{job_id}/download")

    except Exception as exc:
        await manager.fail(job_id, str(exc))
    finally:
        # Cleanup temp segments
        import shutil
        shutil.rmtree(tmp_dir, ignore_errors=True)


async def validate_project_assets(
    settings: Settings, project: Project, manager: JobManager, job_id: str
) -> None:
    seen_end = -1.0
    for track in project.tracks:
        if track.video_id is not None:
            get_video(settings, track.video_id)
        else:
            get_image(settings, track.image_id)  # type: ignore[arg-type]
        if track.start_sec < seen_end:
            await manager.log(job_id, "Track overlap detected; later tracks render above earlier tracks.", "warn")
        if track.end_sec > project.video_meta.duration_sec:
            await manager.log(job_id, f"Track {track.id} exceeds video duration; truncating.", "warn")
        seen_end = max(seen_end, track.end_sec)
