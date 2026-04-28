from datetime import datetime, timezone
from pathlib import Path

from fastapi import (
    BackgroundTasks,
    Depends,
    FastAPI,
    File,
    Form,
    HTTPException,
    UploadFile,
    WebSocket,
    WebSocketDisconnect,
    status,
)
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, Response
from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.db import get_session, init_db
from app.ids import new_id
from app.jobs import job_manager
from app.media import (
    extract_video_frame,
    inspect_image,
    jpeg_bytes_from_frame,
    make_preview,
    run_ffprobe,
    save_base64_image,
)
from app.models import (
    AddTrackRequest,
    Cover,
    CoverRecord,
    CreateProjectRequest,
    ImageRecord,
    PreviewRequest,
    Project,
    RegisterVideoRequest,
    RenderRequest,
    Track,
    UpdateProjectRequest,
    VideoRecord,
)
from app.render import run_render_job
from app.storage import (
    copy_registered_file,
    delete_video_row,
    get_image,
    get_project,
    get_video,
    list_images,
    list_projects,
    list_videos,
    save_image,
    save_project,
    save_upload,
    save_video,
)

_settings = get_settings()
init_db(_settings.db_url)

app = FastAPI(title="ContextClipper Backend", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=_settings.cors_origin_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/api/videos", response_model=VideoRecord)
async def upload_video(
    file: UploadFile | None = File(default=None),
    path: str | None = Form(default=None),
    filename: str | None = Form(default=None),
    settings: Settings = Depends(get_settings),
    db: Session = Depends(get_session),
) -> VideoRecord:
    if file is None and path is None:
        raise HTTPException(status_code=400, detail="send multipart file or path form field")

    if file is not None:
        video_id, destination = await save_upload(file, settings.upload_video_dir, "vid")
        display_name = file.filename or destination.name
    else:
        request = RegisterVideoRequest(path=path or "", filename=filename)
        source = Path(request.path).expanduser()
        video_id, destination = copy_registered_file(
            source, settings.upload_video_dir, "vid", request.filename
        )
        display_name = request.filename or source.name

    meta = run_ffprobe(settings, destination)
    record = VideoRecord(
        video_id=video_id,
        filename=display_name,
        path=str(destination),
        meta=meta.model_copy(update={"path": str(destination)}),
    )
    save_video(db, record)
    return record


@app.get("/api/videos", response_model=list[VideoRecord])
def videos(db: Session = Depends(get_session)) -> list[VideoRecord]:
    return list_videos(db)


@app.get("/api/videos/{video_id}/stream")
def video_stream(video_id: str, db: Session = Depends(get_session)) -> FileResponse:
    video = get_video(db, video_id)
    return FileResponse(video.path, media_type="video/mp4")


@app.get("/api/videos/{video_id}/thumbnail")
def video_thumbnail(
    video_id: str, t: float = 2.5, db: Session = Depends(get_session)
) -> Response:
    video = get_video(db, video_id)
    ok, frame = extract_video_frame(Path(video.path), t)
    if not ok:
        raise HTTPException(status_code=400, detail="could not extract thumbnail")
    return Response(content=jpeg_bytes_from_frame(frame), media_type="image/jpeg")


@app.delete("/api/videos/{video_id}")
def delete_video(video_id: str, db: Session = Depends(get_session)) -> dict[str, str]:
    video = get_video(db, video_id)
    path = Path(video.path)
    if path.exists():
        path.unlink(missing_ok=True)
    delete_video_row(db, video_id)
    return {"status": "deleted"}


@app.post("/api/images", response_model=ImageRecord)
async def upload_image(
    file: UploadFile | None = File(default=None),
    base64_image: str | None = Form(default=None),
    settings: Settings = Depends(get_settings),
    db: Session = Depends(get_session),
) -> ImageRecord:
    if file is None and not base64_image:
        raise HTTPException(status_code=400, detail="send multipart file or base64_image form field")

    if file is not None:
        image_id, destination = await save_upload(file, settings.upload_image_dir, "img")
        display_name = file.filename or destination.name
    else:
        image_id = new_id("img")
        destination = save_base64_image(base64_image or "", settings.upload_image_dir, image_id)
        display_name = destination.name

    width, height = inspect_image(destination)
    record = ImageRecord(
        image_id=image_id,
        filename=display_name,
        path=str(destination),
        url=f"/api/images/{image_id}",
        width=width,
        height=height,
    )
    save_image(db, record)
    return record


@app.get("/api/images", response_model=list[ImageRecord])
def images(db: Session = Depends(get_session)) -> list[ImageRecord]:
    return list_images(db)


@app.get("/api/images/{image_id}")
def image_file(image_id: str, db: Session = Depends(get_session)) -> FileResponse:
    image = get_image(db, image_id)
    return FileResponse(image.path)


@app.delete("/api/images/{image_id}")
def delete_image(image_id: str, db: Session = Depends(get_session)) -> dict[str, str]:
    record = get_image(db, image_id, include_deleted=True)
    updated = record.model_copy(update={"deleted": True})
    save_image(db, updated)
    return {"status": "deleted"}


@app.post("/api/covers", response_model=CoverRecord)
async def upload_cover(
    file: UploadFile | None = File(default=None),
    base64_image: str | None = Form(default=None),
    settings: Settings = Depends(get_settings),
) -> CoverRecord:
    if file is None and not base64_image:
        raise HTTPException(status_code=400, detail="send multipart file or base64_image form field")

    if file is not None:
        cover_id, destination = await save_upload(file, settings.upload_cover_dir, "cov")
        display_name = file.filename or destination.name
    else:
        cover_id = new_id("cov")
        destination = save_base64_image(base64_image or "", settings.upload_cover_dir, cover_id)
        display_name = destination.name

    width, height = inspect_image(destination)
    return CoverRecord(
        cover_id=cover_id,
        filename=display_name,
        path=str(destination),
        url=f"/api/covers/{cover_id}",
        width=width,
        height=height,
    )


@app.get("/api/covers/{cover_id}")
def cover_file(cover_id: str, settings: Settings = Depends(get_settings)) -> FileResponse:
    candidates = list(Path(settings.upload_cover_dir).glob(f"{cover_id}.*"))
    if not candidates:
        raise HTTPException(status_code=404, detail="cover not found")
    return FileResponse(candidates[0])


@app.delete("/api/covers/{cover_id}")
def delete_cover(cover_id: str, settings: Settings = Depends(get_settings)) -> dict[str, str]:
    candidates = list(Path(settings.upload_cover_dir).glob(f"{cover_id}.*"))
    for f in candidates:
        f.unlink(missing_ok=True)
    return {"status": "deleted"}


@app.post("/api/projects", response_model=Project)
def create_project(
    request: CreateProjectRequest,
    db: Session = Depends(get_session),
) -> Project:
    if request.video_id:
        video = get_video(db, request.video_id)
        video_id = video.video_id
        video_meta = video.meta
    else:
        video_id = None
        video_meta = None
    project = Project(
        project_id=new_id("prj"),
        name=request.name,
        video_id=video_id,
        video_meta=video_meta,
    )
    save_project(db, project)
    return project


@app.get("/api/projects", response_model=list[Project])
def projects(db: Session = Depends(get_session)) -> list[Project]:
    return list_projects(db)


@app.get("/api/projects/{project_id}", response_model=Project)
def project(project_id: str, db: Session = Depends(get_session)) -> Project:
    return get_project(db, project_id)


@app.put("/api/projects/{project_id}", response_model=Project)
def update_project(
    project_id: str,
    request: UpdateProjectRequest,
    db: Session = Depends(get_session),
) -> Project:
    project = get_project(db, project_id)
    updates = request.model_dump(exclude_unset=True)
    update_values = {}

    if "name" in updates:
        update_values["name"] = request.name
    if "video_id" in updates and request.video_id:
        video = get_video(db, request.video_id)
        update_values["video_id"] = video.video_id
        update_values["video_meta"] = video.meta
    if "layout" in updates:
        update_values["layout"] = request.layout
    if "click_sound" in updates:
        update_values["click_sound"] = request.click_sound
    if "cover" in updates:
        update_values["cover"] = request.cover
    if "tracks" in updates and request.tracks is not None:
        effective_meta = update_values.get("video_meta", project.video_meta)
        new_duration = effective_meta.duration_sec if effective_meta else float("inf")
        for track in request.tracks or []:
            if track.video_id is not None:
                video_overlay = get_video(db, track.video_id)
                clip_duration = track.end_sec - track.start_sec
                if track.trim_start_sec + clip_duration > video_overlay.meta.duration_sec:
                    raise HTTPException(
                        status_code=422,
                        detail=f"track {track.id} trim_start_sec + clip duration exceeds overlay video duration",
                    )
            elif track.image_id is not None:
                get_image(db, track.image_id)
            if track.end_sec > new_duration:
                raise HTTPException(
                    status_code=422,
                    detail=f"track {track.id} end_sec exceeds video duration",
                )
        update_values["tracks"] = request.tracks

    updated = project.model_copy(update={**update_values, "updated_at": utc_now()})
    save_project(db, updated)
    return updated


@app.post("/api/projects/{project_id}/tracks", response_model=Project)
def add_track(
    project_id: str,
    request: AddTrackRequest,
    db: Session = Depends(get_session),
) -> Project:
    project = get_project(db, project_id)
    if request.video_id is not None:
        video_overlay = get_video(db, request.video_id)
        clip_duration = request.end_sec - request.start_sec
        if request.trim_start_sec + clip_duration > video_overlay.meta.duration_sec:
            raise HTTPException(
                status_code=422,
                detail="trim_start_sec + clip duration exceeds overlay video duration",
            )
    else:
        get_image(db, request.image_id)  # type: ignore[arg-type]
    if project.video_meta and request.end_sec > project.video_meta.duration_sec:
        raise HTTPException(status_code=422, detail="end_sec exceeds project video duration")
    track = Track(
        id=new_id("trk"),
        image_id=request.image_id,
        video_id=request.video_id,
        start_sec=request.start_sec,
        end_sec=request.end_sec,
        trim_start_sec=request.trim_start_sec,
        fit_override=request.fit_override,
    )
    updated = project.model_copy(
        update={"tracks": [*project.tracks, track], "updated_at": utc_now()}
    )
    save_project(db, updated)
    return updated


@app.post("/api/projects/{project_id}/cover", response_model=Project)
async def upload_project_cover(
    project_id: str,
    file: UploadFile | None = File(default=None),
    base64_image: str | None = Form(default=None),
    settings: Settings = Depends(get_settings),
    db: Session = Depends(get_session),
) -> Project:
    project = get_project(db, project_id)
    if file is None and not base64_image:
        raise HTTPException(status_code=400, detail="send multipart file or base64_image form field")

    if project.cover:
        old_path = Path(project.cover.path)
        if old_path.exists():
            old_path.unlink(missing_ok=True)

    if file is not None:
        cover_id, destination = await save_upload(file, settings.upload_cover_dir, "cov")
        display_name = file.filename or destination.name
    else:
        cover_id = new_id("cov")
        destination = save_base64_image(base64_image or "", settings.upload_cover_dir, cover_id)
        display_name = destination.name

    width, height = inspect_image(destination)
    cover = Cover(path=str(destination), filename=display_name, width=width, height=height)
    updated = project.model_copy(update={"cover": cover, "updated_at": utc_now()})
    save_project(db, updated)
    return updated


@app.delete("/api/projects/{project_id}/cover", response_model=Project)
def delete_project_cover(
    project_id: str, db: Session = Depends(get_session)
) -> Project:
    project = get_project(db, project_id)
    if project.cover:
        old_path = Path(project.cover.path)
        if old_path.exists():
            old_path.unlink(missing_ok=True)
    updated = project.model_copy(update={"cover": None, "updated_at": utc_now()})
    save_project(db, updated)
    return updated


@app.get("/api/projects/{project_id}/cover")
def project_cover_file(
    project_id: str, db: Session = Depends(get_session)
) -> FileResponse:
    project = get_project(db, project_id)
    if not project.cover:
        raise HTTPException(status_code=404, detail="cover not set")
    return FileResponse(project.cover.path)


@app.post("/api/preview")
def preview(request: PreviewRequest, db: Session = Depends(get_session)) -> Response:
    video = get_video(db, request.video_id)
    image = get_image(db, request.overlay.image_id)
    jpeg = make_preview(Path(video.path), Path(image.path), request.timestamp, request.overlay.fit)
    return Response(content=jpeg, media_type="image/jpeg")


@app.post("/api/render")
async def start_render(
    request: RenderRequest,
    background_tasks: BackgroundTasks,
    settings: Settings = Depends(get_settings),
    db: Session = Depends(get_session),
) -> dict[str, str]:
    video = get_video(db, request.video_id)

    cover: Cover | None = None
    if request.cover_id:
        candidates = list(Path(settings.upload_cover_dir).glob(f"{request.cover_id}.*"))
        if candidates:
            width, height = inspect_image(candidates[0])
            cover = Cover(path=str(candidates[0]), filename=candidates[0].name, width=width, height=height)

    project = Project(
        project_id=new_id("prj"),
        name="render",
        video_id=video.video_id,
        video_meta=video.meta,
        layout=request.layout,
        click_sound=request.click_sound,
        tracks=request.tracks,
        cover=cover,
    )
    job = await job_manager.create(project)
    background_tasks.add_task(run_render_job, settings, job_manager, job.job_id)
    return {"job_id": job.job_id, "status": job.status.value}


@app.get("/api/render/{job_id}")
def render_status(job_id: str) -> dict:
    job = job_manager.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="job not found")
    return job.public()


@app.get("/api/render/{job_id}/download")
def render_download(job_id: str) -> FileResponse:
    job = job_manager.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="job not found")
    if not job.output_path or not Path(job.output_path).exists():
        raise HTTPException(status_code=404, detail="output not ready")
    return FileResponse(job.output_path, media_type="video/mp4", filename=f"{job_id}.mp4")


@app.websocket("/ws/render/{job_id}")
async def render_ws(websocket: WebSocket, job_id: str) -> None:
    if not job_manager.get(job_id):
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
        return
    await job_manager.attach(job_id, websocket)
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        job_manager.detach(job_id, websocket)
