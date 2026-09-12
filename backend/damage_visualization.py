"""
Phase 2c — damage bounding-box visualization.

Draws a box (OpenCV) around each localized damage entry on its own
source photo and saves annotated copies locally to
results/<submission_id>/. This is a demo/debug aid only — a missing,
null, or inaccurate box never affects pricing (Phase 4 only uses the
damage description list and count).

Boxes are per-source-photo coordinates and are drawn on that same photo
only — never merged or reprojected across views (see PLAN.md Phase 3).
"""
from pathlib import Path

import cv2
import numpy as np

RESULTS_DIR = Path(__file__).resolve().parent / "results"


def _draw_boxes(image_bytes: bytes, damage: list[dict]) -> bytes | None:
    """Returns annotated JPEG bytes, or None if there's nothing to draw
    (no damage entries with a usable box) or the image can't be decoded."""
    boxed = [d for d in damage if isinstance(d, dict) and d.get("box")]
    if not boxed:
        return None

    arr = np.frombuffer(image_bytes, dtype=np.uint8)
    img = cv2.imdecode(arr, cv2.IMREAD_COLOR)
    if img is None:
        return None

    h, w = img.shape[:2]
    thickness = max(2, w // 300)
    for d in boxed:
        try:
            x_min, y_min, x_max, y_max = d["box"]
        except (TypeError, ValueError):
            continue
        pt1 = (int(x_min * w), int(y_min * h))
        pt2 = (int(x_max * w), int(y_max * h))
        cv2.rectangle(img, pt1, pt2, (0, 0, 255), thickness)

        label = d.get("description", "damage")
        label_pos = (pt1[0], max(pt1[1] - 8, 15))
        cv2.putText(img, label, label_pos, cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 0, 255), 2, cv2.LINE_AA)

    ok, buf = cv2.imencode(".jpg", img)
    return buf.tobytes() if ok else None


def save_annotated_photos(
    submission_id: str, photos: list, extractions: list[dict | None]
) -> list[Path]:
    """photos and extractions are the raw per-photo lists from
    pipeline.run_pipeline (same order/length — extractions[i] is None for
    a photo dropped as blurry/unusable). Saves one annotated JPEG per
    photo that has at least one localized damage entry. Returns the paths
    actually written (empty list if nothing had a drawable box)."""
    saved = []
    for i, (photo, extraction) in enumerate(zip(photos, extractions)):
        if extraction is None or not isinstance(photo, bytes):
            continue  # skip unusable photos and non-bytes inputs (e.g. test URLs)
        damage = extraction.get("visible_damage") or []
        annotated = _draw_boxes(photo, damage)
        if annotated is None:
            continue

        submission_dir = RESULTS_DIR / submission_id
        submission_dir.mkdir(parents=True, exist_ok=True)
        path = submission_dir / f"photo_{i}_annotated.jpg"
        path.write_bytes(annotated)
        saved.append(path)
    return saved
