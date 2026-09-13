"""
Phase 5 — Backend API.

POST /predict accepts one guided-capture submission (PLAN.md Phase 2a):
the auto-snapped exterior photos and the session video. Only the photos
go through the pricing pipeline; the video is stored as-is, purely as a
real-life/anti-forgery record (no processing of it in this build).
"""
import uuid
from pathlib import Path

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from damage_visualization import RESULTS_DIR
from pipeline import run_pipeline

app = FastAPI(title="Fleetworth Backend")

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
MIN_PHOTOS = 1  # pipeline.limits.MIN_USABLE_PHOTOS gates the real coverage requirement;
                # this is just the API-layer floor so we don't call the pipeline on nothing

ALLOWED_IMAGE_TYPES = {"image/jpeg", "image/png", "image/webp"}
ALLOWED_VIDEO_TYPES = {"video/mp4", "video/webm", "video/quicktime"}


def _base_content_type(content_type: str | None) -> str:
    """Strip codec/profile parameters (e.g. "video/webm;codecs=vp9,opus" ->
    "video/webm") so browser-supplied MediaRecorder/Blob MIME types compare
    correctly against the allowed sets above."""
    return (content_type or "").split(";", 1)[0].strip().lower()


@app.post("/predict")
async def predict(
    photos: list[UploadFile] = File(...),
    video: UploadFile = File(...),
):
    if len(photos) < MIN_PHOTOS:
        raise HTTPException(status_code=400, detail="At least one photo is required")

    for photo in photos:
        if _base_content_type(photo.content_type) not in ALLOWED_IMAGE_TYPES:
            raise HTTPException(
                status_code=400,
                detail=f"Unsupported photo type: {photo.content_type}",
            )
    if _base_content_type(video.content_type) not in ALLOWED_VIDEO_TYPES:
        raise HTTPException(status_code=400, detail=f"Unsupported video type: {video.content_type}")

    photo_bytes = [await p.read() for p in photos]

    # Store the video as-is (authenticity record — never processed here).
    submission_id = str(uuid.uuid4())
    submission_dir = UPLOAD_DIR / submission_id
    submission_dir.mkdir(parents=True, exist_ok=True)
    video_ext = Path(video.filename or "session.mp4").suffix or ".mp4"
    video_path = submission_dir / f"video{video_ext}"
    video_path.write_bytes(await video.read())

    try:
        result = run_pipeline(photo_bytes, submission_id=submission_id)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Pipeline error: {e}")

    result["submission_id"] = submission_id
    return result


@app.get("/health")
def health():
    return {"status": "ok"}
