"""
Phase 4e — knowing its limits.

Per PLAN.md, confidently pricing a blurry photo, a non-truck, or a truck
with missing critical views is a losing outcome. This module gates the
pipeline before pricing runs, and defines the response tiers:

  "priced"          -> normal pricing output
  "not_a_truck"      -> subject isn't a real truck photo, refuse outright
  "needs_more_info"  -> fixable gap (missing/blurry view, low confidence);
                        names exactly what's needed, per the brief's own
                        "send me a shot of the tires" example
"""
import cv2
import numpy as np

# Matches the waypoint set defined in PLAN.md Phase 2a. This is the
# contract the frontend's guided-capture flow (Phase 2a) and the /predict
# API (Phase 5) both need to agree on.
REQUIRED_WAYPOINTS = ["front", "driver_side", "rear", "passenger_side", "tires", "interior"]

BLUR_VARIANCE_THRESHOLD = 100.0  # Laplacian variance below this = too blurry to trust
NOT_TRUCK_CONFIDENCE_THRESHOLD = 0.3  # extraction confidence below this counts as a "not a truck" vote
LOW_CONFIDENCE_THRESHOLD = 0.35  # fused-pipeline-wide floor before we refuse to price


def blur_variance(image_bytes: bytes) -> float:
    """Laplacian variance — low values mean a flat/blurry image."""
    arr = np.frombuffer(image_bytes, dtype=np.uint8)
    img = cv2.imdecode(arr, cv2.IMREAD_GRAYSCALE)
    if img is None or img.size == 0:
        return 0.0
    return float(cv2.Laplacian(img, cv2.CV_64F).var())


def is_blurry(image_bytes: bytes, threshold: float = BLUR_VARIANCE_THRESHOLD) -> bool:
    return blur_variance(image_bytes) < threshold


def evaluate(waypoint_extractions: dict[str, dict | None]) -> dict:
    """
    waypoint_extractions: {waypoint_name: extraction_dict | None}. A value
    of None means that waypoint's image was missing or too blurry to use
    (caller decides that upstream, e.g. via is_blurry()).

    Returns a gating decision dict with a "status" key:
      {"status": "priced"}
      {"status": "not_a_truck", "message": ...}
      {"status": "needs_more_info", "reason": ..., "message": ...}
    """
    usable = {wp: e for wp, e in waypoint_extractions.items() if e is not None}

    if not usable:
        return {
            "status": "needs_more_info",
            "reason": "no usable images",
            "message": "None of the provided photos were clear enough to assess. "
                       "Please retake in better lighting/focus.",
        }

    # Not-a-truck: majority of usable views agree the subject isn't a real
    # truck photo (wrong subject, or a game/CGI/toy render).
    not_truck_votes = sum(
        1 for e in usable.values()
        if not e.get("is_truck", True)
        or not e.get("is_real_photo", True)
        or e.get("confidence", 1.0) < NOT_TRUCK_CONFIDENCE_THRESHOLD
    )
    if not_truck_votes > len(usable) / 2:
        return {
            "status": "not_a_truck",
            "message": "This doesn't look like a real photo of a truck. "
                       "Please upload actual photos of the truck you'd like priced.",
        }

    # Missing critical views.
    missing = [wp for wp in REQUIRED_WAYPOINTS if wp not in usable]
    if missing:
        pretty = ", ".join(missing)
        return {
            "status": "needs_more_info",
            "reason": f"missing views: {pretty}",
            "message": f"Can't fully assess this truck yet — please add a clear photo of: {pretty}.",
        }

    # Overall low confidence across usable views, even though nothing is
    # individually flagged as not-a-truck or missing.
    avg_conf = sum(e.get("confidence", 0.0) for e in usable.values()) / len(usable)
    if avg_conf < LOW_CONFIDENCE_THRESHOLD:
        return {
            "status": "needs_more_info",
            "reason": "low confidence",
            "message": "The photos aren't clear enough to confidently identify this truck. "
                       "Try retaking in better lighting or from a clearer angle.",
        }

    return {"status": "priced"}
