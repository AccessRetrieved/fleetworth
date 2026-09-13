"""
Phase 5 — Backend API.

POST /predict accepts one guided-capture submission (PLAN.md Phase 2a):
the auto-snapped exterior photos and the session video. Only the photos
go through the pricing pipeline; the video is stored as-is, purely as a
real-life/anti-forgery record (no processing of it in this build).
"""
import asyncio
import logging
import shutil
import time
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from threading import BoundedSemaphore

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from starlette.concurrency import run_in_threadpool
from starlette.responses import JSONResponse

from damage_visualization import RESULTS_DIR
from pipeline import run_pipeline

logger = logging.getLogger(__name__)
PREDICTION_SLOTS = BoundedSemaphore(2)
MAX_PHOTOS = 7
MAX_PHOTO_BYTES = 10 * 1024 * 1024
MAX_VIDEO_BYTES = 256 * 1024 * 1024
# Allow a full recording plus all seven photos at their individual limits.
MAX_SUBMISSION_BYTES = MAX_VIDEO_BYTES + MAX_PHOTOS * MAX_PHOTO_BYTES
MAX_BODY_BYTES = MAX_SUBMISSION_BYTES + 1024 * 1024
CHUNK_BYTES = 1024 * 1024
RETENTION_SECONDS = 24 * 60 * 60


def cleanup_expired_evidence():
    cutoff = time.time() - RETENTION_SECONDS
    for root in (UPLOAD_DIR, RESULTS_DIR):
        if not root.exists():
            continue
        for path in root.iterdir():
            try:
                uuid.UUID(path.name)
                if not path.is_symlink() and path.is_dir() and path.stat().st_mtime < cutoff:
                    shutil.rmtree(path)
            except (ValueError, FileNotFoundError):
                continue


@asynccontextmanager
async def lifespan(app):
    async def cleanup_loop():
        while True:
            try:
                await run_in_threadpool(cleanup_expired_evidence)
            except OSError:
                logger.exception("Evidence cleanup failed")
            await asyncio.sleep(3600)

    task = asyncio.create_task(cleanup_loop())
    try:
        yield
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


app = FastAPI(title="Fleetworth Backend", lifespan=lifespan)


class UploadBodyLimit:
    """Bound the actual request stream before multipart parsing/spooling."""
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or scope["path"] != "/predict":
            return await self.app(scope, receive, send)
        headers = dict(scope.get("headers", []))
        try:
            length = int(headers.get(b"content-length", b"0"))
        except ValueError:
            length = MAX_BODY_BYTES + 1
        if length > MAX_BODY_BYTES:
            return await JSONResponse({"detail": "Submission is too large."}, status_code=413)(scope, receive, send)
        received = 0

        async def bounded_receive():
            nonlocal received
            message = await receive()
            received += len(message.get("body", b""))
            if received > MAX_BODY_BYTES:
                raise HTTPException(status_code=413, detail="Submission is too large.")
            return message

        await self.app(scope, bounded_receive, send)


app.add_middleware(UploadBodyLimit)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# Serves the annotated damage-box photos Phase 2c already saves locally, so
# the frontend can show them instead of them only being a local debug aid.
RESULTS_DIR.mkdir(parents=True, exist_ok=True)
app.mount("/results", StaticFiles(directory=RESULTS_DIR), name="results")

UPLOAD_DIR = Path(__file__).resolve().parent / "uploads"
MIN_PHOTOS = 3

ALLOWED_IMAGE_TYPES = {"image/jpeg", "image/png", "image/webp"}
ALLOWED_VIDEO_TYPES = {"video/mp4", "video/webm", "video/quicktime"}


def _base_content_type(content_type: str | None) -> str:
    """Strip codec/profile parameters (e.g. "video/webm;codecs=vp9,opus" ->
    "video/webm") so browser-supplied MediaRecorder/Blob MIME types compare
    correctly against the allowed sets above."""
    return (content_type or "").split(";", 1)[0].strip().lower()


@app.post("/predict")
def predict(
    photos: list[UploadFile] = File(...),
    video: UploadFile | None = File(None),
):
    # A synchronous route runs all disk and pipeline work in Starlette's
    # thread pool. Reject excess work instead of queuing paid inference.
    if not PREDICTION_SLOTS.acquire(blocking=False):
        raise HTTPException(status_code=503, detail="The analysis service is busy. Please try again shortly.")
    submission_id = str(uuid.uuid4())
    submission_dir = UPLOAD_DIR / submission_id
    retain = False
    try:
        if not MIN_PHOTOS <= len(photos) <= MAX_PHOTOS:
            raise HTTPException(status_code=400, detail=f"Provide {MIN_PHOTOS}–{MAX_PHOTOS} exterior photos.")

        for photo in photos:
            if _base_content_type(photo.content_type) not in ALLOWED_IMAGE_TYPES:
                raise HTTPException(status_code=400, detail=f"Unsupported photo type: {photo.content_type}")
        if video and _base_content_type(video.content_type) not in ALLOWED_VIDEO_TYPES:
            raise HTTPException(status_code=400, detail=f"Unsupported video type: {video.content_type}")

        total = 0

        def chunks(upload, limit, label):
            nonlocal total
            size = 0
            while chunk := upload.file.read(CHUNK_BYTES):
                size += len(chunk)
                total += len(chunk)
                if size > limit:
                    raise HTTPException(status_code=413, detail=f"{label} exceeds the {limit / (1024 * 1024):g} MiB limit. "
                                        + ("Record a shorter session; your captured photos can be kept." if label == "Session recording" else "Choose a smaller image."))
                if total > MAX_SUBMISSION_BYTES:
                    raise HTTPException(status_code=413, detail=f"Photos and recording together exceed the {MAX_SUBMISSION_BYTES / (1024 * 1024):g} MiB submission limit.")
                yield chunk
            if not size:
                raise HTTPException(status_code=400, detail="Uploaded files must not be empty.")

        photo_bytes = [b"".join(chunks(photo, MAX_PHOTO_BYTES, f"Photo {i + 1}")) for i, photo in enumerate(photos)]
        if video:
            submission_dir.mkdir(parents=True, exist_ok=True)
            extension = {"video/mp4": ".mp4", "video/webm": ".webm", "video/quicktime": ".mov"}[_base_content_type(video.content_type)]
            with (submission_dir / f"video{extension}").open("wb") as output:
                for chunk in chunks(video, MAX_VIDEO_BYTES, "Session recording"):
                    output.write(chunk)
        result = run_pipeline(photo_bytes, submission_id=submission_id)
        if result.get("status") == "service_error":
            raise HTTPException(status_code=503, detail=result["message"])
        result["submission_id"] = submission_id
        retain = result.get("status") == "priced"
        return result
    except HTTPException:
        raise
    except Exception:
        logger.exception("Pipeline failed for %s", submission_id)
        raise HTTPException(status_code=502, detail="The photo analysis service could not complete your appraisal. Please try again shortly.")
    finally:
        PREDICTION_SLOTS.release()
        if not retain:
            shutil.rmtree(submission_dir, ignore_errors=True)
            shutil.rmtree(RESULTS_DIR / submission_id, ignore_errors=True)
        for upload in [*photos, *([video] if video else [])]:
            upload.file.close()


@app.get("/health")
def health():
    return {"status": "ok"}
