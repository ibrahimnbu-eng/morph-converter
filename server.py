"""
MORPH — File Converter Backend
================================
Requirements:
    pip install fastapi uvicorn python-multipart Pillow

Run:
    python server.py
"""

import os
import uuid
import subprocess
import tempfile
import shutil
from pathlib import Path

from fastapi import FastAPI, File, UploadFile, Form, HTTPException
from fastapi.responses import FileResponse
from fastapi.middleware.cors import CORSMiddleware
from PIL import Image

app = FastAPI(title="MORPH Converter API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

TEMP_DIR = Path(tempfile.gettempdir()) / "morph_converter"
TEMP_DIR.mkdir(exist_ok=True)

VIDEO_FORMATS = {"mp4","avi","mov","mkv","webm","flv","wmv","3gp","m4v","ts","gif"}
AUDIO_FORMATS = {"mp3","wav","ogg","flac","aac","m4a","wma","opus","aiff"}
IMAGE_FORMATS = {"jpg","jpeg","png","gif","webp","bmp","tiff","avif"}

MIME_MAP = {
    "mp4":  "video/mp4",
    "avi":  "video/x-msvideo",
    "mov":  "video/quicktime",
    "mkv":  "video/x-matroska",
    "webm": "video/webm",
    "flv":  "video/x-flv",
    "wmv":  "video/x-ms-wmv",
    "3gp":  "video/3gpp",
    "m4v":  "video/x-m4v",
    "gif":  "image/gif",
    "mp3":  "audio/mpeg",
    "wav":  "audio/wav",
    "ogg":  "audio/ogg",
    "flac": "audio/flac",
    "aac":  "audio/aac",
    "m4a":  "audio/mp4",
    "opus": "audio/opus",
    "wma":  "audio/x-ms-wma",
    "aiff": "audio/aiff",
    "jpg":  "image/jpeg",
    "jpeg": "image/jpeg",
    "png":  "image/png",
    "webp": "image/webp",
    "bmp":  "image/bmp",
    "tiff": "image/tiff",
    "avif": "image/avif",
}

FFMPEG_PATHS = [
    "ffmpeg",
    "/usr/bin/ffmpeg",
    "/usr/local/bin/ffmpeg",
    "/nix/var/nix/profiles/default/bin/ffmpeg",
    "/run/current-system/sw/bin/ffmpeg",
]

def get_ffmpeg():
    for path in FFMPEG_PATHS:
        try:
            result = subprocess.run([path, "-version"], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            if result.returncode == 0:
                return path
        except Exception:
            continue
    return None

def get_file_type(ext):
    ext = ext.lower().lstrip(".")
    if ext in VIDEO_FORMATS: return "video"
    if ext in AUDIO_FORMATS: return "audio"
    if ext in IMAGE_FORMATS: return "image"
    return "unknown"

def run_ffmpeg(input_path, output_path, output_ext):
    ffmpeg = get_ffmpeg()
    if not ffmpeg:
        raise RuntimeError("FFmpeg not found on this server.")

    args = [ffmpeg, "-y", "-i", str(input_path)]

    if output_ext == "gif":
        args += ["-vf", "fps=10,scale=480:-1:flags=lanczos", "-loop", "0"]
    elif output_ext == "mp3":
        args += ["-q:a", "2"]
    elif output_ext == "ogg":
        args += ["-c:a", "libvorbis"]
    elif output_ext == "opus":
        args += ["-c:a", "libopus"]
    elif output_ext == "aiff":
        args += ["-c:a", "pcm_s16be"]
    elif output_ext in ("mp4", "m4v"):
        args += ["-c:v", "libx264", "-preset", "fast", "-crf", "23", "-c:a", "aac"]
    elif output_ext == "webm":
        args += ["-c:v", "libvpx-vp9", "-crf", "30", "-b:v", "0", "-c:a", "libopus"]

    args.append(str(output_path))

    result = subprocess.run(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=300)
    if result.returncode != 0:
        raise RuntimeError(result.stderr.decode(errors="replace")[-800:])

def convert_image_pillow(input_path, output_path, output_ext):
    img = Image.open(input_path)
    if output_ext in ("jpg", "jpeg"):
        if img.mode in ("RGBA", "P", "LA"):
            bg = Image.new("RGB", img.size, (255, 255, 255))
            bg.paste(img, mask=img.split()[-1] if img.mode == "RGBA" else None)
            img = bg
        else:
            img = img.convert("RGB")
        img.save(output_path, "JPEG", quality=92)
    elif output_ext == "png":
        img.save(output_path, "PNG")
    elif output_ext == "webp":
        img.save(output_path, "WEBP", quality=90)
    elif output_ext == "bmp":
        img.convert("RGB").save(output_path, "BMP")
    elif output_ext == "gif":
        img.save(output_path, "GIF")
    elif output_ext == "tiff":
        img.save(output_path, "TIFF")
    else:
        img.save(output_path)

@app.get("/")
def root():
    ffmpeg = get_ffmpeg()
    return {"status": "MORPH API running", "ffmpeg": ffmpeg or "not found"}

@app.get("/health")
def health():
    ffmpeg = get_ffmpeg()
    return {"ok": True, "ffmpeg_available": ffmpeg is not None, "ffmpeg_path": ffmpeg}

@app.post("/convert")
async def convert(
    file: UploadFile = File(...),
    output_format: str = Form(...),
):
    output_format = output_format.lower().lstrip(".")
    original_ext  = Path(file.filename).suffix.lower().lstrip(".")
    file_type     = get_file_type(original_ext)

    if file_type == "unknown":
        raise HTTPException(400, f"Unsupported input format: .{original_ext}")
    if output_format not in MIME_MAP:
        raise HTTPException(400, f"Unsupported output format: .{output_format}")

    job_id      = uuid.uuid4().hex
    input_path  = TEMP_DIR / f"{job_id}_in.{original_ext}"
    output_path = TEMP_DIR / f"{job_id}_out.{output_format}"

    try:
        with open(input_path, "wb") as f:
            content = await file.read()
            f.write(content)

        if file_type == "image":
            convert_image_pillow(input_path, output_path, output_format)
        else:
            run_ffmpeg(input_path, output_path, output_format)

        if not output_path.exists():
            raise HTTPException(500, "Conversion produced no output file.")

        stem          = Path(file.filename).stem
        download_name = f"{stem}.{output_format}"
        mime          = MIME_MAP.get(output_format, "application/octet-stream")

        return FileResponse(
            path=str(output_path),
            media_type=mime,
            filename=download_name,
        )

    except HTTPException:
        raise
    except subprocess.TimeoutExpired:
        raise HTTPException(504, "Conversion timed out.")
    except Exception as e:
        raise HTTPException(500, f"Conversion failed: {str(e)}")
    finally:
        try: input_path.unlink(missing_ok=True)
        except: pass

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("server:app", host="0.0.0.0", port=8000, reload=True)
