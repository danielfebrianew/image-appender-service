import base64
import json
import subprocess
from pathlib import Path
from io import BytesIO
from typing import Any

import cv2
from fastapi import HTTPException, status
from PIL import Image, ImageOps

from app.config import Settings
from app.models import FitMode, VideoMeta


def run_ffprobe(settings: Settings, path: Path) -> VideoMeta:
    command = [
        settings.ffprobe_path,
        "-v",
        "error",
        "-select_streams",
        "v:0",
        "-show_entries",
        "stream=width,height,r_frame_rate,duration:format=duration",
        "-of",
        "json",
        str(path),
    ]
    try:
        result = subprocess.run(command, capture_output=True, check=True, text=True)
    except FileNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="ffprobe not found",
        ) from exc
    except subprocess.CalledProcessError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"could not inspect video: {exc.stderr.strip()}",
        ) from exc

    payload = json.loads(result.stdout)
    stream = payload["streams"][0]
    duration = float(stream.get("duration") or payload.get("format", {}).get("duration") or 0)
    fps = parse_fps(stream.get("r_frame_rate") or "0/1")
    return VideoMeta(
        path=str(path),
        duration_sec=duration,
        width=int(stream["width"]),
        height=int(stream["height"]),
        fps=fps,
    )


def parse_fps(value: str) -> float:
    if "/" not in value:
        return float(value)
    numerator, denominator = value.split("/", 1)
    den = float(denominator)
    return float(numerator) / den if den else 0.0


def inspect_image(path: Path) -> tuple[int, int]:
    try:
        with Image.open(path) as img:
            img.verify()
        with Image.open(path) as img:
            return img.size
    except Exception as exc:
        path.unlink(missing_ok=True)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="uploaded file is not a valid image",
        ) from exc


def save_base64_image(data_url: str, directory: Path, image_id: str) -> Path:
    if "," in data_url:
        header, encoded = data_url.split(",", 1)
        ext = ".png" if "png" in header.lower() else ".jpg"
    else:
        encoded = data_url
        ext = ".png"
    path = directory / f"{image_id}{ext}"
    try:
        path.write_bytes(base64.b64decode(encoded))
    except Exception as exc:
        raise HTTPException(status_code=400, detail="invalid base64 image") from exc
    return path


def extract_video_frame(video_path: Path, timestamp: float) -> tuple[bool, Any]:
    capture = cv2.VideoCapture(str(video_path))
    if not capture.isOpened():
        return False, None
    capture.set(cv2.CAP_PROP_POS_MSEC, max(timestamp, 0) * 1000)
    ok, frame = capture.read()
    capture.release()
    return ok, frame


def jpeg_bytes_from_frame(frame) -> bytes:
    ok, encoded = cv2.imencode(".jpg", frame, [int(cv2.IMWRITE_JPEG_QUALITY), 90])
    if not ok:
        raise HTTPException(status_code=500, detail="could not encode jpeg")
    return encoded.tobytes()


def make_preview(video_path: Path, image_path: Path, timestamp: float, fit: FitMode) -> bytes:
    ok, frame = extract_video_frame(video_path, timestamp)
    if not ok:
        raise HTTPException(status_code=400, detail="could not extract frame")

    height, width = frame.shape[:2]
    area_h = int(height * 0.30)
    y = height - area_h

    rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
    base = Image.fromarray(rgb)
    overlay = compose_overlay(image_path, (width, area_h), fit)
    base.paste(overlay, (0, y))
    return jpeg_bytes_from_pil(base)


def compose_overlay(image_path: Path, size: tuple[int, int], fit: FitMode) -> Image.Image:
    with Image.open(image_path) as img:
        img = img.convert("RGB")
        if fit == FitMode.contain:
            background = Image.new("RGB", size, "black")
            fitted = ImageOps.contain(img, size)
            x = (size[0] - fitted.width) // 2
            y = (size[1] - fitted.height) // 2
            background.paste(fitted, (x, y))
            return background
        return ImageOps.fit(img, size, method=Image.Resampling.LANCZOS, centering=(0.5, 0.5))


def jpeg_bytes_from_pil(image: Image.Image) -> bytes:
    buf = BytesIO()
    image.save(buf, format="JPEG", quality=90)
    return buf.getvalue()
