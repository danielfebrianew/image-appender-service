# ContextClipper Backend

FastAPI backend untuk menerima video hasil AutoClipper, menyimpan project timeline, membuat preview, dan render overlay gambar di 30% bawah video dengan click sound otomatis.

## Setup

```bash
python -m venv image-appender-env
source image-appender-env/bin/activate
pip install -r requirements.txt
cp .env.example .env
uvicorn app.main:app --reload
```

API akan jalan di `http://127.0.0.1:8000`.

Pastikan `ffmpeg` dan `ffprobe` tersedia di PATH.

## Endpoint Utama

- `POST /api/videos` multipart `file=@clip.mp4`, atau form `path=/abs/path/video.mp4`
- `GET /api/videos`
- `GET /api/videos/{video_id}/thumbnail?t=2.5`
- `POST /api/images` multipart `file=@image.png`, atau form `base64_image=data:image/png;base64,...`
- `GET /api/images`
- `GET /api/images/{image_id}`
- `DELETE /api/images/{image_id}`
- `POST /api/projects`
- `GET /api/projects`
- `GET /api/projects/{project_id}`
- `PUT /api/projects/{project_id}`
- `POST /api/preview`
- `POST /api/render`
- `GET /api/render/{job_id}`
- `WS /ws/render/{job_id}`
- `GET /api/render/{job_id}/download`

## Postman

Import collection dari:

```text
postman/ContextClipper.postman_collection.json
```

Variable penting yang bisa diganti di Postman:

- `base_url`: default `http://127.0.0.1:8000`
- `ws_base_url`: default `ws://127.0.0.1:8000`
- `video_file_path`: path lokal video untuk upload/register
- `image_file_path`: path lokal gambar untuk upload
- `video_id`, `image_id`, `project_id`, `job_id`: otomatis terisi dari test script jika request dijalankan berurutan

## Quick Curl

```bash
curl -F "file=@/path/to/clip.mp4" http://127.0.0.1:8000/api/videos
curl -F "file=@/path/to/context.png" http://127.0.0.1:8000/api/images
curl -X POST http://127.0.0.1:8000/api/projects \
  -H "content-type: application/json" \
  -d '{"video_id":"vid_xxx","name":"Episode 31"}'
```

Update timeline:

```bash
curl -X PUT http://127.0.0.1:8000/api/projects/prj_xxx \
  -H "content-type: application/json" \
  -d '{
    "tracks": [
      {"id":"trk_001","image_id":"img_xxx","start_sec":0,"end_sec":4.5,"fit_override":null}
    ]
  }'
```

Render:

```bash
curl -X POST http://127.0.0.1:8000/api/render \
  -H "content-type: application/json" \
  -d '{"project_id":"prj_xxx"}'
curl http://127.0.0.1:8000/api/render/job_xxx
```

## Catatan MVP

- Storage masih filesystem JSON di `data/`.
- Job state masih in-memory, cocok untuk single user lokal.
- Jika `assets/click.mp3` belum ada, render tetap jalan dan click sound dilewati dengan warning.
- Jika host bukan macOS, codec otomatis fallback dari `h264_videotoolbox` ke `libx264`.
